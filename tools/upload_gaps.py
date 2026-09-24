"""Read verify_fresh/upload-status/courses.json and say what is still not uploaded.

The status page answers "where is each lesson" one course at a time. This answers the
queue question: given everything on disk right now, what is still missing on each
platform, and for the things that cannot be uploaded at all, why not.

Four buckets come out of it:

  * uploadable now   -- on disk and absent from a platform: what the uploaders will send
  * needs an export  -- absent from a platform and not on disk either, so the lesson has to be
                        exported from the course platform first. Two routes, and
                        export_missing.py picks between them: download it, or publish the merged
                        lesson.ts that is already sitting in work/;
  * never uploaded   -- no local file and no mapping on either platform: nothing about the
                        course exists anywhere yet
  * blocked          -- the platform answer is unknown (null), because no prefix or 稿件 was
                        ever established for that course; absence is not a fact there

A lesson whose platform field is null is reported as unknown, never as missing.

    python upload_gaps.py [--json] [--limit N]
"""

import argparse
import json
import pathlib
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
COURSES = REPO / "verify_fresh" / "upload-status" / "courses.json"


def load() -> dict:
    return json.loads(COURSES.read_text(encoding="utf-8"))


def gigabytes(total: int) -> float:
    return total / 1024 ** 3


def rows_for(courses: list, keep) -> list:
    """[(course, [episodes])] for the episodes a predicate selects."""
    found = []
    for course in courses:
        picked = [e for e in course["episodes"] if keep(e)]
        if picked:
            found.append((course, picked))
    return found


def uploadable(courses: list, field: str) -> list:
    return rows_for(courses, lambda e: e["local"] and e[field] is False)


def needs_export(courses: list, field: str) -> list:
    return rows_for(courses, lambda e: not e["local"] and e[field] is False)


def never_uploaded(courses: list) -> list:
    """Courses with no local file and no home on either platform.

    `counts.r2` is a count of lessons present, not a presence flag, and it is never null --
    the "no mapping" signal is an empty `r2_prefix` and an empty 稿件 list.
    """
    return [c for c in courses
            if not c["counts"]["local"] and c["r2_prefix"] is None and not c["bilibili_ids"]]


def blocked(courses: list, field: str) -> list:
    """Courses where that platform's answer is unknown, so absence cannot be claimed."""
    if field == "r2":
        return [c for c in courses if c["r2_prefix"] is None]
    return [c for c in courses if not c["bilibili_ids"]]


def total(rows: list) -> tuple:
    return (sum(len(m) for _c, m in rows),
            sum(e["size_bytes"] for _c, m in rows for e in m))


def table(title: str, rows: list, target) -> None:
    print()
    print(f"=== {title} ===")
    if not rows:
        print("  nothing")
        return
    files, size = total(rows)
    print(f"  {files} files / {gigabytes(size):.2f} GB across {len(rows)} courses")
    print()
    print(f"  {'course':<34} {'files':>5}  {'GB':>7}  target")
    for course, missing in sorted(rows, key=lambda r: -sum(e["size_bytes"] for e in r[1])):
        where = target(course)
        print(f"  {course['name'][:34]:<34} {len(missing):>5}  "
              f"{gigabytes(sum(e['size_bytes'] for e in missing)):>7.2f}  {str(where)[:56]}")


def markdown(document: dict, r2_open: list, bili_open: list, r2_export: list,
             bili_export: list, nowhere: list) -> str:
    """The same four buckets as a document, plus the blockers that need a person.

    The last section is written by hand because no query produces it: a lesson that cannot be
    exported at all, two 稿件s only Bilibili's own editor can fix. Each entry names where the
    evidence is, so a reader can check instead of trusting.
    """
    lines = [
        "# 尚未上传的课程与原因",
        "",
        f"生成时间 {time.strftime('%Y-%m-%d %H:%M')}，数据来自 `courses.json`"
        f"（生成于 {document['generated_at']}）。"
        f"共 {len(document['courses'])} 门课 / {document['totals']['episodes']} 集。",
        "",
        "「可上传」= 本地有成片、平台上还没有；上传由看门狗每 20 分钟自动跑一次。"
        "「需先导出」= 本地没有成片，得先从课程平台导出（可下载，或直接发布 work/ 里已合并的缓存）。",
        "",
        "## 可以直接上传（本地已有成片）",
        "",
    ]

    def table(rows: list, target) -> list:
        out = ["| 课程 | 文件数 | 源大小 | 目标 |", "| --- | ---: | ---: | --- |"]
        for course, missing in sorted(rows, key=lambda r: -sum(e["size_bytes"] for e in r[1])):
            size = sum(e["size_bytes"] for e in missing) / 1024 ** 3
            out.append(f"| {course['name']} | {len(missing)} | {size:.2f} GB | "
                       f"`{target(course)}` |")
        return out if len(out) > 2 else ["（无）"]

    lines += ["**R2**", ""]
    lines += table(r2_open, lambda c: c["r2_prefix"])
    lines += ["", "**Bilibili**", ""]
    lines += table(bili_open, lambda c: ",".join(c["bilibili_ids"]) or "(还没有稿件)")

    lines += ["", "## 需要先导出（本地没有成片）", ""]
    lines += ["**缺在 R2**", ""]
    lines += table(r2_export, lambda c: c["r2_prefix"])
    lines += ["", "**缺在 Bilibili**", ""]
    lines += table(bili_export, lambda c: ",".join(c["bilibili_ids"]) or "(还没有稿件)")

    episodes = sum(c["episode_count"] for c in nowhere)
    size = sum(c["size_bytes"] for c in nowhere) / 1024 ** 3
    lines += [
        "", "### 从未导出过（两个平台都没有任何记录）", "",
        f"{len(nowhere)} 门课 / {episodes} 集 / 源文件合计 {size:.1f} GB。"
        "这些课**从来没有下载过**：本地没有成片、R2 没有前缀、B站 没有稿件，"
        "所以不是上传失败，而是还没走到上传。",
        "",
        "| 课程 | 集数 | 源大小 |",
        "| --- | ---: | ---: |",
    ]
    for course in sorted(nowhere, key=lambda c: -c["episode_count"]):
        lines.append(f"| {course['name']} | {course['episode_count']} | "
                     f"{course['size_bytes'] / 1024 ** 3:.2f} GB |")

    lines += [
        "", "## 需要人工处理的（工具做不到）", "",
        "* **`前端工具链` / `01. ESLint介绍` —— 导出失败，不是上传失败。** "
        "音轨里有 1 帧 AAC 截断（131,496 帧中 1 帧），发布前的严格音视频校验不通过，"
        "导出器拒绝出片；两次独立下载都复现。两条出路（接受已 remux 的成品 / 只重编码音轨）"
        "都需要人来定：见 `docs/EXPORT-PIPELINE.md` §5。",
        "* **`工程管理实战` 有 2 条重复分P**（`35. [测试体系]单元测试.mp4`，三条同长 2281 秒）。"
        "删除接口 404，编辑接口要整份 archive 载荷，只能人工：见 "
        "`.scratch/bilibili-upload/issues/02-delete-the-duplicate-unit-test-parts.md`。",
        "* **`前端工具链` 第 1 分P 名被改成了合集名**（应为 `01. 课程概述`），"
        "内容其实早就在稿件里。人工改名后请删掉 `bili_upload.PART_ALIASES` 里的别名：见 "
        "`.scratch/bilibili-upload/issues/01-fix-the-toolchain-part-order.md`。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true",
                        help="emit the same findings as JSON instead of tables")
    parser.add_argument("--limit", type=int, default=0,
                        help="with --json, cap the per-course episode lists")
    parser.add_argument("--markdown", type=pathlib.Path,
                        help="write the findings as a markdown document, so the answer to "
                             "'what is still missing and why' is a file rather than a chat "
                             "message")
    args = parser.parse_args()

    document = load()
    courses = document["courses"]
    r2_open = uploadable(courses, "r2")
    bili_open = uploadable(courses, "bilibili")
    r2_download = needs_export(courses, "r2")
    bili_download = needs_export(courses, "bilibili")
    nowhere = never_uploaded(courses)

    if args.markdown:
        args.markdown.write_text(
            markdown(document, r2_open, bili_open, r2_download, bili_download, nowhere),
            encoding="utf-8")
        print(f"wrote {args.markdown}")
        return 0

    if args.json:
        def as_rows(rows, cap):
            return [{"course": c["name"], "files": len(m),
                     "bytes": sum(e["size_bytes"] for e in m),
                     "episodes": [{"title": e["title"], "size_bytes": e["size_bytes"]}
                                  for e in (m[:cap] if cap else m)]}
                    for c, m in rows]
        print(json.dumps({
            "generated_at": document["generated_at"],
            "courses": len(courses),
            "episodes": document["totals"]["episodes"],
            "uploadable_now": {"r2": as_rows(r2_open, args.limit),
                               "bilibili": as_rows(bili_open, args.limit)},
            "needs_export": {"r2": as_rows(r2_download, args.limit),
                               "bilibili": as_rows(bili_download, args.limit)},
            "never_uploaded": [{"course": c["name"], "episodes": c["episode_count"]}
                               for c in nowhere],
            "unknown_mapping": {
                "r2": [c["name"] for c in blocked(courses, "r2")],
                "bilibili": [c["name"] for c in blocked(courses, "bilibili")]},
        }, ensure_ascii=False, indent=1))
        return 0

    print(f"courses.json generated_at {document['generated_at']}")
    print(f"  {len(courses)} courses / {document['totals']['episodes']} episodes")
    for field, label in (("r2", "R2"), ("bilibili", "Bilibili")):
        files, size = total(r2_open if field == "r2" else bili_open)
        more, more_size = total(r2_download if field == "r2" else bili_download)
        print(f"  {label:<9} uploadable {files:>4} files / {gigabytes(size):>6.2f} GB"
              f"   ·   needs an export {more:>3} files / {gigabytes(more_size):>5.2f} GB")

    table("uploadable now: on R2", r2_open, lambda c: c["r2_prefix"])
    table("uploadable now: on Bilibili", bili_open,
          lambda c: ",".join(c["bilibili_ids"]) or "(no 稿件 yet)")
    table("needs an export first: missing on R2", r2_download, lambda c: c["r2_prefix"])
    table("needs an export first: missing on Bilibili", bili_download,
          lambda c: ",".join(c["bilibili_ids"]) or "(no 稿件 yet)")

    print()
    print(f"=== never uploaded anywhere: {len(nowhere)} courses, "
          f"{sum(c['episode_count'] for c in nowhere)} episodes ===")
    print("  no local file and no R2 prefix or 稿件: the course was never exported, so there "
          "is nothing to upload")
    print()
    print(f"  {'course':<40} {'episodes':>8}")
    for course in sorted(nowhere, key=lambda c: -c["episode_count"]):
        print(f"  {course['name'][:40]:<40} {course['episode_count']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
