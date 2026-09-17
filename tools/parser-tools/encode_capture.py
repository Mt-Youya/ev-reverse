"""Turn a frame capture into a video whose length is the length of what was recorded.

The capture does not run at exactly the decoder's frame rate: writing three megabytes a frame through
a hook costs a little, and a run that lasts 25 seconds of wall clock produced 437 frames rather than
625. Encoding those at 25 fps would produce a 17-second file -- a video that plays, is missing a third
of its frames, and is the wrong length, which is the one thing this project refuses to ship.

So the rate is measured, not assumed: frames divided by the wall-clock seconds the capture covered,
and that is what the encoder is told. The duration then matches the recording, and the drop shows up
where it belongs -- as a lower frame rate.

    python encode_capture.py D:\\ev-export\\capture --seconds 25 [--audio <segment.ts>]
"""

import argparse
import json
import os
import subprocess
import sys


FRAME_BYTES = 1920 * 1080 * 3 // 2          # YUV420 at the size the player decodes


def duration_of(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float((done.stdout or "").strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--seconds", type=float, required=True,
                    help="wall-clock seconds the capture actually covered")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--out", default="")
    ap.add_argument("--audio", default="", help="a decrypted segment to take the audio track from")
    args = ap.parse_args()

    frame_bytes = args.width * args.height * 3 // 2
    # Per-file rates when the capture recorded its own timing; the run-wide average otherwise. A clip
    # recorded while the decoder was slow is not the same speed as one recorded while it was fast, and
    # giving them one rate is what made a batch come out at 1.8 fps and be encoded as if it were 10.
    per_file = {}
    index_path = os.path.join(args.directory, "capture_index.json")
    if os.path.exists(index_path):
        for entry in json.load(open(index_path, encoding="utf-8")):
            span = (entry.get("last") or 0) - (entry.get("first") or 0)
            if entry.get("frames") and span > 0:
                per_file[os.path.basename(entry["name"])] = entry["frames"] / span
        print(f"{len(per_file)} file(s) have their own measured rate")
    files = sorted((name for name in os.listdir(args.directory) if name.endswith(".yuv")),
                   key=lambda name: int(name.split("_")[1].split(".")[0]))
    if not files:
        print(f"no .yuv capture in {args.directory}")
        return 1

    # The same stream file appears twice in a listing whenever the decoder reopened it; count each
    # file once, and take its frame count from its size instead of from a counter that double counts.
    total_frames, total_bytes = 0, 0
    sizes = {}
    for name in files:
        size = os.path.getsize(os.path.join(args.directory, name))
        sizes[name] = size
        total_bytes += size
        total_frames += size // frame_bytes
    rate = total_frames / args.seconds if args.seconds else 0
    print(f"{len(sizes)} file(s), {total_frames} frame(s), {total_bytes / 1048576:.1f} MB")
    print(f"measured rate: {rate:.2f} fps over {args.seconds:.1f}s "
          f"(the decoder's own rate would be higher; frames were dropped and the encoder is told so)")

    out_dir = args.out or args.directory
    os.makedirs(out_dir, exist_ok=True)
    produced = []
    for name, size in sizes.items():
        frames = size // frame_bytes
        if frames < 2:
            continue
        rate_for_file = per_file.get(name, rate)
        path = os.path.join(args.directory, name)
        target = os.path.join(out_dir, name[:-4] + ".mp4")
        done = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                               "-s", f"{args.width}x{args.height}", "-r", f"{rate_for_file:.6f}",
                               "-i", path, "-c:v", "libx264", "-preset", "veryfast",
                               "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-y", target],
                              capture_output=True, text=True)
        if not os.path.exists(target):
            print(f"  {name}: encode failed: {(done.stderr or '')[:120]}")
            continue
        actual = duration_of(target)
        expected = frames / rate_for_file if rate_for_file else 0
        produced.append((name, frames, expected, actual))

    print(f"\n{'file':<24}{'frames':>8}{'expected':>10}{'actual':>9}{'delta':>8}")
    worst = 0.0
    for name, frames, expected, actual in produced:
        delta = (actual or 0) - expected
        worst = max(worst, abs(delta))
        print(f"{name:<24}{frames:>8}{expected:>10.2f}{(actual or 0):>9.2f}{delta:>+8.2f}")
    print(f"\n{len(produced)} video(s); worst length difference {worst:.2f}s")
    if args.audio:
        print(f"audio would come from {args.audio} (already decrypted, so lossless)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
