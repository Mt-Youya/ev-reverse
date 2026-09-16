"""Decode a level-40 segment with another segment's parameter sets.

If the level-40 payload is ordinary H.264 whose SPS and PPS were replaced by decoys that still parse,
then swapping in the parameter sets from a segment that *does* decode -- same 1920x1080 geometry, level
42 -- should turn the picture back on. It is a one-minute offline test and it costs nothing to run
before reaching for anything more invasive.

    python swap_params.py <level40.ts> <donor.ts> <out.h264>
"""

import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from slice_probe import nals_of                                        # noqa: E402


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    target, donor, out = sys.argv[1:4]

    def params(path):
        found = {}
        for nal_type, body in nals_of(open(path, "rb").read()):
            if nal_type in (7, 8) and nal_type not in found:
                found[nal_type] = body
        return found

    mine, theirs = params(target), params(donor)
    print(f"target parameter sets: " + ", ".join(f"NAL {k} {len(v)} B" for k, v in sorted(mine.items())))
    print(f"donor  parameter sets: " + ", ".join(f"NAL {k} {len(v)} B" for k, v in sorted(theirs.items())))
    for kind in (7, 8):
        if kind in mine and kind in theirs:
            same = mine[kind] == theirs[kind]
            print(f"  NAL {kind}: {'identical' if same else 'differs'}"
                  + ("" if same else f"  target {mine[kind].hex(' ')}\n"
                                     f"          donor  {theirs[kind].hex(' ')}"))

    stream = bytearray()
    replaced = 0
    for nal_type, body in nals_of(open(target, "rb").read()):
        if nal_type in (7, 8) and nal_type in theirs:
            stream += b"\x00\x00\x00\x01" + theirs[nal_type]
            replaced += 1
        else:
            stream += b"\x00\x00\x00\x01" + body
    with open(out, "wb") as handle:
        handle.write(bytes(stream))
    print(f"wrote {out}: {len(stream):,} bytes, {replaced} parameter set(s) swapped")

    for label, path in (("original", target), ("swapped", out)):
        with tempfile.TemporaryDirectory() as work:
            raw = os.path.join(work, "f.gray")
            done = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "select=eq(n\\,30)",
                                   "-vsync", "0", "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw],
                                  capture_output=True, text=True)
            lines = [line for line in (done.stderr or "").splitlines() if line.strip()]
            if os.path.exists(raw) and os.path.getsize(raw) >= 1920 * 1080:
                plane = open(raw, "rb").read(1920 * 1080)
                print(f"  {label:<9} mean={statistics.fmean(plane):6.1f} "
                      f"sd={statistics.pstdev(plane):6.2f}  errors={len(lines)}")
            else:
                print(f"  {label:<9} no frame  errors={len(lines)}")
                for line in lines[:3]:
                    print(f"      {line[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
