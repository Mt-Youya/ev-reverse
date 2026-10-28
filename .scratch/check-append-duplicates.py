"""Check an append candidate by duration before sending it.

`bili_upload.py` decides what is missing by comparing part TITLES, and 前端工具链 proved that a
part can carry the wrong title while holding the right content: 稿件 part 1 is named
`01. ESLint介绍` but is 2342s, the same video as the local `01. 课程概述.mp4` at 2341.4s. A pass
that trusted the titles appended it and the 稿件 grew a duplicate lesson.

So before appending, compare durations. A local file whose length matches a part already in the
稿件 -- within a second, which is Bilibili's own rounding -- is that part under another name.

    python check-append-duplicates.py Electron "React源码分析" "文档协同" "运维自动化项目实战"
"""

import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
VIDEO = (".mp4", ".mkv", ".ts")

sys.path.insert(0, str(REPO / "tools"))
import bili_upload as bu  # noqa: E402

TOLERANCE = 3  # seconds; the 稿件 reports whole seconds and ffprobe reports fractions


def ffprobe_seconds(path: pathlib.Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def parts(bv: str):
    raw = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    brace = raw.find("{")
    if brace < 0:
        return []
    return json.loads(raw[brace:]).get("videos", [])


def main() -> int:
    collections = sys.argv[1:]
    if not collections:
        print(__doc__)
        return 2

    entries = dict(bu.COLLECTIONS)
    duplicates = 0
    for title in collections:
        rel = next((k for k in entries if pathlib.PurePosixPath(k).name == title), None)
        if not rel:
            print(f"{title}: not in bili_upload.COLLECTIONS")
            continue
        folder = OUT / rel
        videos = sorted(p for p in folder.rglob("*")
                        if p.is_file() and p.suffix.lower() in VIDEO)
        index = bu.archive_index({title})
        entry = index.get(title)
        if not entry:
            print(f"{title}: no 稿件 found (would be created new)")
            continue
        bv = entry["bv"]
        online = parts(bv)
        lengths = [(int(v.get("duration") or 0), v.get("title")) for v in online]
        print(f"=== {title}  -> {bv}  ({len(videos)} local, {len(online)} parts online)")

        for path in videos:
            local = ffprobe_seconds(path)
            alias = bu.PART_ALIASES.get(title, {}).get(path.name)
            if bu.norm(path.name) in {bu.norm(v.get("title") or "") for v in online} or \
               (alias and bu.norm(alias) in {bu.norm(v.get("title") or "") for v in online}):
                continue  # title already matches; the uploader would skip it anyway
            hit = next(((d, t) for d, t in lengths if abs(d - local) <= TOLERANCE), None)
            if hit:
                duplicates += 1
                print(f"   DUPLICATE  {path.name}  {local:.0f}s  == part {hit[1]!r} ({hit[0]}s)")
            else:
                print(f"   missing    {path.name}  {local:.0f}s")

    print()
    print(f"{duplicates} file(s) are already in their 稿件 under another title -- appending them "
          f"would duplicate the lesson")
    return 1 if duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())
