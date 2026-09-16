"""Self-test for the per-file capture rate, without needing the player to be rendering anything.

The defect this guards against was invisible until two clips of very different speeds were joined: a
single rate for the whole capture made a 1.8 fps batch encode as if it were 10 fps, and every duration
derived from it was wrong. So the rate is now recorded per file, and this checks that the encoder uses
it -- with a synthetic clip whose frame count and wall-clock span are known exactly, so the expected
length is arithmetic rather than opinion.

    python verify_capture_rate.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WIDTH, HEIGHT, FRAMES, SPAN = 320, 240, 20, 10.0          # 20 frames over 10 seconds = 2 fps


def main():
    work = tempfile.mkdtemp(prefix="rate-check-")
    try:
        frame_bytes = WIDTH * HEIGHT * 3 // 2
        clip = os.path.join(work, "stream_100.yuv")
        with open(clip, "wb") as handle:
            for index in range(FRAMES):
                handle.write(bytes([(index * 12) % 256]) * frame_bytes)
        with open(os.path.join(work, "capture_index.json"), "w", encoding="utf-8") as handle:
            json.dump([{"name": clip, "frames": FRAMES, "first": 1000.0,
                        "last": 1000.0 + SPAN}], handle)

        out = os.path.join(work, "mp4")
        done = subprocess.run([sys.executable, os.path.join(HERE, "encode_capture.py"), work,
                               "--seconds", str(SPAN), "--width", str(WIDTH), "--height", str(HEIGHT),
                               "--out", out], capture_output=True, text=True)
        print((done.stdout or "").strip()[-400:])
        target = os.path.join(out, "stream_100.mp4")
        if not os.path.exists(target):
            print("FAIL: nothing was encoded")
            return 1
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", target], capture_output=True, text=True)
        actual = float((probe.stdout or "0").strip())
        expected = FRAMES / (FRAMES / SPAN)               # frames / rate == the clip's own span
        print(f"\nframes {FRAMES} over {SPAN:g}s  ->  expected {expected:.2f}s, got {actual:.2f}s")
        ok = abs(actual - expected) < 0.2
        print("PASS: the clip's own rate was used" if ok
              else "FAIL: the length does not follow the clip's own timing")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
