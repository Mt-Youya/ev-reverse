"""Which collections do R2 and Bilibili disagree about?

`courses.json` records, per lesson, whether R2 holds it and whether a 稿件 part matches it. This
reads that and prints every collection where the two platforms do not cover exactly the same
lessons -- the interesting case is not a count mismatch but a lesson present on one side only,
because a count can agree while the sets differ.

    python align-r2-bili.py [--verbose]
"""

import json
import pathlib
import sys

COURSES = pathlib.Path(r"D:\Codes\github\ev-reverse\verify_fresh\upload-status\courses.json")

data = json.loads(COURSES.read_text(encoding="utf-8"))
courses = data["courses"] if isinstance(data, dict) else data

verbose = "--verbose" in sys.argv
rows = []
for course in courses:
    lessons = course.get("lessons") or course.get("episodes") or []
    if not lessons:
        continue
    r2 = sum(1 for x in lessons if x.get("r2"))
    bili = sum(1 for x in lessons if x.get("bilibili"))
    only_r2 = [x for x in lessons if x.get("r2") and not x.get("bilibili")]
    only_bili = [x for x in lessons if x.get("bilibili") and not x.get("r2")]
    if only_r2 or only_bili:
        rows.append((course.get("title") or course.get("name"), len(lessons), r2, bili,
                     only_r2, only_bili))

if not rows:
    total_r2 = sum(1 for c in courses for x in (c.get("lessons") or []) if x.get("r2"))
    total_bili = sum(1 for c in courses for x in (c.get("lessons") or []) if x.get("bilibili"))
    print(f"aligned: no lesson is on one platform only ({total_r2} on R2, {total_bili} on Bilibili)")
    raise SystemExit(0)

print(f"{len(rows)} collection(s) where the two platforms disagree:\n")
for title, total, r2, bili, only_r2, only_bili in rows:
    print(f"{title}: {total} lesson(s), R2 {r2}, Bilibili {bili}")
    for label, items in (("只 R2", only_r2), ("只 B站", only_bili)):
        for item in items[:6]:
            print(f"    {label}: {item.get('title') or item.get('name')}")
        if len(items) > 6:
            print(f"    {label}: ... and {len(items) - 6} more")
    print()

raise SystemExit(1)
