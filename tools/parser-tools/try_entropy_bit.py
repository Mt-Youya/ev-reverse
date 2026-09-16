"""Test whether a level-40 stream decodes when a single PPS bit is taken at anything but face value.

Every stock decoder conceals the whole I frame of these segments after reading almost the entire
slice for one macroblock, which is what a wrong entropy mode looks like: the arithmetic decoder never
finds a terminating bin and walks off the end. The PPS says CABAC. If the payload is really CAVLC --
encoded that way and labelled the other way round -- then the parameter set is the lie, and flipping
that one bit should turn the picture back on. It is one bit and one decode, so it is worth trying
before anything more elaborate.

    python try_entropy_bit.py captured/classify/<file>.ts
"""

import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from slice_probe import nals_of                                        # noqa: E402


def decode(path, indices=(30,)):
    with tempfile.TemporaryDirectory() as work:
        raw = os.path.join(work, "f.gray")
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                               f"select=eq(n\\,{indices[0]})", "-vsync", "0", "-pix_fmt", "gray",
                               "-f", "rawvideo", "-y", raw], capture_output=True, text=True)
        errors = [line for line in (done.stderr or "").splitlines() if line.strip()]
        if not os.path.exists(raw) or os.path.getsize(raw) < 1920 * 1080:
            return None, errors
        plane = open(raw, "rb").read(1920 * 1080)
        return (statistics.fmean(plane), statistics.pstdev(plane)), errors


def emit(nals, out_path, patch=None):
    with open(out_path, "wb") as handle:
        for index, (nal_type, body) in enumerate(nals):
            if patch and index == patch[0]:
                body = patch[1](bytearray(body))
            handle.write(b"\x00\x00\x00\x01" + bytes(body))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    source = sys.argv[1]
    nals = nals_of(open(source, "rb").read())
    pps = next(((index, body) for index, (kind, body) in enumerate(nals) if kind == 8), None)
    if not pps:
        print("no PPS in this stream")
        return 1
    index, body = pps
    print(f"{len(nals)} NAL(s); PPS at {index}: {bytes(body).hex(' ')}")

    work = os.path.join(HERE, "captured", "entropy_test")
    os.makedirs(work, exist_ok=True)
    variants = {}

    original = os.path.join(work, "as-is.h264")
    emit(nals, original)
    variants["as the file has it (CABAC)"] = original

    # pps_id and sps_id are each the single bit 1 (ue(0)), so entropy_coding_mode_flag is the third
    # bit of the second byte: 0x20 set means CABAC.
    def flip_cabac(payload, on):
        payload[1] = (payload[1] | 0x20) if on else (payload[1] & ~0x20)
        return payload

    for label, on in (("CABAC flag cleared (CAVLC)", False), ("CABAC flag set again", True)):
        path = os.path.join(work, label.split(" (")[0].replace(" ", "_") + ".h264")
        emit(nals, path, (index, lambda payload, on=on: flip_cabac(payload, on)))
        variants[label] = path

    for label, path in variants.items():
        stats, errors = decode(path)
        if stats:
            verdict = "FLAT GREY" if stats[1] < 3 else "a picture"
            print(f"  {label:<32} mean={stats[0]:6.1f} sd={stats[1]:6.2f}  {verdict}  "
                  f"({len(errors)} error line(s))")
        else:
            print(f"  {label:<32} no frame  ({len(errors)} error line(s))")
            for line in errors[:2]:
                print(f"      {line[:110]}")
    print(f"\nvariants written under {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
