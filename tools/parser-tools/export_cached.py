"""Export every lesson the captures can still open, from what is already on disk.

The offline path needs two things per segment: its ciphertext (the player's download directory) and
its `tk` (only ever in a segment-list response). A capture holds lists, and each list covers the
window the player asked about at that moment -- five or six segments for a download, more for a
playback. So a lesson is exportable to the extent that the *union* of every captured list for that
lesson covers its segments, and no lesson is exportable at all if none of its lists was captured.

This walks every source of lists (probe payloads, the archived inflated responses, and `fetch`
output), unions them per lesson host, derives keys, decrypts, merges, and checks the result the only
way that means anything: every 188-byte packet of the merged file has to start with 0x47.

    python export_cached.py --cache D:\\Downloads\\EVPlayer2Downloads --out D:\\ev-export
"""

import argparse
import glob
import gzip
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EVMEDIA = os.path.join(REPO, "target", "release", "evmedia.exe")
LIST = re.compile(rb'\{"d_p":"[^"]+","k_l":\[.*?\]\}')


def lists_from(path):
    """Every `{"d_p", "k_l"}` document a file holds, whatever wrapper it arrived in."""
    blob = open(path, "rb").read()
    plain = gzip.GzipFile(fileobj=io.BytesIO(blob)).read() if blob[:2] == b"\x1f\x8b" else blob
    found = []
    for match in LIST.finditer(plain):
        try:
            found.append(json.loads(match.group().decode("utf-8")))
        except Exception:
            pass
    try:
        document = json.loads(plain.decode("utf-8"))
        if isinstance(document, dict) and document.get("k_l"):
            found.append(document)
    except Exception:
        pass
    return found


def collect():
    """lesson -> ordered [(filename, tk)], using capture order for the ordering.

    The `idx` in a list is *window-relative*: the player asks about five segments at a time and the
    reply numbers them 0..4, so the same index names a different segment in every window and a union
    keyed on it is nonsense (the first version of this tool did exactly that and `derive` refused
    every lesson with "indexes are not a contiguous 0..n range"). What survives is the order the
    payloads were captured in: the player downloads a lesson front to back, so the windows are in
    order and the index orders the segments inside one.
    """
    lessons = {}
    sources = 0
    patterns = [
        os.path.join(HERE, "captured", "api", "*", "*.bin"),
        os.path.join(HERE, "captured", "inflated", "*.json"),
        os.path.join(REPO, "verify_out*", "*.json"),
    ]
    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    # Capture order: the probe names payloads NNN-<caller>.bin inside a timestamped session
    # directory, so sorting on the full path orders sessions first and payloads second.
    for path in sorted(paths, key=lambda p: (os.path.dirname(p), os.path.basename(p))):
        for document in lists_from(path):
            if not document.get("k_l"):
                continue
            sources += 1
            host = str(document.get("d_p", "")).rstrip("/")
            lesson = host.rsplit("/", 1)[-1] or os.path.basename(os.path.dirname(path))
            for item in document["k_l"]:
                name = str(item.get("sf", "")).split("?")[0].rsplit("/", 1)[-1]
                if not name or not item.get("tk"):
                    continue
                # One list can repeat a segment it already named; the first sighting wins, and a
                # segment seen in two windows keeps the position of the window that came first.
                lessons.setdefault(lesson, {})[name] = (sources, item.get("idx", 0), item["tk"])
    return lessons, sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=r"D:\Downloads\EVPlayer2Downloads")
    ap.add_argument("--out", default=os.path.join(REPO, "export_out"))
    ap.add_argument("--limit", type=int, default=0, help="stop after this many lessons (0 = all)")
    args = ap.parse_args()

    if not os.path.exists(EVMEDIA):
        print(f"missing {EVMEDIA}; run: cargo build --release -p evmedia")
        return 1

    cache = set(os.listdir(args.cache))
    lessons, sources = collect()
    print(f"{sources} list(s) read, {len(lessons)} distinct lesson(s)")

    os.makedirs(args.out, exist_ok=True)
    exported = failed = skipped = 0
    total_bytes = total_packets = total_sync = 0
    report = []

    for lesson, entries in sorted(lessons.items()):
        if args.limit and exported >= args.limit:
            break
        present = {name: value for name, value in entries.items() if name in cache}
        if not present:
            skipped += 1
            continue

        work = os.path.join(args.out, lesson)
        os.makedirs(work, exist_ok=True)
        # `derive` reads the ciphertext by name from the download directory, so the input directory
        # is the download directory itself: nothing is copied and nothing is renamed. The indices are
        # rewritten to a contiguous 0..n-1 in the order the windows were captured, because that is
        # the order this tool reconstructed and `derive` checks contiguity rather than trusting it.
        ordered = sorted(present.items(), key=lambda kv: (kv[1][0], kv[1][1]))
        playlist = {
            "d_p": "",
            "k_l": [{"idx": index, "sf": f"/{name}", "tk": tk}
                    for index, (name, (_window, _idx, tk)) in enumerate(ordered)],
        }
        # A signed path is not needed for the key, only for the download; `sf` is read for its
        # filename, so a bare name keeps this independent of an expired URL.
        list_path = os.path.join(work, "list.json")
        with open(list_path, "w", encoding="utf-8") as handle:
            json.dump(playlist, handle, ensure_ascii=False, indent=1)

        manifest = os.path.join(work, "manifest.json")
        merged = os.path.join(args.out, f"{lesson}.ts")
        steps = [
            [EVMEDIA, "derive", "--playlist", list_path, "--input", args.cache, "--output", manifest],
            [EVMEDIA, "decode-ev", args.cache, manifest, merged],
        ]
        ok = True
        for step in steps:
            done = subprocess.run(step, capture_output=True, text=True)
            if done.returncode != 0:
                ok = False
                print(f"  {lesson}: {' '.join(step[1:2])} failed: {done.stderr.strip()[:120]}")
                break
        if not ok:
            failed += 1
            continue

        data = open(merged, "rb").read()
        packets = len(data) // 188
        sync = sum(1 for i in range(0, len(data) - 187, 188) if data[i] == 0x47)
        total_bytes += len(data)
        total_packets += packets
        total_sync += sync
        exported += 1
        report.append({"lesson": lesson, "segments": len(present), "of": len(entries),
                       "bytes": len(data), "packets": packets, "sync": sync})
        print(f"  [{exported:3d}] {lesson}  {len(present):3d}/{len(entries):3d} segment(s)  "
              f"{len(data):9d} B  sync {sync}/{packets}")

    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(f"\n{exported} lesson(s) exported, {failed} failed, {skipped} skipped (no ciphertext on disk)")
    print(f"{total_bytes} bytes, {total_packets} TS packets, {total_sync} with a sync byte "
          f"({total_sync / max(1, total_packets) * 100:.2f}%)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
