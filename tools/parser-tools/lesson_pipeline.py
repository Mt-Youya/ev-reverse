"""The whole job as one command: watch the player, collect keys, and keep turning them into video.

Everything needed is already here as separate tools, and the awkward part is only that a key exists
for one moment -- while the player decrypts that segment -- so the way to a whole lesson is to be
watching when it happens rather than to ask the server afterwards. This runs the steps in that order,
repeatedly, and prints what each pass gained:

    harvest   read the keys the player sets up while it plays          (read-only, safe to leave on)
    map       attach each key to the file it opens                     (mask once, AES per key)
    rebuild   mine captured lesson lists, verify every key, report the library
    assemble  decrypt, place every segment on one timeline, merge, and measure each video's length

It is deliberately a loop rather than a single pass: the first pass produces whatever the player has
played so far, and each later pass adds to it. Stop it whenever; the library persists.

    python lesson_pipeline.py --lesson 119354 --minutes 60 --cycle 10
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(tool, args, label):
    started = time.time()
    done = subprocess.run([PY, os.path.join(HERE, tool)] + args, capture_output=True, text=True)
    took = time.time() - started
    tail = [line for line in (done.stdout or "").splitlines() if line.strip()][-4:]
    print(f"\n--- {label} ({took:.0f}s, exit {done.returncode})")
    for line in tail:
        print(f"    {line}")
    if done.returncode != 0:
        for line in (done.stderr or "").splitlines()[-3:]:
            print(f"    ! {line}")
    return done.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="119354")
    ap.add_argument("--minutes", type=float, default=60, help="0 = one pass and stop")
    ap.add_argument("--cycle", type=float, default=10, help="minutes of harvesting per pass")
    ap.add_argument("--out", default=r"D:\ev-export\lessons")
    args = ap.parse_args()

    deadline = time.time() + args.minutes * 60 if args.minutes else None
    passes = 0
    while True:
        passes += 1
        print(f"\n================ pass {passes} ================", flush=True)
        seconds = int(args.cycle * 60)
        if deadline:
            seconds = int(min(seconds, max(30, deadline - time.time())))
        run("harvest_player.py", ["--follow", "--seconds", str(seconds)], "harvest")
        run("map_keys.py", ["--recent-hours", "12"], "map")
        run("key_inventory.py", [], "rebuild the library")
        run("decrypt_lesson.py", ["--lesson", args.lesson, "--session", "all",
                                  "--out", args.out], "assemble")
        if not deadline or time.time() >= deadline:
            break

    print(f"\n{passes} pass(es) done; library and videos are in "
          f"{os.path.join(HERE, 'captured')} and {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
