"""Measure what a decode actually produced: error lines, and the luma statistics of its frames.

Sync bytes and file size say nothing about whether a picture came out -- a flat grey frame is a
structurally perfect file. This reports the two numbers that do: how many error lines the decoder
emitted, and the mean/spread of the decoded Y plane of a few frames, printed as `mean/sd` per frame.
Brightness depends on the scene; compare actual pictures and errors rather than imposing a minimum
mean. Dimensions come from ffprobe, and Y is measured without gray/full-range conversion.

    python decode_report.py <file> [--index 0,60,200] [--extra "-threads 1"] [--ffmpeg path]
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile


def stats(path, indices, extra, ffmpeg):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "json", path],
                           capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    pixels = stream["width"] * stream["height"]
    errors = subprocess.run([ffmpeg, "-v", "warning"] + extra +
                            ["-i", path, "-map", "0:v:0", "-an", "-f", "null", "-"],
                            capture_output=True, text=True)
    error_lines = [line for line in (errors.stderr or "").splitlines()
                   if "error" in line.lower() or "corrupt" in line.lower()]
    out = []
    for index in indices:
        with tempfile.TemporaryDirectory() as work:
            raw = os.path.join(work, "f.yuv")
            subprocess.run([ffmpeg, "-v", "error"] + extra + ["-i", path,
                            "-map", "0:v:0", "-an",
                            "-vf", f"select=eq(n\\,{index})", "-vsync", "0",
                            "-frames:v", "1", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-y", raw],
                           capture_output=True, text=True)
            if not os.path.exists(raw) or os.path.getsize(raw) < pixels:
                out.append(f"n={index}: no frame")
                continue
            with open(raw, "rb") as handle:
                plane = handle.read(pixels)
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
