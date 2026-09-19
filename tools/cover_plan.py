"""Finds every cover image under a local root and works out where it belongs in R2.

Cover images are the `封面/*.png` files the poster generator writes. They sit beside the lesson
videos, so a cover's R2 prefix is the prefix its course's videos already use. That mapping is read
from the audit (`build/r2-audit.json`) rather than guessed from folder names -- one course's videos
live under a prefix that shares no words with the folder, and a guessed name silently uploads to a
new prefix nobody looks at.

    python cover_plan.py [--root <dir>] [--apply] [--json <out>]

Without `--apply` it prints the plan and uploads nothing.
"""

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
UPLOADER = HERE / "upload_to_r2.py"
DEFAULT_ROOT = pathlib.Path(r"D:\Codes\github\ev-reverse\verify_fresh\out\AI 大全栈")
AUDIT = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-audit.json")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def course_prefixes() -> dict:
    """local course folder -> R2 prefix, from the audit."""
    if not AUDIT.exists():
        return {}
    rows = json.loads(AUDIT.read_text(encoding="utf-8"))
    return {pathlib.Path(row["local"]): row["r2_prefix"]
            for row in rows if row.get("r2_prefix")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"D:\Codes\github\ev-reverse\build\cover-plan.json"))
    parser.add_argument("--apply", action="store_true",
                        help="actually upload; without it the plan is printed and nothing is sent")
    args = parser.parse_args()

    known = course_prefixes()
    # Map each local folder that holds a cover to the nearest ancestor the audit knows about.
    plan, unknown = [], []
    for path in sorted(args.root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        folder = path.parent
        prefix = None
        probe = folder
        while probe != args.root.parent:
            if probe in known:
                prefix = known[probe]
                break
            probe = probe.parent
        relative = path.relative_to(args.root).as_posix()
        if prefix:
            # Keep the cover's own folder in the key so 封面/16x9 and 封面/4x3 stay apart.
            tail = path.relative_to(probe).as_posix()
            plan.append({"file": str(path), "relative": relative, "prefix": prefix,
                         "key": prefix + tail, "course": str(probe)})
        else:
            unknown.append(relative)

    print(f"{len(plan)} cover(s) with a known course prefix")
    print(f"{'file':<52}{'R2 key'}")
    print("-" * 110)
    for item in plan:
        rel = item["relative"]
        print(f"{rel[:50]:<52}{item['key']}")

    if unknown:
        print(f"\n{len(unknown)} image(s) with no course prefix in the audit:")
        for rel in unknown:
            print(f"   {rel}")

    args.json.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.apply:
        print("\ndry run: nothing uploaded")
        return 0

    if not plan:
        print("\nnothing to upload")
        return 0

    # One uploader call per course, so the skip-by-MD5 decision still applies per object.
    by_prefix: dict[str, list] = {}
    for item in plan:
        by_prefix.setdefault(item["prefix"], []).append(item)

    failures = 0
    for prefix, items in sorted(by_prefix.items()):
        print("\n" + "=" * 100)
        print(f"{prefix}  ({len(items)} cover(s))")
        print("=" * 100)
        for item in items:
            # --local is the course folder and the exact key is passed whole. Pointing --local at the
            # cover's own folder instead made the key `prefix + filename`, dropping the `封面/` level.
            command = [sys.executable, "-u", str(UPLOADER),
                       "--local", item["course"],
                       "--prefix", prefix,
                       "--only", pathlib.Path(item["file"]).name,
                       "--exact-key", item["key"],
                       "--include-images", "--apply"]
            result = subprocess.run(command, capture_output=True)
            output = (result.stdout or b"").decode("utf-8", "replace") + \
                     (result.stderr or b"").decode("utf-8", "replace")
            for line in output.splitlines():
                if line.startswith(("SKIP", "UPLOAD", "uploaded", "verified", "  =")):
                    print(f"   {line}")
            if result.returncode != 0:
                failures += 1
                print(f"   FAILED: {item['relative']}")
                for line in output.splitlines():
                    if line.strip().startswith("!"):
                        print(f"      {line.strip()}")

    print(f"\n{len(plan) - failures}/{len(plan)} cover(s) handled, {failures} failed")
    print("\nnote: this fixes keys only for objects it is told about. Objects already written under a "
          "flattened key stay there -- remove them with tools/delete_r2_objects.py.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
