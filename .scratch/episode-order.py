"""Print a collection's episodes in course order, for the browser uploader.

`bili_upload.course_order` is not reused on purpose. Its chapter key is 0 for anything that is not
`第X章`, so a course's `课程导言` and `总结` both land at the front -- the intro would be submitted
after the summary. Its tie-break is a plain string compare, which orders `7-10` before `7-2`.
Bilibili keeps parts in the order they arrive and offers no way to fix that afterwards, so the
order is stated here instead of inherited.

    python episode-order.py <collection relative to verify_fresh/out> [output-name]

Writes `<output-name>-files.txt` next to this script: one absolute path per line, in submission
order. The name defaults to the collection's ASCII characters, which collapses every all-Chinese
course name to the same `collection` -- two collections then share one listing and one of them
silently gets the other's episodes. Pass the name explicitly for those.
"""

import pathlib
import re
import sys

OUT = pathlib.Path(r"verify_fresh\out")
CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
VIDEO = (".mp4", ".mkv", ".ts")


def folder_rank(name: str) -> int:
    """Intro first, chapters in order, summary last, anything else in between."""
    if "导言" in name or "介绍" in name:
        return 0
    match = re.match(r"^第([一二三四五六七八九十]+)章", name)
    if match:
        return CN.get(match.group(1), 50)
    if "总结" in name:
        return 99
    return 50


def natural(name: str):
    """`7-2` before `7-10`: compare digit runs as numbers, not as text."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def order_key(folder: pathlib.Path, path: pathlib.Path):
    folders = path.relative_to(folder).parts[:-1]
    return ([folder_rank(part) for part in folders],
            [natural(part) for part in folders],
            natural(path.name))


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    relative = sys.argv[1]
    folder = OUT / relative
    if not folder.is_dir():
        sys.exit(f"no such collection: {folder}")

    videos = sorted((p for p in folder.rglob("*")
                     if p.is_file() and p.suffix.lower() in VIDEO),
                    key=lambda p: order_key(folder, p))

    total = sum(p.stat().st_size for p in videos) / 1024 ** 3
    print(f"{len(videos)} files, {total:.2f} GB")
    for index, path in enumerate(videos, 1):
        print(f"{index:>2}. {path.relative_to(folder)}  ({path.stat().st_size / 1024 ** 2:.0f} MB)")

    slug = sys.argv[2] if len(sys.argv) > 2 else re.sub(r"[^0-9A-Za-z]+", "-", relative).strip("-").lower()
    if not slug:
        sys.exit("this collection name has no ASCII in it; pass an output name as the second argument")
    listing = pathlib.Path(__file__).with_name(f"{slug}-files.txt")
    listing.write_text("\n".join(str(p.resolve()) for p in videos), encoding="utf-8")
    print(f"\nwrote {listing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
