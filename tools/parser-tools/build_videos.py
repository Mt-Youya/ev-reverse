"""Turn harvested player keys into watchable videos.

Keys come from `harvest_player.py`, which reads them off the live player. Each key belongs to
exactly one file in the download directory, and the way to find it is worth stating because the
obvious version of it is wrong: testing one sync byte (`plain[0] == 0x47`) accepts a wrong key once
in 256, and 8 keys against 10,770 files produced 335 "matches" where 336 false positives are
expected. Three sync bytes at offsets 0, 188 and 376 make it a one-in-sixteen-million test, and
that returned exactly one file per key, twenty times over.

From there each file is decrypted, its first presentation timestamp is read from its own packets
(the player downloads several segments at once, so the order keys arrive in is not playback order),
and the segments are grouped into videos and remuxed with ffmpeg. Every output is checked by
decoding it: a file that is structurally perfect and visually grey reports zero problems to a
sync-byte count and thousands to a full decode, so the decode is what gets reported.

    python build_videos.py                       # everything harvested so far
    python build_videos.py --min-segments 5      # skip fragments
"""

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
KEYS = os.path.join(HERE, "captured", "player_keys.jsonl")
CACHE = os.path.join(HERE, "captured", "key_files.json")
CACHE_DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
GAP_SECONDS = 30          # a PTS jump this large starts a new video
GAP_WALL_SECONDS = 600    # so does this long a pause between keys arriving


def decrypt(blob, key, name):
    from Crypto.Cipher import AES
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    masked = bytes(b ^ mask[i % 16] for i, b in enumerate(blob))
    plain = AES.new(key.encode(), AES.MODE_ECB).decrypt(masked)
    padding = len(plain) % 188
    if 0 < padding <= 15 and plain[-padding:] == b"#" * padding:
        plain = plain[:-padding]
    return plain


def first_pts(stream):
    """First video PES timestamp, in 90 kHz units. Skips the adaptation field, which is why an
    earlier version of this reported every segment as having no PTS."""
    for offset in range(0, len(stream) - 187, 188):
        packet = stream[offset:offset + 188]
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
        return (((stamp[0] >> 1) & 0x07) << 30) | (stamp[1] << 22) \
            | (((stamp[2] >> 1) & 0x7F) << 15) | (stamp[3] << 7) | ((stamp[4] >> 1) & 0x7F)
    return None


def file_for_key(key, files):
    from Crypto.Cipher import AES
    for name in files:
        try:
            with open(os.path.join(CACHE_DOWNLOADS, name), "rb") as handle:
                head = handle.read(576)
        except Exception:
            continue
        if len(head) < 576:
            continue
        mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
        masked = bytes(b ^ mask[i % 16] for i, b in enumerate(head))
        plain = AES.new(key.encode(), AES.MODE_ECB).decrypt(masked)
        if plain[0] == 0x47 and plain[188] == 0x47 and plain[376] == 0x47:
            return name
    return None


def decode_errors(path):
    done = subprocess.run(["ffmpeg", "-v", "warning", "-i", path, "-f", "null", "-"],
                          capture_output=True, text=True)
    return sum(1 for line in (done.stderr or "").splitlines()
               if "error" in line.lower() or "corrupt" in line.lower())


def main():
    global CACHE_DOWNLOADS
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", default=KEYS)
    ap.add_argument("--downloads", default=CACHE_DOWNLOADS)
    ap.add_argument("--out", default=os.path.join(REPO, "ev_videos"))
    ap.add_argument("--min-segments", type=int, default=3)
    ap.add_argument("--container", default="mp4", choices=["mp4", "mkv"])
    args = ap.parse_args()
    CACHE_DOWNLOADS = args.downloads

    if not os.path.exists(args.keys):
        print(f"no keys at {args.keys}; run harvest_player.py first")
        return 1

    keys = []
    for line in open(args.keys, encoding="utf-8"):
        try:
            record = json.loads(line)
        except Exception:
            continue
        keys.append((record["at"], record["key"]))
    distinct = {}
    for when, key in keys:
        distinct.setdefault(key, when)
    print(f"{len(keys)} key line(s), {len(distinct)} distinct", flush=True)

    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
    files = [name for name in os.listdir(CACHE_DOWNLOADS) if name.endswith(".ts")]
    print(f"{len(files)} file(s) in the download directory", flush=True)

    segments = []
    for index, (key, when) in enumerate(sorted(distinct.items(), key=lambda kv: kv[1]), 1):
        name = cache.get(key)
        if name is None or not os.path.exists(os.path.join(CACHE_DOWNLOADS, name)):
            name = file_for_key(key, files)
            cache[key] = name
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
        if not name:
            print(f"  key {key} matches no file", flush=True)
            continue
        plain = decrypt(open(os.path.join(CACHE_DOWNLOADS, name), "rb").read(), key, name)
        pts = first_pts(plain)
        if pts is None:
            continue
        segments.append({"pts": pts, "name": name, "key": key, "when": when, "plain": plain})
        if index % 20 == 0:
            print(f"  {index}/{len(distinct)} keys placed", flush=True)

    segments.sort(key=lambda item: (item["when"], item["pts"]))
    groups = []
    current = []
    for item in segments:
        if not current:
            current = [item]
            continue
        previous = current[-1]
        jump = abs(item["pts"] - previous["pts"]) / 90000.0
        pause = item["when"] - previous["when"]
        if jump > GAP_SECONDS or pause > GAP_WALL_SECONDS:
            groups.append(current)
            current = [item]
        else:
            current.append(item)
    if current:
        groups.append(current)

    os.makedirs(args.out, exist_ok=True)
    report = []
    for number, group in enumerate(groups, 1):
        if len(group) < args.min_segments:
            continue
        group.sort(key=lambda item: item["pts"])
        start = group[0]["pts"] / 90000.0
        end = group[-1]["pts"] / 90000.0
        stem = f"{number:02d}_{group[0]['name'][7:15]}_{int(start)}s-{int(end)}s"
        ts_path = os.path.join(args.out, stem + ".ts")
        with open(ts_path, "wb") as handle:
            for item in group:
                handle.write(item["plain"])
        target = os.path.join(args.out, f"{stem}.{args.container}")
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", ts_path, "-c", "copy", "-y", target],
                              capture_output=True, text=True)
        if not os.path.exists(target):
            print(f"  {stem}: remux failed: {(done.stderr or '')[:120]}", flush=True)
            continue
        errors = decode_errors(target)
        size = os.path.getsize(target)
        report.append({"video": os.path.basename(target), "segments": len(group),
                       "seconds": round(end - start, 1), "bytes": size, "decode_errors": errors})
        print(f"  [{number:02d}] {os.path.basename(target)}  {len(group)} segment(s)  "
              f"{end - start:6.1f}s  {size / 1048576:5.1f} MB  decode errors: {errors}", flush=True)
        os.remove(ts_path)

    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(f"\n{len(report)} video(s) in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
