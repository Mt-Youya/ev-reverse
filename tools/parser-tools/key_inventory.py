"""Inventory every key we hold, grouped by lesson, so a tool knows what it can decrypt offline.

Keys reach this project three ways -- derived from `tk` the API signed, harvested from the live
player, or recorded while tracing the key derivation -- and they live in several files. This reads
them all, reports how many distinct files each lesson covers, and writes one merged library that the
general-purpose decryptor can load. Having this in one place is what makes "decrypt any lesson" a
question about the key library rather than about which script wrote which file.

    python key_inventory.py [--write captured/keys_merged.json]
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURED = os.path.join(HERE, "captured")
SOURCES = ["kdf_today.jsonl", "kdf_round3.jsonl", "kdf_inputs.jsonl", "kdf_inputs_round2.jsonl",
           "plaintext_keys.jsonl", "player_keys.jsonl", os.path.join("pairing", "events.jsonl")]


def looks_like_key(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def key_for(tk, name):
    """The project's key derivation: MD5_hex(tk + filename + "20220507"), used as 32 ASCII bytes."""
    return hashlib.md5((tk + name + "20220507").encode()).hexdigest()


def harvest_file(path):
    """Pull (filename, key) pairs out of whichever shape this file uses."""
    out = {}
    try:
        handle = open(path, encoding="utf-8")
    except OSError:
        return out
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        name = record.get("file") or record.get("name") or record.get("filename")
        if not name:
            continue
        # Three shapes live in these files: a finished key, a `md5` field that *is* the key, and a
        # `tk` plus the string it was hashed with. `tk` is itself 32 hex characters, so it cannot be
        # told apart from a key by shape -- only by which field it sits in, which is why each is read
        # by name rather than sniffed.
        for field in ("key", "md5"):
            value = record.get(field)
            if looks_like_key(value) and name.endswith(".ts"):
                out[name] = value
                break
        else:
            tk = record.get("tk")
            extra = record.get("extra") or "20220507"
            if isinstance(tk, str) and tk:
                out[name] = key_for(tk, name) if extra == "20220507" \
                    else hashlib.md5((tk + name + extra).encode()).hexdigest()
    return out


def harvest_lesson_lists(roots):
    """Mine every captured API response that carries a `k_l` list of signed segments.

    This is the cheapest keys there are. When the player opens a lesson the server answers with the
    whole lesson: one entry per segment, each with its signed URL and its `tk`. `tk` is a long-lived
    per-file value -- unlike the URLs, which expire -- so a response captured once keeps yielding keys
    for every segment of that lesson without another request, another token, or another minute of
    playback. The inflated bodies the API capture leaves behind are exactly those responses.

    The *sessions* matter as much as the keys. Each response belongs to one playback session, and a
    lesson's segments differ between sessions -- same content, first PTS a few seconds apart -- so
    merging segments from two sessions produces a video that is longer than the lesson. The session a
    segment came from is therefore kept alongside its key, and the returned mapping is
    `segment -> (key, session)`.
    """
    out = {}
    sessions = {}
    indexed = {}
    entries_by_session = {}
    for root in roots:
        for directory, _, files in os.walk(root):
            for name in files:
                if not name.endswith(".json"):
                    continue
                path = os.path.join(directory, name)
                try:
                    if os.path.getsize(path) > 64 * 1024 * 1024:
                        continue
                    document = json.load(open(path, encoding="utf-8"))
                except Exception:
                    continue
                entries = document.get("k_l") if isinstance(document, dict) else None
                if not isinstance(entries, list):
                    continue
                session = os.path.relpath(path, os.path.join(HERE, "..", ".."))
                collected = []
                entries_kept = []
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    tk = item.get("tk")
                    signed = item.get("sf") or ""
                    segment = str(signed).split("?")[0].rsplit("/", 1)[-1]
                    if tk and segment.endswith(".ts"):
                        out[segment] = (key_for(tk, segment), session)
                        collected.append(segment)
                        # The list index is the content position. Sessions disagree about a segment's
                        # first PTS but agree about where it belongs in the lesson, so the index is
                        # what lets segments from different sessions be assembled without gaps or
                        # duplicates -- PTS cannot, because the grids are offset from each other.
                        if "idx" in item:
                            indexed[segment] = {"idx": int(item["idx"]), "session": session,
                                                "key": key_for(tk, segment)}
                        # Keep the signed URL too. It expires, but a list captured while the player
                        # is playing still carries live ones, and those are what let the tool fetch
                        # the segments a session has that the download directory lacks.
                        entries_kept.append({"name": segment, "idx": item.get("idx"),
                                             "tk": tk, "sf": signed})
                if collected:
                    sessions[session] = collected
                    entries_by_session[session] = {
                        "base": document.get("d_p") or "",
                        "entries": entries_kept,
                    }
    return out, sessions, indexed, entries_by_session


def verify(library, cache_dir):
    """Test every key against the file it names, and keep only the ones that open it.

    A key is only valid for the file as that file was downloaded: `tk` belongs to a playback session,
    so a list captured in one session yields keys that do *not* open a file downloaded in another --
    measured here as 190 of 742 library entries decrypting to noise (0.5% sync, where a right key
    gives 100%). Adding mined keys unverified therefore inflates the library with entries that look
    like coverage and fail at decrypt time, so they are tested before they are kept.
    """
    good, bad, absent = {}, 0, 0
    for name, key in library.items():
        path = os.path.join(cache_dir, name)
        if not os.path.exists(path):
            absent += 1
            continue
        try:
            from Crypto.Cipher import AES
            with open(path, "rb") as handle:
                head = handle.read(576)
            if len(head) < 576:
                absent += 1
                continue
            mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
            masked = bytes(b ^ mask[i % 16] for i, b in enumerate(head))
            plain = AES.new(key.encode(), AES.MODE_ECB).decrypt(masked)
            if plain[0] == 0x47 and plain[188] == 0x47 and plain[376] == 0x47:
                good[name] = key
            else:
                bad += 1
        except Exception:
            absent += 1
    return good, bad, absent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default=os.path.join(CAPTURED, "keys_merged.json"))
    args = ap.parse_args()

    library = {}
    per_source = {}
    for relative in SOURCES:
        path = os.path.join(CAPTURED, relative)
        found = harvest_file(path)
        per_source[relative] = len(found)
        library.update(found)

    mined, sessions, indexed, entries_by_session = harvest_lesson_lists(
        [CAPTURED, os.path.join(HERE, "..", "..", "verify_out5")])
    per_source[f"lesson lists ({len(sessions)} session(s))"] = len(mined)
    for segment, (key, _) in mined.items():
        library.setdefault(segment, key)
    if indexed:
        index_path = os.path.join(CAPTURED, "key_index.json")
        with open(index_path, "w", encoding="utf-8") as handle:
            json.dump(indexed, handle, indent=1)
        print(f"{len(indexed)} segment(s) carry a content position; wrote {index_path}")
    if entries_by_session:
        lists_path = os.path.join(CAPTURED, "lesson_lists.json")
        with open(lists_path, "w", encoding="utf-8") as handle:
            json.dump(entries_by_session, handle, indent=1)
        total = sum(len(value.get("entries", [])) for value in entries_by_session.values())
        print(f"{total} list entr(ies) across {len(entries_by_session)} session(s); "
              f"wrote {lists_path}")
    if sessions:
        session_path = os.path.join(CAPTURED, "lesson_sessions.json")
        with open(session_path, "w", encoding="utf-8") as handle:
            json.dump(sessions, handle, indent=1)
        biggest = max(sessions.items(), key=lambda item: len(item[1]))
        print(f"{len(sessions)} captured session(s); largest is {biggest[0]} "
              f"with {len(biggest[1])} segment(s)")
        print(f"wrote {session_path}")

    # The oracle-mapped key->file cache is the other direction; fold it in too.
    mapped_path = os.path.join(CAPTURED, "key_files.json")
    if os.path.exists(mapped_path):
        for key, name in json.load(open(mapped_path, encoding="utf-8")).items():
            if name and looks_like_key(key):
                library.setdefault(name, key)
                per_source["key_files.json"] = per_source.get("key_files.json", 0) + 1

    print(f"{len(library)} distinct segment(s) have a key\n")
    for relative, count in sorted(per_source.items()):
        if count:
            print(f"  {count:6d}  {relative}")

    cache_dir = r"D:\Downloads\EVPlayer2Downloads"
    verified, rejected, absent = verify(library, cache_dir)
    print(f"\nverified against the files on disk: {len(verified)} open, {rejected} do not, "
          f"{absent} not on disk")
    if rejected:
        print("  (a key belongs to the session its file was downloaded in; keys mined from another")
        print("   session's list decrypt to noise, and are dropped rather than counted as coverage)")
    library = verified

    lessons = collections.Counter()
    for name in library:
        lessons[name.split("-")[0]] += 1
    print(f"\nby lesson prefix: {dict(lessons.most_common(10))}")

    with open(args.write, "w", encoding="utf-8") as handle:
        json.dump(library, handle, indent=1)
    print(f"\nwrote {args.write} ({os.path.getsize(args.write):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
