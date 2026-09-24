"""Look for 稿件s belonging to courses the tables do not map.

The status page reports a course as "never uploaded anywhere" when no prefix and no 稿件 is
recorded for it. That conclusion is only as good as the table, and 稿件 titles are not
reliable identifiers -- `Python语言核心精讲`'s 稿件 is titled `Python语言基础`. So before
believing "nothing of this course is on Bilibili", look through every 稿件 on the account
for one whose title resembles the course, or whose part titles do.

    python find_orphan_archives.py            # every course with no local file
    python find_orphan_archives.py --check    # resolve the part titles of the near misses
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
COURSES = REPO / "verify_fresh" / "upload-status" / "courses.json"
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw  # noqa: E402


def norm(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"(\.(mp4|mkv|ts))+$", "", str(text).strip(), flags=re.I))


def archives() -> list:
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "list", "--max-pages", "60"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = []
    for line in result.stdout.splitlines():
        if not line.startswith("BV"):
            continue
        fields = line.split("\t")
        if len(fields) >= 2:
            rows.append((fields[0], fields[1].strip()))
    if not rows:
        # The whole point of this tool is to say "no 稿件 of that course exists", and an empty
        # listing cannot support that claim: Bilibili answers the archives endpoint with HTTP 412
        # when it thinks the caller asks too often, and biliup turns that into an error message on
        # stderr with nothing on stdout.
        raise SystemExit("biliup list returned no 稿件 at all (HTTP 412 from Bilibili?); "
                         "an empty listing proves nothing about what the account holds")
    return rows


def parts(bv: str) -> set:
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    brace = result.stdout.find("{")
    if brace < 0:
        return set()
    try:
        data = json.loads(result.stdout[brace:])
    except json.JSONDecodeError:
        return set()
    return {norm(v.get("title", "")) for v in data.get("videos", [])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="read the part titles of the 稿件s whose name is close, to compare "
                             "against the course's lessons")
    args = parser.parse_args()

    document = json.loads(COURSES.read_text(encoding="utf-8"))
    unmapped = [c for c in document["courses"] if c["r2_prefix"] is None or not c["bilibili_ids"]]
    listed = archives()
    print(f"{len(listed)} 稿件(s) on the account; "
          f"{len(unmapped)} course(s) with no id recorded\n")

    known = {bv for bvs in cw.BILI_BY_COLLECTION.values() for bv in bvs}
    suspicious = []
    for course in unmapped:
        name = norm(course["name"])
        titles = {norm(e["title"]) for e in course["episodes"]}
        for bv, title in listed:
            if bv in known:
                continue
            flat = norm(title)
            # A 稿件 titled after the course, or whose title is a prefix of it (Python语言基础 vs
            # Python语言核心精讲), or that shares a long run of the course's own name.
            if flat and (flat in name or name in flat or
                         len(set(flat) & set(name)) / max(1, len(set(flat) | set(name))) > 0.7):
                suspicious.append((course["name"], bv, title, titles))
                break

    if not suspicious:
        print("no 稿件 outside the table looks like one of those courses")
        return 0

    for course, bv, title, titles in suspicious:
        print(f"{course:<26} {bv}  {title[:48]}")
        if args.check:
            theirs = parts(bv)
            shared = titles & theirs
            print(f"    {len(shared)} of the course's {len(titles)} lesson titles are parts of it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
