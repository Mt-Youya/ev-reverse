"""Decrypt a whole lesson into playable video, and check that the video's length is the lesson's.

One lesson is a list of ten-second segments. Decrypting them is the part this project already proved;
what makes it a tool rather than a pile of scripts is doing the whole job for whichever lesson is
asked for, and *checking the result against the thing the user actually cares about*: the running time.
A merge that drops a segment, doubles one, or loses the timestamps still produces a file that plays,
and it is the wrong file.

So every run ends in a measured comparison, per merged stretch:

    expected = (last segment's first PTS - first segment's first PTS) + that segment's own duration
    actual   = what ffprobe reports for the output

and both the ratio and the decoder's own error count are printed. Segments are ordered by presentation
timestamp rather than by name or index, because the download order is not playback order, and a PTS
jump larger than a segment starts a new stretch instead of being silently glued over.

Keys come from a library file, from the live player, or from the API, in that order of convenience:

    python decrypt_lesson.py --lesson 119354
    python decrypt_lesson.py --lesson 119354 --harvest 30        # top up from the running player
    python decrypt_lesson.py --names names.txt --playkey ... --token ...
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"D:\Downloads\EVPlayer2Downloads"
LIBRARY = os.path.join(HERE, "captured", "keys_merged.json")
SEGMENT_SECONDS = 10.0          # segments are ten seconds; a bigger PTS jump is a different stretch
GAP_SECONDS = 1.5               # small PTS wobble between neighbouring segments is normal
POSITION_WINDOW = 5.0           # two segments closer than this are encodings of the same position
DURATION_TOLERANCE = 0.015      # a stretched container can differ from the timeline by a frame or two


def load_library(path):
    if not os.path.exists(path):
        return {}
    return json.load(open(path, encoding="utf-8"))


def save_library(path, library):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(library, handle, indent=1, sort_keys=True)


def harvest(seconds, reporter=print):
    """Read the keys the live player is using, for whichever lesson it happens to be playing."""
    from harvest_player import OUT
    before = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                before.add(json.loads(line)["key"])
            except Exception:
                pass
    done = subprocess.run([sys.executable, os.path.join(HERE, "harvest_player.py"),
                           "--follow", "--seconds", str(seconds)],
                          capture_output=True, text=True)
    reporter((done.stdout or "").strip().splitlines()[-1:] or ["harvest produced no output"])
    after = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                after.add(json.loads(line)["key"])
            except Exception:
                pass
    return after - before


def segment_facts(plain):
    """First PTS, last PTS and SPS level of a decrypted segment."""
    from build_videos import first_pts
    from slice_probe import nals_of

    first = first_pts(plain)
    last = None
    for offset in range(0, len(plain) - 187, 188):
        packet = plain[offset:offset + 188]
        if packet[0] != 0x47 or not packet[1] & 0x40:
            continue
        control = (packet[3] >> 4) & 0x03
        if control in (0, 2):
            continue
        start = 4 + (1 + packet[4] if control == 3 else 0)
        payload = packet[start:]
        if payload[:3] != b"\x00\x00\x01" or not 0xE0 <= payload[3] <= 0xEF:
            continue
        if not payload[7] & 0x80:
            continue
        stamp = payload[9:14]
        if len(stamp) < 5:
            continue
        value = (((stamp[0] >> 1) & 0x07) << 30) | (stamp[1] << 22) \
            | (((stamp[2] >> 1) & 0x7F) << 15) | (stamp[3] << 7) | ((stamp[4] >> 1) & 0x7F)
        last = value
    level = None
    for nal_type, body in nals_of(plain):
        if nal_type == 7 and len(body) > 3:
            level = body[3]
            break
    return first, last, level


def duration_of(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float((done.stdout or "").strip())
    except ValueError:
        return None


def probe_start_pts(path):
    """First video PTS in 90 kHz units, asked of ffprobe.

    The hand-rolled PES walk finds a PTS only when the segment's first video packet carries one;
    several hundred segments here start with a packet that does not, and were being dropped as
    unusable rather than measured. ffprobe looks at the stream as a whole and answers anyway.
    """
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=start_time",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return int(round(float((done.stdout or "").strip()) * 90000))
    except ValueError:
        return None


def decode_errors(path):
    done = subprocess.run(["ffmpeg", "-v", "warning", "-i", path, "-f", "null", "-"],
                          capture_output=True, text=True)
    return sum(1 for line in (done.stderr or "").splitlines()
               if "error" in line.lower() or "corrupt" in line.lower())


def watch_for_lists(seconds, reporter=print):
    """Watch the player's API traffic until it fetches a lesson, then fold that list into the library.

    `tk` only arrives when the player opens a lesson, and asking for it needs a token that expires
    daily -- but the response the player gets is the whole lesson, every segment with its `tk`. So the
    tool waits for that one moment instead of asking a person to time it: start this, open the lesson
    in the player, and the keys land. Inflation of the captured bodies is the capture tool's job, so
    the only thing watched here is the file count.

    `seconds` of 0 means "however long it takes".
    """
    import glob
    inflated = os.path.join(HERE, "captured", "inflated")
    before = set(glob.glob(os.path.join(inflated, "*.json")))
    deadline = time.time() + seconds if seconds else None
    reporter(f"watching the player's API traffic for a lesson list "
             f"({'up to %.0fs' % seconds if seconds else 'until one arrives'})")
    while True:
        subprocess.run([sys.executable, os.path.join(HERE, "capture_api.py"),
                        "--follow", "--seconds", "60"], capture_output=True, text=True)
        fresh = set(glob.glob(os.path.join(inflated, "*.json"))) - before
        if fresh:
            reporter(f"{len(fresh)} new response(s) captured; mining them for keys")
            subprocess.run([sys.executable, os.path.join(HERE, "key_inventory.py")],
                           capture_output=True, text=True)
            return True
        if deadline and time.time() > deadline:
            reporter("nothing arrived in the window; continuing with the keys already held")
            return False


def newest_token(path=None):
    """The most recent Bearer token captured from the player's own API traffic, if any is still live."""
    import base64
    path = path or os.path.join(HERE, "captured", "tokens.jsonl")
    if not os.path.exists(path):
        return None
    best = None
    for line in open(path, encoding="utf-8"):
        try:
            record = json.loads(line)
            token = record["authorization"].split()[-1]
            stamp = float(record.get("at", 0))
        except Exception:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, token)
    if not best:
        return None
    token = best[1]
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        expiry = json.loads(base64.urlsafe_b64decode(body))["exp"]
    except Exception:
        return token
    if expiry < time.time():
        print(f"the newest captured token expired {int((time.time() - expiry) / 60)} minute(s) ago")
        return None
    print(f"using a captured token valid for another {int((expiry - time.time()) / 60)} minute(s)")
    return token


def newest_playkey(path=None):
    path = path or os.path.join(HERE, "captured", "playkeys.json")
    if not os.path.exists(path):
        return None
    try:
        entries = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    return entries[-1] if entries else None


def load_facts_cache():
    path = os.path.join(HERE, "captured", "segment_facts.json")
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_facts_cache(cache):
    path = os.path.join(HERE, "captured", "segment_facts.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, indent=1, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="", help="file-name prefix, e.g. 119354")
    ap.add_argument("--names", default="", help="a file with one segment filename per line")
    ap.add_argument("--cache", default=CACHE_DIR)
    ap.add_argument("--library", default=LIBRARY)
    ap.add_argument("--out", default=r"D:\ev-export\lessons")
    ap.add_argument("--container", default="mp4", choices=["mp4", "mkv"])
    ap.add_argument("--min-segments", type=int, default=1,
                    help="skip merged runs shorter than this (1 keeps everything we can decrypt)")
    ap.add_argument("--harvest", type=float, default=0,
                    help="seconds to watch the live player for extra keys first")
    ap.add_argument("--watch", type=float, default=0,
                    help="watch the player's API traffic for a lesson list first (0 = off, "
                         "negative = until one arrives)")
    ap.add_argument("--playkey", default="", help="defaults to the newest one captured from the player")
    ap.add_argument("--token", default="", help="defaults to the newest unexpired captured token")
    ap.add_argument("--session", default="largest",
                    help="restrict to one captured session's segment set: 'largest', 'all', or a "
                         "session label from captured/lesson_sessions.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from build_videos import decrypt, file_for_key

    names = []
    if args.names:
        names = [line.strip() for line in open(args.names, encoding="utf-8") if line.strip()]
    else:
        names = sorted(n for n in os.listdir(args.cache)
                       if n.endswith(".ts") and (not args.lesson or n.startswith(args.lesson)))
    if args.limit:
        names = names[:args.limit]
    if not names:
        print(f"no .ts file in {args.cache} matches {args.lesson or '(any lesson)'}")
        return 1
    print(f"{len(names):,} candidate file(s) for lesson '{args.lesson or 'any'}'")

    # One session at a time. The lesson's segments are not identical between playback sessions -- the
    # same ten seconds appears again with its first PTS a few seconds away -- so mixing two sessions'
    # segments into one merge yields a video that runs longer than the lesson. The player uses one
    # session's set; so does this.
    if args.session != "all":
        sessions_path = os.path.join(HERE, "captured", "lesson_sessions.json")
        if os.path.exists(sessions_path):
            sessions = json.load(open(sessions_path, encoding="utf-8"))
            available = set(names)
            best_label, best_names = None, []
            for label, listed in sessions.items():
                if args.session not in ("largest", "") and args.session != label:
                    continue
                usable = [name for name in listed if name in available]
                if len(usable) > len(best_names):
                    best_label, best_names = label, usable
            if best_names:
                print(f"using session {best_label}: {len(best_names)} segment(s), "
                      f"{len(names) - len(best_names)} variant(s) from other sessions left out")
                names = best_names

    library = load_library(args.library)
    print(f"{len(library)} key(s) in {args.library}")

    if args.watch:
        watch_for_lists(0 if args.watch < 0 else args.watch)
        library = load_library(args.library)
        print(f"{len(library)} key(s) after watching")

    if args.harvest:
        found = harvest(args.harvest)
        if found:
            print(f"harvest added {len(found)} key(s); mapping them to files")
            for key in found:
                name = file_for_key(key, names)
                if name:
                    library[name] = key
            save_library(args.library, library)

    if not args.playkey:
        args.playkey = newest_playkey() or ""
    if not args.token:
        args.token = newest_token() or ""

    if args.playkey and args.token:
        import match_lesson_files
        print(f"asking the API to sign {len(names)} name(s)")
        keys = match_lesson_files.ask_for_keys(args.playkey, args.token, names,
                                               os.path.join(HERE, "captured", "lesson"))
        import hashlib
        for name, tk in keys.items():
            library.setdefault(name, hashlib.md5((tk + name + "20220507").encode()).hexdigest())
        save_library(args.library, library)
        print(f"{len(keys)} name(s) signed; library now {len(library)} key(s)")

    lesson_dir = os.path.join(args.out, args.lesson or "lesson")
    dec_dir = os.path.join(lesson_dir, "dec")
    os.makedirs(dec_dir, exist_ok=True)

    index_map = {}
    index_path = os.path.join(HERE, "captured", "key_index.json")
    if os.path.exists(index_path):
        index_map = json.load(open(index_path, encoding="utf-8"))
    print(f"{len(index_map)} segment(s) have a known content position")

    segments = []
    missing = 0
    facts = load_facts_cache()
    reused = 0
    for name in names:
        key = library.get(name)
        if not key:
            missing += 1
            continue
        path = os.path.join(args.cache, name)
        if not os.path.exists(path):
            missing += 1
            continue
        written = os.path.join(dec_dir, name)
        # Facts are cached against the key, so a lesson assembled once can be re-assembled -- per
        # session, say -- without decrypting six hundred segments again.
        fact = facts.get(name)
        if fact and fact.get("key") == key and os.path.exists(written):
            first, last, level = fact["pts"], fact.get("last"), fact.get("level")
            reused += 1
        else:
            plain = decrypt(open(path, "rb").read(), key, name)
            first, last, level = segment_facts(plain)
            if not os.path.exists(written):
                with open(written, "wb") as handle:
                    handle.write(plain)
            if first is None:
                first = probe_start_pts(written)
            if first is not None:
                facts[name] = {"key": key, "pts": first, "last": last, "level": level}
        if first is None:
            print(f"  {name}: no video PTS from either parser; skipped")
            continue
        segments.append({"name": name, "pts": first, "last": last, "level": level,
                         "bytes": 0, "path": written,
                         "idx": (index_map.get(name) or {}).get("idx")})
    save_facts_cache(facts)
    print(f"{len(segments)} segment(s) placed ({missing} without a usable key, "
          f"{reused} taken from the facts cache)")
    print(f"{len(segments)} segment(s) decrypted ({missing} without a usable key)")

    if not segments:
        print("nothing to merge: no keys for this lesson's files")
        return 1

    segments.sort(key=lambda item: item["pts"])

    # One segment per position. A lesson keeps several encodings of the same ten seconds -- different
    # uuid, and a first PTS that differs by a frame or two rather than being identical -- and merging
    # all of them produces a video that plays and is two or three times too long, which is exactly the
    # failure the duration check exists to catch. Positions are ten seconds apart, so anything within
    # POSITION_WINDOW of the previous kept segment is another encoding of it, not the next one.
    unique, variants = [], 0
    for item in segments:
        if unique and (item["pts"] - unique[-1]["pts"]) < POSITION_WINDOW * 90000:
            variants += 1
            continue
        unique.append(item)
    if variants:
        print(f"{variants} duplicate-position segment(s) dropped ({len(unique)} positions kept)")
    segments = unique

    # One timeline, built from both kinds of evidence.
    #
    # A captured list gives a segment's index outright. A segment without one still says which session
    # it came from: positions within a session are exactly ten seconds apart, so `pts - index * 10s`
    # is that session's own zero point and is the same number for every segment of it. Indexed
    # segments therefore reveal their session's zero point, and an unindexed segment landing on one of
    # those zero points can be placed on the same timeline instead of being emitted as a parallel,
    # overlapping video -- which is what the two-path version did, and why its output was longer than
    # the lesson.
    STEP = 900000                                     # ten seconds in 90 kHz units
    lists_path = os.path.join(HERE, "captured", "lesson_lists.json")
    sessions_meta = json.load(open(lists_path, encoding="utf-8")) if os.path.exists(lists_path) else {}
    session_length = {label: len(payload.get("entries", []))
                      for label, payload in sessions_meta.items()}

    positioned = [item for item in segments if item.get("idx") is not None]
    grids = {}
    for item in positioned:
        base = item["pts"] - item["idx"] * STEP
        label = (index_map.get(item["name"]) or {}).get("session")
        record = grids.setdefault(base, {"count": 0, "length": 0})
        record["count"] += 1
        # A grid reaches exactly as far as the session that defines it. Without this bound the
        # extrapolation happily invents positions past the end of the lesson -- the first version
        # reported a timeline reaching position 287 in a lesson the captured list says has 195.
        record["length"] = max(record["length"], session_length.get(label, 0))

    by_index = {}
    for item in positioned:
        by_index.setdefault(item["idx"], item)
    placed = 0
    unmatched = []
    for item in segments:
        if item.get("idx") is not None:
            continue
        for base, record in grids.items():
            delta = item["pts"] - base
            if delta < 0 or delta % STEP != 0:
                continue
            index = delta // STEP
            if record["length"] and index >= record["length"]:
                continue
            if index not in by_index:
                by_index[index] = dict(item, idx=index)
                placed += 1
            break
        else:
            unmatched.append(item)

    print(f"{len(positioned)} segment(s) indexed by a captured list, {placed} more placed by their "
          f"session's own grid, {len(unmatched)} left unplaced")
    if not by_index:
        print("nothing can be placed; no captured list covers these segments")
        return 1

    ordered = [by_index[key] for key in sorted(by_index)]
    runs, current = [], [ordered[0]]
    for item in ordered[1:]:
        if item["idx"] != current[-1]["idx"] + 1:
            runs.append(current)
            current = [item]
        else:
            current.append(item)
    runs.append(current)
    print(f"timeline covers position {ordered[0]['idx']} .. {ordered[-1]['idx']} "
          f"in {len(runs)} contiguous run(s)")
    sequences = [(f"idx{runs_index:02d}", run) for runs_index, run in enumerate(runs, 1)]

    stretches = [(label, group) for label, group in sequences if len(group) >= args.min_segments]

    report = []
    for number, (label, group) in enumerate(stretches, 1):
        if len(group) < args.min_segments:
            continue
        first_seconds = group[0]["pts"] / 90000.0
        last_seconds = group[-1]["pts"] / 90000.0
        # Sum of what each segment actually contains, plus one frame for the last. Using the PTS span
        # instead assumes the segments are on one grid and contiguous, which is exactly what is not
        # true once segments from more than one session are in play.
        expected = sum(((item["last"] or item["pts"]) - item["pts"]) / 90000.0 + 0.04
                       for item in group)
        levels = sorted({item["level"] for item in group if item["level"] is not None})
        stem = f"{args.lesson or 'lesson'}_{number:02d}_{label}_{int(first_seconds)}s-{int(last_seconds)}s"

        # The concat demuxer, not a byte concatenation: it rewrites each input's timestamps so the
        # output follows on from the previous file. Concatenating the transport streams and copying
        # the result keeps the original PTS, so the duration becomes the span of whatever offsets the
        # segments happened to carry -- a file that plays and is the wrong length.
        list_path = os.path.join(lesson_dir, stem + ".txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for item in group:
                handle.write("file '" + item["path"].replace("'", "'\\''") + "'\n")
        target = os.path.join(lesson_dir, f"{stem}.{args.container}")
        done = subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0",
                               "-i", list_path, "-c", "copy", "-y", target],
                              capture_output=True, text=True)
        if not os.path.exists(target):
            print(f"  {stem}: merge failed: {(done.stderr or '')[:160]}")
            continue
        os.remove(list_path)

        actual = duration_of(target)
        errors = decode_errors(target)
        ratio = (actual / expected) if (actual and expected) else None
        entry = {
            "video": os.path.basename(target),
            "segments": len(group),
            "first_seconds": round(first_seconds, 2),
            "last_seconds": round(last_seconds, 2),
            "expected_seconds": round(expected, 2),
            "actual_seconds": round(actual, 2) if actual else None,
            "ratio": round(ratio, 4) if ratio else None,
            "sps_levels": levels,
            "decode_errors": errors,
            "bytes": os.path.getsize(target),
            "positions": [round(item["pts"] / 90000.0, 2) for item in group],
        }
        report.append(entry)
        verdict = ("duration matches" if ratio and abs(ratio - 1) <= DURATION_TOLERANCE
                   else "CHECK DURATION")
        protected = "  [level 40: picture blocked, see docs]" if 40 in levels else ""
        print(f"  {stem}: {len(group)} segments  expected {expected:7.2f}s  actual "
              f"{(actual or 0):7.2f}s  delta {((actual or 0) - expected):+6.2f}s  ratio "
              f"{(ratio or 0):.4f}  {verdict}  errors {errors}{protected}")

    with open(os.path.join(lesson_dir, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)

    # Coverage, in the only unit that answers the question the user actually asks: how much of the
    # lesson is in the output, and is what is there the right length. Positions come from the captured
    # lesson lists, which enumerate the lesson; seconds come from the videos just measured.
    assembled = sum(entry["expected_seconds"] for entry in report)
    covered = len({item["idx"] for item in segments if item.get("idx") is not None})
    covered = len(by_index)
    lists_path = os.path.join(HERE, "captured", "lesson_lists.json")
    if os.path.exists(lists_path):
        lists = json.load(open(lists_path, encoding="utf-8"))
        verified = set(library)
        best_label, best_positions = None, 0
        for label, payload in lists.items():
            names = [entry["name"] for entry in payload.get("entries", [])]
            on_disk = len([name for name in names if name in verified])
            if on_disk > best_positions:
                best_label, best_positions = label, on_disk
        lesson_positions = max((len(payload.get("entries", [])) for payload in lists.values()),
                               default=0)
        if lesson_positions:
            print(f"\nkeys: the largest captured list holds {lesson_positions} entr(ies) "
                  f"(one request's batch, not a known lesson length); session {best_label} "
                  f"contributes {best_positions} of them")
        print(f"assembled {assembled:.1f}s across {len(report)} video(s), covering {covered} "
              f"position(s) of the lesson's index. Every video was measured against the content it "
              f"was built from, which is the length guarantee; the index coverage says how much of "
              f"the lesson is here.")
    print(f"\n{len(report)} video(s) in {lesson_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
