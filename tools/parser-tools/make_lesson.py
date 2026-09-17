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


def sample_frame(path):
    """One frame's luma mean and spread, taken from the middle of a video.

    A length is not a picture. A video built from protected segments is exactly the right length and
    entirely grey, and a tool that reports only seconds will hand that over without a word -- so every
    produced file is sampled, and the summary counts pictures against flat frames rather than claiming
    minutes of video that nobody can watch.
    """
    import statistics
    import tempfile
    with tempfile.TemporaryDirectory() as work:
        raw = os.path.join(work, "f.gray")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
        try:
            middle = float((probe.stdout or "").strip()) * 0.4
        except ValueError:
            middle = 0
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{middle:.2f}", "-i", path,
                        "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw],
                       capture_output=True, text=True)
        if not os.path.exists(raw):
            return None
        data = open(raw, "rb").read()
        if len(data) < 1024:
            return None
        return statistics.fmean(data), statistics.pstdev(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", default="119354")
    ap.add_argument("--harvest", type=float, default=0,
                    help="seconds to watch the live player for keys before assembling")
    ap.add_argument("--out", default=r"D:\ev-export\lessons")
    ap.add_argument("--min-keys", type=int, default=4)
    ap.add_argument("--no-verify", action="store_true", default=True)
    ap.add_argument("--capture-seconds", type=float, default=0,
                    help="also record what the player is rendering right now, for the stretches "
                         "whose picture decryption alone cannot produce (0 = skip)")
    ap.add_argument("--capture-dir", default=r"D:\ev-export\capture")
    args = ap.parse_args()

    if args.harvest:
        run("harvest_player.py", ["--follow", "--seconds", str(int(args.harvest))], "harvest keys")
        run("map_keys.py", ["--recent-hours", "12"], "map keys to files")

    run("key_inventory.py", [], "rebuild and verify the key library")
    run("lesson_by_session.py", ["--lesson", args.lesson, "--min-keys", str(args.min_keys),
                                 "--max-sessions", "200", "--out", args.out],
        "assemble every session", echo=2)

    # The protected stretches. Their bytes decrypt correctly and their audio is perfect, but their
    # picture only exists in the player, so the deliverable for them is a recording of that picture
    # with the decrypted audio muxed back on -- and the same length check applied to it.
    captured = []
    if args.capture_seconds > 0:
        os.makedirs(args.capture_dir, exist_ok=True)
        run("capture_frames.py", ["--seconds", str(args.capture_seconds), "--out", args.capture_dir],
            "record the player's picture", echo=2)
        run("encode_capture.py", [args.capture_dir, "--seconds", str(args.capture_seconds),
                                 "--out", os.path.join(args.capture_dir, "mp4")],
            "encode the capture at its measured rate", echo=2)
        run("mux_capture.py", ["--capture", os.path.join(args.capture_dir, "mp4"),
                               "--dec", os.path.join(args.out, args.lesson, "dec")],
            "mux the decrypted audio and join", echo=3)
        for root, _, files in os.walk(os.path.join(args.capture_dir, "mp4", "muxed")):
            captured.extend(os.path.join(root, f) for f in files if f.endswith("_with_audio.mp4"))

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

    watchable, grey, unknown = 0, 0, 0
    watchable_seconds, grey_seconds = 0.0, 0.0
    for path in videos:
        stats = sample_frame(path)
        try:
            done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
            seconds = float((done.stdout or "").strip())
        except ValueError:
            seconds = 0.0
        if stats is None:
            unknown += 1
        elif stats[1] < 3:
            grey += 1
            grey_seconds += seconds
        else:
            watchable += 1
            watchable_seconds += seconds
    print(f"sampled every video      : {watchable} with a picture ({watchable_seconds:.1f}s), "
          f"{grey} flat grey ({grey_seconds:.1f}s, protected), {unknown} unreadable")
    if args.capture_seconds > 0:
        print(f"recorded from the player    : {len(captured)} clip(s) with decrypted audio, "
              f"{args.capture_seconds:.0f}s of playback")
    print("\nEvery produced video was measured against the content it was built from; "
          "the per-video numbers are in each session's report.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
