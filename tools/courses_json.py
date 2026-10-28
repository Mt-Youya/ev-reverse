"""Emit the course-status data as JSON, with a JSON Schema describing it.

The page that displays this reads the JSON at load time, so refreshing the numbers never means
editing HTML -- only regenerating the data.

Where each number comes from:

  * index.json      -- the account's own course list: every course, every file, with file_id,
                       title, duration and size. This is the only source that contains courses
                       that were never exported, which is why it is the spine of the document
                       rather than a walk of the export tree.
  * verify_fresh/out -- what is on disk.
  * R2 / Bilibili   -- the bucket's own object names (not only those under the course's prefix,
                       which is a hand-written claim), and the part titles of its 稿件.

Per episode, a platform is reported as `true`, `false`, or `null`. `null` means "not known"
rather than "not there": a course with no prefix established yet has no R2 answer, and writing
`false` for it would read as a fact about the upload.

    python courses_json.py [--out-dir <dir>]
"""

import argparse
import collections
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
import bili_upload as bu         # noqa: E402  (the part-title aliases)


def common_parent(rels: list):
    """The folder the collections share, so a multi-chapter course still names a path.

    WebGIS课程 is one course of 220 lessons across ten chapter collections, all under
    `out/WebGIS课程`. Reporting null there would say "nothing was ever exported" about the
    ten folders that are right there.
    """
    parents = {str(pathlib.PurePosixPath(rel).parent) for rel in rels}
    if len(parents) == 1 and parents != {"."}:
        return OUT / parents.pop()
    return None


def norm(title: str) -> str:
    """Part or object name for comparison: no media extension, no whitespace.

    The extension pattern repeats because the platform's own title does: 工程管理实战's lesson 35
    is stored as `35. [测试体系]单元测试.mp4.mp4`, and stripping one extension left
    `35.[测试体系]单元测试.mp4` against a disk file named `35. [测试体系]单元测试.mp4`. The 1.1 GB
    file sitting in out/ therefore read as "no local copy" and the page offered it as
    un-uploadable. bili_upload.norm() strips the same repeat, for the same reason.
    """
    t = re.sub(r"(\.(mp4|mkv|ts))+$", "", str(title).strip(), flags=re.I)
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


def local_folders() -> dict:
    """course name -> the folder holding its finished files.

    Matching on "the folder that directly contains videos" was wrong for every chaptered
    collection: 前端工具链, LangGraph, CSS企业应用方案 and the rest keep their lessons in
    chapter subfolders, so they have no video at their own level and every one of their
    episodes read as never exported on a page that meanwhile showed them uploaded. Count
    the subtree instead, and when one name occurs twice under out/, take the folder with
    more videos.
    """
    totals = {}
    for path in OUT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        parent = path.parent
        while True:
            totals[parent] = totals.get(parent, 0) + 1
            if parent == OUT:
                break
            parent = parent.parent
    found = {}
    for path, count in totals.items():
        if path == OUT:
            continue
        if path.name not in found or count > found[path.name][1]:
            found[path.name] = (path, count)
    return {name: path for name, (path, _count) in found.items()}


def stray_home(titles: set, own: set, homes: dict):
    """The prefix that holds most of a collection's lessons when the mapped one holds none.

    Only asked then. A prefix in `clean_work.R2_BY_COLLECTION` is a hand-written claim about where
    a collection lives and is also where the uploaders write, so a prefix holding nothing of its
    collection reads two ways: nothing of it was ever uploaded, or the prefix does not point at
    its home. The bucket settles which. A collection's lesson titles are near-unique there -- on
    this account no collection with an empty prefix shares more than one title with any other
    prefix -- so a prefix holding half of them is a mapping to fix, not a coincidence.
    """
    votes = collections.Counter()
    for title in titles:
        for home in homes.get(title, ()):
            if home not in own:
                votes[home] += 1
    return votes.most_common(1)[0] if votes else (None, 0)


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


def episode_index(index: dict) -> dict:
    """(course id, file id) -> the platform's own record for that lesson.

    A catalog video's id is `<course>:<file>`, which is exactly the pair the course list is keyed
    by: on this account all 1910 catalog videos resolve, so the tree never has to guess which
    lesson a file is by matching its title.
    """
    found = {}
    for cid, record in index["courses"].items():
        for f in course_files(record["course_detail"]):
            if f.get("file_id") is not None:
                found[(int(cid), int(f["file_id"]))] = f
    return found


def course_tree(byname: dict, roots: dict) -> list:
    """The player's own catalog tree, stamped with each lesson's upload state.

    The page mirrors the player's directory tree, so the tree comes from catalog.json -- the same
    document the player draws -- rather than from a re-grouping of the flat course list. Nodes are
    emitted in catalog order and named exactly as the player names them; only the per-video state
    below is added.

    Every video is looked up by `<course id>:<file id>`, not by title: 工程管理实战 stores its
    lesson 35 as `35. [测试体系]单元测试.mp4.mp4`, and titles repeat across courses.
    """
    by_id = {}
    for course in byname.values():
        for episode in course["episodes"]:
            if episode.get("file_id") is not None:
                by_id[(course["id"], int(episode["file_id"]))] = (course, episode)

    def visit(node) -> dict | None:
        kind = node.get("kind")
        title = str(node.get("title") or "")
        if kind == "video":
            cid, _, fid = str(node.get("id") or "").partition(":")
            found = by_id.get((int(cid), int(fid))) if cid.isdigit() and fid.isdigit() else None
            if not found:
                return None
            course, episode = found
            record = {
                "kind": "video",
                "title": title,
                "file_id": int(fid),
                "duration_seconds": episode["duration_seconds"],
                "size_bytes": episode["size_bytes"],
                "local": episode["local"],
                "r2": episode["r2"],
                "bilibili": episode["bilibili"],
            }
            if episode["bilibili"] and course["bilibili_ids"]:
                # Which part of which 稿件 carries it, so the page can link straight to the
                # lesson instead of naming a BV the reader then has to search.
                record["bv"] = course["bilibili_ids"][0]
                record["page"] = next((i + 1 for i, e in enumerate(course["episodes"])
                                       if e is episode), None)
            return record

        children = [child for child in (visit(c) for c in node.get("children") or []) if child]
        if not children:
            return None
        # A folder's own totals are the only place they are written down: a video carries its own
        # three flags and nothing else, and the page aggregates as it renders. `both` means the
        # same thing here as in the course list -- on R2 and on Bilibili -- so adding `local`
        # would turn it into "still on disk", which is 17 lessons, not the 1075 that are up.
        summary = {"count": 0, "both": 0}
        for child in children:
            folder = child["kind"] == "folder"
            summary["count"] += child["summary"]["count"] if folder else 1
            summary["both"] += (child["summary"]["both"] if folder
                                else 1 if child["r2"] and child["bilibili"] else 0)
        return {"kind": "folder", "title": title, "children": children, "summary": summary}

    return [node for node in (visit(root) for root in (roots.get("roots") or [])) if node]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_DIR)
    args = parser.parse_args()

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    roots = catalog_roots()
    s3 = upload_to_r2.client(upload_to_r2.credentials())

    # One listing of the bucket instead of one per prefix. It answers the same question about each
    # prefix, and it can also answer the one a prefix cannot -- where a collection's lessons are:
    # `r2_cache` is what the mapped prefixes hold, `homes` is the bucket's own index, and the two
    # are compared below. At this bucket's size it is also cheaper: 1.4k objects is two paginated
    # calls against the 72 the loop made. Listed from the root, not from `videos/`, so a prefix
    # that ever moves outside that root is still read correctly.
    bucket = upload_to_r2.list_remote(s3, "cyrus-media-private", "")
    videos = {k: size for k, (size, _e) in bucket.items()
              if size > 0 and k.lower().endswith(VIDEO_SUFFIXES)}
    r2_cache = {}
    for prefix in sorted(set(cw.R2_BY_COLLECTION.values())):
        r2_cache[prefix] = {norm(k.rsplit("/", 1)[-1]) for k in videos if k.startswith(prefix)}
    homes = {}
    for key in videos:
        homes.setdefault(norm(key.rsplit("/", 1)[-1]), set()).add(key.rsplit("/", 1)[0] + "/")
    bili_cache = {rel: bili_titles(bvs) for rel, bvs in cw.BILI_BY_COLLECTION.items()}
    mappings = name_map()

    # local folder lookup: course name -> path under out/
    local_by_name = local_folders()

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
            current = mappings.get(cw.path_key(own), inherited)
            for f in node.get("files", []):
                if f.get("type") == "video":
                    units.append((f, current))
            for child in node.get("childs", []):
                attribute(child, current)

        attribute(detail, mappings.get(cw.path_key(name)))
        rel = next((u["rel"] for _f, u in units if u), None)
        prefixes = {u["prefix"] for _f, u in units if u and u["prefix"]}
        all_bvs = sorted({bv for _f, u in units if u for bv in u["bvs"]})
        r2_titles = set().union(*[r2_cache[p] for p in prefixes]) if prefixes else None
        # A prefix that holds none of this collection's lessons cannot say whether they were never
        # uploaded or the prefix points somewhere else: both come back empty, and the page wrote
        # the first as a fact for seventeen courses. Ask the bucket instead, and when another
        # prefix holds half of them, report the R2 answer as unknown rather than as absent -- a
        # lesson that is in the bucket must never be offered for upload again. Two hits minimum
        # because one shared title (`01.课程导言` sits in four prefixes here) proves nothing.
        titles = {norm(f.get("title", "")) for f in files}
        if r2_titles is not None and titles and not (r2_titles & titles):
            home, hits = stray_home(titles, prefixes, homes)
            if home and hits >= 2 and hits * 2 >= len(titles):
                print(f"warning: {name}: {sorted(prefixes)} holds none of its {len(titles)} "
                      f"lessons, but {home} holds {hits} of them; reporting R2 as unknown -- "
                      f"check clean_work.R2_BY_COLLECTION", file=sys.stderr)
                r2_titles = None
        bili_parts = set().union(*[bili_cache[r] for r in
                                   {u["rel"] for _f, u in units if u} if r in bili_cache]) \
            if any(u and u["rel"] in bili_cache for _f, u in units) else None

        rels = sorted({u["rel"] for _f, u in units if u})
        if rels:
            # The collection's own folder, the same `rel` its R2 prefix and 稿件 were derived
            # from. Looking up the *course* name instead missed every chaptered collection --
            # WebGIS课程 is ten chapter folders and has no folder of its own, and LangGraph keeps
            # its lessons under 第一章/第二章 -- so their episodes all read as never exported on a
            # page that meanwhile showed them uploaded.
            local = set().union(*[local_titles(OUT / rel) for rel in rels])
            folder = OUT / rels[0] if len(rels) == 1 else common_parent(rels)
        else:
            # No mapping yet: a collection that has just been exported and not added to the
            # tables still deserves a real local count, so fall back to matching by name.
            folder = local_by_name.get(name)
            local = local_titles(folder) if folder else set()

        # Lessons whose part carries a different title than the lesson does: 前端工具链's first part
        # is named after the collection, because the batch upload page had that row selected.
        # bili_upload knows the alias and does not re-upload it; without it here the page keeps
        # reporting one lesson missing from Bilibili on a collection that is complete.
        aliases = {}
        for _f, unit in units:
            if unit:
                title = pathlib.PurePosixPath(unit["rel"]).name
                aliases |= {norm(local_name): norm(part_title)
                            for local_name, part_title in bu.PART_ALIASES.get(title, {}).items()}

        episodes = []
        for f in sorted(files, key=lambda x: episode_order(x.get("title"))):
            key = norm(f.get("title", ""))
            on_bilibili = None if bili_parts is None else (
                key in bili_parts or (aliases.get(key) in bili_parts if key in aliases else False))
            episodes.append({
                "file_id": f.get("file_id"),
                "title": str(f.get("title", "")).removesuffix(".mp4"),
                "duration_seconds": int(f.get("duration") or 0),
                "size_bytes": int(f.get("size") or 0),
                "local": key in local,
                "r2": (key in r2_titles) if r2_titles is not None else None,
                "bilibili": on_bilibili,
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
                # "都无" means absent from both, so it has to test both. Counting `r2 is False`
                # alone called a lesson Bilibili holds a lesson nothing holds -- the reader sees
                # "两边都没有" about a collection that is up. Null unless both answers are known,
                # for the same reason the episode fields are: an unknown is not a "no".
                "neither": (sum(1 for e in episodes
                                if e["r2"] is False and e["bilibili"] is False)
                            if r2_titles is not None and bili_parts is not None else None),
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

    # A second view over the same facts: the player's catalog tree, so a reader navigates the
    # backlog the way they navigate the player. Built from catalog.json rather than re-grouped
    # from the course list, and emitted into courses.json so one file describes the page.
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    tree = course_tree({c["name"]: c for c in courses}, catalog)
    in_tree = sum(node["summary"]["count"] for node in tree)
    if in_tree != totals["episodes"]:
        print(f"warning: the catalog tree holds {in_tree} videos but the course list has "
              f"{totals['episodes']} episodes; the tree view will be incomplete",
              file=sys.stderr)

    document = {
        "$schema": "./courses.schema.json",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "account_id": index.get("account_id"),
        "spine": "verify_fresh/index.json (the account's own course list)",
        "totals": totals,
        "courses": courses,
        "catalog": tree,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "courses.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")

    # The page cannot fetch() a local file when it is opened straight from disk, so the same
    # document is also emitted as a script that assigns it to a global. Regenerate, do not
    # hand-edit: courses.json stays the source of truth. Written compact because this is machine
    # input, not reading material, and the catalog tree roughly doubles the document; the
    # validator decodes both and compares the documents, so the two encodings cannot drift.
    (args.out_dir / "courses.data.js").write_text(
        "// Generated from courses.json -- do not edit. Regenerate with tools/courses_json.py\n"
        "window.COURSE_DATA = " + json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
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
                        "description": "Lessons absent from both platforms. Null when either "
                                       "platform's answer is unknown, so absence cannot be "
                                       "claimed."},
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
    # The catalog tree, recursive: a folder holds folders and videos, a video carries its own
    # three flags. Only folders carry `summary` ("两边都有 x/y"); a video's contribution is its
    # own line, so repeating a summary per video would say the same thing twice 1910 times.
    summary = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 0, "description": "Videos below this node."},
            "both": {"type": "integer", "minimum": 0,
                     "description": "Videos below this node that are on R2 and on Bilibili."},
        },
        "required": ["count", "both"],
        "additionalProperties": False,
    }
    catalog_video = {
        "type": "object",
        "properties": {
            "kind": {"const": "video"},
            "title": {"type": "string", "description": "The player's own name, extension and all."},
            "file_id": {"type": "integer"},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "size_bytes": {"type": "integer", "minimum": 0},
            "local": {"type": "boolean"},
            "r2": {"type": ["boolean", "null"]},
            "bilibili": {"type": ["boolean", "null"]},
            "bv": {"type": "string", "description": "The 稿件 carrying this lesson, when known."},
            "page": {"type": ["integer", "null"], "description": "Its part number in that 稿件."},
        },
        "required": ["kind", "title", "file_id", "duration_seconds", "size_bytes", "local",
                     "r2", "bilibili"],
        "additionalProperties": False,
    }
    catalog_folder = {
        "type": "object",
        "properties": {
            "kind": {"const": "folder"},
            "title": {"type": "string"},
            "children": {"type": "array",
                         "items": {"oneOf": [{"$ref": "#/$defs/catalog_folder"}, catalog_video]}},
            "summary": summary,
        },
        "required": ["kind", "title", "children", "summary"],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/ev-reverse/courses.schema.json",
        "title": "Course upload status",
        "description": "Every course on the account and where each of its lessons has reached: "
                       "on disk, in the R2 bucket, and on Bilibili.",
        "type": "object",
        "$defs": {"catalog_folder": catalog_folder},
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
            "catalog": {
                "type": "array",
                "description": "The player's catalog tree (catalog.json), the same hierarchy the "
                               "player shows, with each video's upload state attached.",
                "items": {"$ref": "#/$defs/catalog_folder"},
            },
        },
        "required": ["generated_at", "account_id", "totals", "courses", "catalog"],
        "additionalProperties": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
