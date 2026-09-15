"""Measure what a decode actually produced: error lines, and the luma statistics of its frames.

Sync bytes and file size say nothing about whether a picture came out -- a flat grey frame is a
structurally perfect file. This reports the two numbers that do: how many error lines the decoder
emitted, and the mean/spread of the luma plane of a few frames, printed as `mean/sd` per frame. A
real screen recording sits around mean 220 with sd 30+; a failed decode is mean 128 with sd 0.

    python decode_report.py <file> [--index 0,60,200] [--extra "-threads 1"] [--ffmpeg path]
"""

import argparse
import os
import statistics
import subprocess
import sys
import tempfile


def stats(path, indices, extra, ffmpeg):
    errors = subprocess.run([ffmpeg, "-v", "warning", "-i", path, "-f", "null", "-"] + extra,
                            capture_output=True, text=True)
    error_lines = [line for line in (errors.stderr or "").splitlines()
                   if "error" in line.lower() or "corrupt" in line.lower()]
    out = []
    for index in indices:
        with tempfile.TemporaryDirectory() as work:
            raw = os.path.join(work, "f.gray")
            subprocess.run([ffmpeg, "-v", "error", "-i", path,
                            "-vf", f"select=eq(n\\,{index})", "-vsync", "0",
                            "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw] + extra,
                           capture_output=True, text=True)
            if not os.path.exists(raw) or os.path.getsize(raw) < 1920 * 1080:
                out.append(f"n={index}: no frame")
                continue
            plane = open(raw, "rb").read(1920 * 1080)
            out.append(f"n={index}: mean={statistics.fmean(plane):6.1f} sd={statistics.pstdev(plane):6.2f}")
    return len(error_lines), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--index", default="0,60,200")
    ap.add_argument("--extra", default="")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    indices = [int(x) for x in args.index.split(",") if x.strip()]
    extra = args.extra.split() if args.extra.strip() else []
    errors, lines = stats(args.file, indices, extra, args.ffmpeg)
    label = args.label or os.path.basename(args.file)
    print(f"{label:<44} errors={errors:4d}  " + "  ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
