"""Emit the course-status data as JSON, with a JSON Schema describing it.

The page that displays this reads the JSON at load time, so refreshing the numbers never means
editing HTML -- only regenerating the data.

Where each number comes from:

  * index.json      -- the account's own course list: every course, every file, with file_id,
                       title, duration and size. This is the only source that contains courses
                       that were never exported, which is why it is the spine of the document
                       rather than a walk of the export tree.
  * verify_fresh/out -- what is on disk.
  * R2 / Bilibili   -- object names under the course's prefix, and part titles of its 稿件.

Per episode, a platform is reported as `true`, `false`, or `null`. `null` means "not known"
rather than "not there": a course with no prefix established yet has no R2 answer, and writing
`false` for it would read as a fact about the upload.

    python courses_json.py [--out-dir <dir>]
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
INDEX = REPO / "verify_fresh" / "index.json"
CATALOG = REPO / "verify_fresh" / "catalog.json"
DEFAULT_DIR = REPO / "verify_fresh" / "upload-status"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".ts")

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw          # noqa: E402  (verified prefix / 稿件 tables)
import upload_to_r2              # noqa: E402


def norm(title: str) -> str:
    t = re.sub(r"\.(mp4|mkv|ts)$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", t)


def catalog_roots() -> dict:
    """course title -> root category title, for labelling each course."""
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    found = {}

    def walk(node, root):
        if node.get("title"):
            found.setdefault(node["title"], root)
        for child in node.get("children", []):
            if child.get("kind") != "video":
                walk(child, root)

    for root in value["roots"]:
        walk(root, root["title"])
    return found


def local_titles(folder: pathlib.Path) -> set:
    if not folder.is_dir():
        return set()
    return {norm(p.name) for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES}


def bili_titles(bvs) -> set:
    titles = set()
    for bv in bvs:
        for attempt in range(3):
            result = subprocess.run([str(cw.BILIUP), "-u", str(cw.COOKIES), "show", bv],
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            brace = result.stdout.find("{")
            if brace >= 0:
                try:
                    data = json.loads(result.stdout[brace:])
                except json.JSONDecodeError:
                    data = None
                if data is not None:
                    titles |= {norm(v.get("title", "")) for v in data.get("videos", [])}
                    break
            time.sleep(2 * (attempt + 1))
        else:
            raise SystemExit(f"could not read {bv}; refusing to write a table that calls it empty")
    return titles


def episode_order(title: str):
    """Sort key from the lesson's own number, not from order_num.

    order_num comes back descending from the platform (998 for the first file of a course), so
    sorting by it put every episode list in reverse -- 26 down to 01. Two title shapes occur:
    "01. Web服务框架" and "1-2. 前端自动化测试", the second numbering lessons inside a chapter,
    so both parts are kept. Anything unnumbered sorts last instead of being dropped among the
    numbered ones.
    """
    text = str(title or "").strip()
    chapter = re.match(r"^(\d+)\s*[-–—]\s*(\d+)", text)
    if chapter:
        return (int(chapter.group(1)), int(chapter.group(2)), 0, text)
    simple = re.match(r"^(\d+)", text)
    if simple:
        return (int(simple.group(1)), 0, 0, text)
    return (10 ** 6, 0, 0, text)


def name_map() -> dict:
    """collection name -> (rel path, R2 prefix, 稿件 ids), from the verified tables."""
    out = {}
    for rel, prefix in cw.R2_BY_COLLECTION.items():
        out.setdefault(pathlib.PurePosixPath(rel).name, {"rel": rel, "prefix": prefix, "bvs": []})
        out[pathlib.PurePosixPath(rel).name]["prefix"] = prefix
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        nm = pathlib.PurePosixPath(rel).name
        entry = out.setdefault(nm, {"rel": rel, "prefix": None, "bvs": []})
        entry["rel"] = rel
        entry["bvs"] = bvs
    return out


def course_files(detail) -> list:
    files = []

    def gather(node):
        files.extend(node.get("files", []))
        for child in node.get("childs", []):
            gather(child)

    gather(detail)
    return [f for f in files if f.get("type") == "video"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_DIR)
    args = parser.parse_args()

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    roots = catalog_roots()
    s3 = upload_to_r2.client(upload_to_r2.credentials())

    r2_cache = {}
    for prefix in sorted(set(cw.R2_BY_COLLECTION.values())):
        r2_cache[prefix] = {norm(k.rsplit("/", 1)[-1])
                            for k, (size, _e) in
                            upload_to_r2.list_remote(s3, "cyrus-media-private", prefix).items()
                            if size > 0 and k.lower().endswith(VIDEO_SUFFIXES)}
    bili_cache = {rel: bili_titles(bvs) for rel, bvs in cw.BILI_BY_COLLECTION.items()}
    mappings = name_map()

    # local folder lookup: course name -> path under out/
    local_by_name = {}
    for path in OUT.rglob("*"):
        if path.is_dir() and any(p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
                                 for p in path.iterdir()):
            local_by_name.setdefault(path.name, path)

    courses = []
    for cid, record in index["courses"].items():
        detail = record["course_detail"]
        self_ = detail["self"]
        name = self_["name"]
        files = course_files(detail)

        # A course in the platform's list may be one collection or several: WebGIS课程 is a
        # single course of 220 lessons whose chapters are the ten collections tracked
        # separately here. So each file is attributed to the deepest chapter that has a
        # mapping, and only falls back to the course name itself.
        units = []

        def attribute(node, inherited):
            # a chapter carries its name at self.name, not at the top level
            own = (node.get("self") or {}).get("name") or node.get("title")
            current = mappings[own] if own in mappings else inherited
            for f in node.get("files", []):
                if f.get("type") == "video":
                    units.append((f, current))
            for child in node.get("childs", []):
                attribute(child, current)

        attribute(detail, mappings.get(name))
        rel = next((u["rel"] for _f, u in units if u), None)
        prefixes = {u["prefix"] for _f, u in units if u and u["prefix"]}
        all_bvs = sorted({bv for _f, u in units if u for bv in u["bvs"]})
        r2_titles = set().union(*[r2_cache[p] for p in prefixes]) if prefixes else None
        bili_parts = set().union(*[bili_cache[r] for r in
                                   {u["rel"] for _f, u in units if u} if r in bili_cache]) \
            if any(u and u["rel"] in bili_cache for _f, u in units) else None

        folder = local_by_name.get(name)
        local = local_titles(folder) if folder else set()

        episodes = []
        for f in sorted(files, key=lambda x: episode_order(x.get("title"))):
            key = norm(f.get("title", ""))
            episodes.append({
                "file_id": f.get("file_id"),
                "title": str(f.get("title", "")).removesuffix(".mp4"),
                "duration_seconds": int(f.get("duration") or 0),
                "size_bytes": int(f.get("size") or 0),
                "local": key in local,
                "r2": (key in r2_titles) if r2_titles is not None else None,
                "bilibili": (key in bili_parts) if bili_parts is not None else None,
            })

        def count(field, value):
            return sum(1 for e in episodes if e[field] == value)

        both = sum(1 for e in episodes if e["r2"] and e["bilibili"])
        courses.append({
            "id": int(cid),
            "name": name,
            "category": roots.get(name) or (str(pathlib.PurePosixPath(rel).parent)
                                            if rel else None),
            "local_path": str(folder.relative_to(OUT)).replace("\\", "/") if folder else None,
            "r2_prefix": sorted(prefixes)[0] if len(prefixes) == 1 else (sorted(prefixes) or None),
            "bilibili_ids": all_bvs,
            "episode_count": len(episodes),
            "duration_seconds": sum(e["duration_seconds"] for e in episodes),
            "size_bytes": sum(e["size_bytes"] for e in episodes),
            "counts": {
                "local": count("local", True),
                "r2": count("r2", True),
                "bilibili": count("bilibili", True),
                "both": both,
                "only_r2": sum(1 for e in episodes if e["r2"] and e["bilibili"] is False),
                "only_bilibili": sum(1 for e in episodes if e["bilibili"] and e["r2"] is False),
                "neither": count("r2", False) if r2_titles is not None else None,
            },
            "episodes": episodes,
        })

    courses.sort(key=lambda c: (-c["episode_count"], c["name"]))
    totals = {
        "courses": len(courses),
        "episodes": sum(c["episode_count"] for c in courses),
        "duration_seconds": sum(c["duration_seconds"] for c in courses),
        "size_bytes": sum(c["size_bytes"] for c in courses),
        "local": sum(c["counts"]["local"] for c in courses),
        "r2": sum(c["counts"]["r2"] for c in courses),
        "bilibili": sum(c["counts"]["bilibili"] for c in courses),
        "both": sum(c["counts"]["both"] for c in courses),
    }

    document = {
        "$schema": "./courses.schema.json",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "account_id": index.get("account_id"),
        "spine": "verify_fresh/index.json (the account's own course list)",
        "totals": totals,
        "courses": courses,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "courses.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

    # The page cannot fetch() a local file when it is opened straight from disk, so the same
    # document is also emitted as a script that assigns it to a global. Regenerate, do not
    # hand-edit: courses.json stays the source of truth.
    (args.out_dir / "courses.data.js").write_text(
        "// Generated from courses.json -- do not edit. Regenerate with tools/courses_json.py\n"
        "window.COURSE_DATA = " + json.dumps(document, ensure_ascii=False, indent=1) + ";\n",
        encoding="utf-8")

    (args.out_dir / "courses.schema.json").write_text(
        json.dumps(schema(), ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"wrote {args.out_dir}\\courses.json  "
          f"({totals['courses']} courses, {totals['episodes']} episodes)")
    print(f"  local={totals['local']} r2={totals['r2']} bilibili={totals['bilibili']} both={totals['both']}")
    return 0


def schema() -> dict:
    """JSON Schema (2020-12) for courses.json."""
    episode = {
        "type": "object",
        "description": "One lesson. A platform field is null when that platform's mapping for "
                       "the course is unknown, which is not the same as the lesson being absent.",
        "properties": {
            "file_id": {"type": ["integer", "null"], "description": "Platform file id; the "
                         "<course>-<file> work directory name is built from it."},
            "title": {"type": "string", "description": "Lesson title without the extension."},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "size_bytes": {"type": "integer", "minimum": 0},
            "local": {"type": "boolean"},
            "r2": {"type": ["boolean", "null"]},
            "bilibili": {"type": ["boolean", "null"]},
        },
        "required": ["file_id", "title", "duration_seconds", "size_bytes", "local", "r2", "bilibili"],
        "additionalProperties": False,
    }
    counts = {
        "type": "object",
        "properties": {
            "local": {"type": "integer", "minimum": 0},
            "r2": {"type": "integer", "minimum": 0},
            "bilibili": {"type": "integer", "minimum": 0},
            "both": {"type": "integer", "minimum": 0,
                     "description": "Lessons present on both platforms."},
            "only_r2": {"type": "integer", "minimum": 0},
            "only_bilibili": {"type": "integer", "minimum": 0},
            "neither": {"type": ["integer", "null"], "minimum": 0,
                        "description": "Null when the course has no R2 mapping, so absence "
                                       "cannot be claimed."},
        },
        "required": ["local", "r2", "bilibili", "both", "only_r2", "only_bilibili", "neither"],
        "additionalProperties": False,
    }
    course = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "Platform course id."},
            "name": {"type": "string"},
            "category": {"type": ["string", "null"]},
            "local_path": {"type": ["string", "null"],
                           "description": "Path under verify_fresh/out, when exported."},
            "r2_prefix": {
                "description": "One prefix, or several when the course spans more than one "
                               "(WebGIS课程 is a single course of 220 lessons whose chapters "
                               "are the ten WebGIS collections).",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "null"},
                ],
            },
            "bilibili_ids": {"type": "array", "items": {"type": "string"},
                             "description": "BV ids of the 稿件 carrying this course."},
            "episode_count": {"type": "integer", "minimum": 0},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "size_bytes": {"type": "integer", "minimum": 0},
            "counts": counts,
            "episodes": {"type": "array", "items": episode},
        },
        "required": ["id", "name", "category", "local_path", "r2_prefix", "bilibili_ids",
                     "episode_count", "duration_seconds", "size_bytes", "counts", "episodes"],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/ev-reverse/courses.schema.json",
        "title": "Course upload status",
        "description": "Every course on the account and where each of its lessons has reached: "
                       "on disk, in the R2 bucket, and on Bilibili.",
        "type": "object",
        "properties": {
            "$schema": {"type": "string", "description": "Relative path to this schema."},
            "generated_at": {"type": "string", "description": "Local time, YYYY-MM-DD HH:MM."},
            "account_id": {"type": ["integer", "null"]},
            "spine": {"type": "string", "description": "Which source supplied the course list."},
            "totals": {
                "type": "object",
                "properties": {
                    "courses": {"type": "integer", "minimum": 0},
                    "episodes": {"type": "integer", "minimum": 0},
                    "duration_seconds": {"type": "integer", "minimum": 0},
                    "size_bytes": {"type": "integer", "minimum": 0},
                    "local": {"type": "integer", "minimum": 0},
                    "r2": {"type": "integer", "minimum": 0},
                    "bilibili": {"type": "integer", "minimum": 0},
                    "both": {"type": "integer", "minimum": 0},
                },
                "required": ["courses", "episodes", "duration_seconds", "size_bytes",
                             "local", "r2", "bilibili", "both"],
                "additionalProperties": False,
            },
            "courses": {"type": "array", "items": course},
        },
        "required": ["generated_at", "account_id", "totals", "courses"],
        "additionalProperties": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
