"""Write down what was produced, with the two facts that matter for each file: its length and whether
its picture is real.

The tooling has been verifying these one file at a time and printing them into logs that scroll away.
A manifest makes the claim checkable afterwards and usable by anything else -- a player, a dashboard,
an MCP call -- and it is also the honest form of "how much of this lesson did you get": seconds that
can be watched, seconds that are correct but grey, and where each file is.

    python make_manifest.py --root D:\\ev-export\\lessons\\119354\\by-session --out D:\\ev-export\\manifest.json
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile


def probe_duration(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float((done.stdout or "").strip())
    except ValueError:
        return None


def picture(path):
    """Luma spread of three frames spread through the file, and the best of them.

    One sample is not enough to judge a mixed file: an assembled video can contain decrypted stretches
    with real pictures and protected stretches that are grey, and a single frame at forty percent can
    land in either. Three samples make the verdict pessimistic only when the file really is grey
    throughout, and the spreads themselves are kept so the judgement can be revisited.
    """
    spreads, means = [], []
    seconds = probe_duration(path) or 0
    with tempfile.TemporaryDirectory() as work:
        for index, fraction in enumerate((0.15, 0.5, 0.85)):
            raw = os.path.join(work, f"f{index}.gray")
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{seconds * fraction:.2f}", "-i", path,
                            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-y", raw],
                           capture_output=True, text=True)
            if not os.path.exists(raw):
                continue
            data = open(raw, "rb").read()
            if len(data) < 1024:
                continue
            means.append(statistics.fmean(data))
            spreads.append(statistics.pstdev(data))
    if not spreads:
        return None
    return max(spreads), spreads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    entries = []
    for root in args.root:
        for directory, _, files in os.walk(root):
            for name in sorted(files):
                if not name.endswith(".mp4"):
                    continue
                path = os.path.join(directory, name)
                seconds = probe_duration(path)
                stats = picture(path)
                best = stats[0] if stats else None
                entries.append({
                    "path": path,
                    "file": name,
                    "session": os.path.basename(os.path.dirname(directory))
                               if os.path.basename(directory) == "119354" else os.path.basename(directory),
                    "seconds": round(seconds, 2) if seconds is not None else None,
                    "megabytes": round(os.path.getsize(path) / 1048576, 2),
                    "picture": (None if best is None else
                                ("picture" if best >= 3 else "flat grey")),
                    "luma_spreads": None if stats is None else [round(v, 2) for v in stats[1]],
                })

    entries.sort(key=lambda entry: -(entry["seconds"] or 0))
    watchable = [e for e in entries if e["picture"] == "picture"]
    grey = [e for e in entries if e["picture"] == "flat grey"]
    summary = {
        "videos": len(entries),
        "seconds_total": round(sum(e["seconds"] or 0 for e in entries), 1),
        "seconds_watchable": round(sum(e["seconds"] or 0 for e in watchable), 1),
        "seconds_flat": round(sum(e["seconds"] or 0 for e in grey), 1),
        "watchable_files": len(watchable),
        "flat_files": len(grey),
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "files": entries}, handle, ensure_ascii=False, indent=1)

    print(f"{summary['videos']} video(s), {summary['seconds_total']:.1f}s total")
    print(f"  watchable : {summary['watchable_files']:>3} file(s), "
          f"{summary['seconds_watchable']:.1f}s ({summary['seconds_watchable'] / 60:.1f} min)")
    print(f"  flat grey : {summary['flat_files']:>3} file(s), {summary['seconds_flat']:.1f}s "
          f"({summary['seconds_flat'] / 60:.1f} min) -- length correct, picture needs the player")
    print(f"\nwritten {args.out}")
    print("\nlongest watchable files:")
    for entry in watchable[:5]:
        print(f"  {entry['seconds']:8.1f}s  {entry['megabytes']:6.1f} MB  {entry['file'][:56]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
