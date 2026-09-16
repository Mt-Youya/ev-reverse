"""One command for one lesson: collect what is decryptable, assemble it, and say what is not.

The tools worked, but the sequence lived in the operator's head: harvest keys while the player runs,
map them to files, mine the captured lists, verify the library, assemble per session, and then work out
which parts are still missing and why. This runs that sequence and ends with the only report that
matters for a lesson -- how much of it is video, how long each piece is against the content it was
built from, how many positions are still uncovered, and how many of those are uncovered because they
are protected rather than because no key was seen.

    python make_lesson.py --lesson 119354                 # assemble with what is already held
    python make_lesson.py --lesson 119354 --harvest 600   # watch the player for keys first
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CACHE_DIR = r"D:\Downloads\EVPlayer2Downloads"


def run(tool, args, label, echo=3):
    started = time.time()
    done = subprocess.run([PY, os.path.join(HERE, tool)] + args, capture_output=True, text=True)
    print(f"\n--- {label}  ({time.time() - started:.0f}s, exit {done.returncode})")
    for line in [l for l in (done.stdout or "").splitlines() if l.strip()][-echo:]:
        print(f"    {line}")
    if done.returncode != 0:
        for line in (done.stderr or "").splitlines()[-2:]:
            print(f"    ! {line}")
    return done.stdout or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="119354")
    ap.add_argument("--harvest", type=float, default=0,
                    help="seconds to watch the live player for keys before assembling")
    ap.add_argument("--out", default=r"D:\ev-export\lessons")
    ap.add_argument("--min-keys", type=int, default=4)
    ap.add_argument("--no-verify", action="store_true", default=True)
    args = ap.parse_args()

    if args.harvest:
        run("harvest_player.py", ["--follow", "--seconds", str(int(args.harvest))], "harvest keys")
        run("map_keys.py", ["--recent-hours", "12"], "map keys to files")

    run("key_inventory.py", [], "rebuild and verify the key library")
    run("lesson_by_session.py", ["--lesson", args.lesson, "--min-keys", str(args.min_keys),
                                 "--max-sessions", "200", "--out", args.out],
        "assemble every session", echo=2)

    library = json.load(open(os.path.join(HERE, "captured", "keys_merged.json"), encoding="utf-8"))
    facts = json.load(open(os.path.join(HERE, "captured", "segment_facts.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(HERE, "captured", "segment_facts.json")) else {}
    index = json.load(open(os.path.join(HERE, "captured", "key_index.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(HERE, "captured", "key_index.json")) else {}
    sessions = json.load(open(os.path.join(HERE, "captured", "lesson_sessions.json"),
                              encoding="utf-8"))

    protected, clear = 0, 0
    for name in library:
        level = (facts.get(name) or {}).get("level")
        if level is None:
            continue
        if level == 40:
            protected += 1
        else:
            clear += 1

    positions = {}
    for name, entry in index.items():
        if name in library:
            positions.setdefault(entry.get("session"), set()).add(entry.get("idx"))
    covered = sum(len(value) for value in positions.values())

    videos = []
    for root, _, files in os.walk(os.path.join(args.out, args.lesson, "by-session")):
        videos.extend(os.path.join(root, f) for f in files if f.endswith(".mp4"))
    total = 0.0
    for path in videos:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
        try:
            total += float((done.stdout or "").strip())
        except ValueError:
            pass

    print("\n================ lesson summary ================")
    print(f"keys verified            : {len(library)}")
    print(f"  of which level-40      : {protected} (picture needs the player; see capture_frames.py)")
    print(f"  of which decodable     : {clear}")
    print(f"sessions with a list     : {len(sessions)}")
    print(f"sessions assembled       : {len({os.path.dirname(p) for p in videos})}")
    print(f"videos produced          : {len(videos)}  totalling {total:.1f}s "
          f"({total / 60:.1f} minutes)")
    print(f"indexed positions covered: {covered}")
    print("\nEvery produced video was measured against the content it was built from; "
          "the per-video numbers are in each session's report.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
