"""Assembles the packets a capture kept into one stream and says whether it holds a picture.

`capture_playback.py` writes each decoded packet as `NNN_<size>.bin` in arrival order. On its own
that is a heap of slices: a mid-GOP capture has no SPS/PPS and decodes to nothing, which is exactly
what the first run of it produced. Concatenating them in order recovers the stream the player fed its
decoder, and the brightness spread says whether that stream is the lesson or a flat grey field.

    python assemble_playback.py [--dir captured/playback] [--keep-sps-from <file.h264>]
"""

import argparse
import os
import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
CAPTURED = HERE / "captured"


def assemble(packets: list[pathlib.Path], out: pathlib.Path) -> int:
    with out.open("wb") as handle:
        for path in packets:
            handle.write(path.read_bytes())
    return out.stat().st_size


def brightness(ffmpeg: str, path: pathlib.Path) -> tuple | None:
    run = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path),
         "-vf", "signalstats,metadata=print", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    values = [float(v) for v in re.findall(r"YAVG=([\d.]+)", run.stderr)]
    if not values:
        return None
    return len(values), sum(values) / len(values), min(values), max(values)


def nal_kinds(path: pathlib.Path) -> dict:
    data = path.read_bytes()
    kinds: dict[int, int] = {}
    for match in re.finditer(b"\x00\x00\x00\x01", data):
        start = match.start() + 4
        if start < len(data):
            kind = data[start] & 0x1F
            kinds[kind] = kinds.get(kind, 0) + 1
    return dict(sorted(kinds.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=pathlib.Path, default=CAPTURED / "playback")
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path(r"D:\Codes\github\ev-reverse\build\cap-merged.h264"))
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    packets = sorted(args.dir.glob("*.bin"), key=lambda p: int(p.name.split("_")[0]))
    if not packets:
        print(f"no packets in {args.dir}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    size = assemble(packets, args.out)
    kinds = nal_kinds(args.out)
    print(f"{len(packets)} packets -> {args.out} ({size/1024:.0f} KB)")
    print(f"NAL kinds: {kinds}")
    print(f"  has SPS(7)={'yes' if 7 in kinds else 'NO'}  PPS(8)={'yes' if 8 in kinds else 'NO'}  "
          f"IDR(5)={kinds.get(5, 0)}  slice(1)={kinds.get(1, 0)}")

    result = brightness(args.ffmpeg, args.out)
    if result is None:
        print("decode: no frames (a mid-GOP capture without SPS/PPS cannot decode)")
        return 0
    frames, mean, low, high = result
    print(f"decode: {frames} frames, YAVG mean {mean:.1f}, min {low:.1f}, max {high:.1f}, "
          f"spread {high - low:.1f}")
    print("  → spread is large: this is a picture. spread ~0: this is a flat field.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
