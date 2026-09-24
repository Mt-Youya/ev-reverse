"""Upload every locally exported collection to its R2 prefix, one collection per invocation.

The prefix table lives in `clean_work.R2_BY_COLLECTION` and is imported rather than copied. It was
a second, hand-maintained list here until a coverage check caught the drift: 音视频实时互动技术
had no local files when this file was written and was therefore absent from it, so once the
exporter produced that collection its R2 upload would have been skipped without a word. One table
cannot drift from itself.

Prefix names are ambiguous enough that they cannot be guessed: `scaffolding` really is
新自定义脚手架 (7 of its 9 filenames were already in that prefix), while `react`, `gsap` and `koa`
hold files that are not in this account's catalogue at all and must not be reused. See
docs/R2-UPLOAD.md for how a pairing is proved.

Keys are `prefix + path relative to the collection folder`, so a chaptered course keeps its chapter
subfolders, matching how LangGraph is already stored under
`videos/ai/langgraph/architecture-duyi-edu/第二章 快速入门/`.

    python r2_upload_all.py [--apply] [--only <substring>]
"""

import argparse
import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
OUT = REPO / "verify_fresh" / "out"
UPLOADER = REPO / "tools" / "upload_to_r2.py"
PYTHON = sys.executable

sys.path.insert(0, str(REPO / "tools"))
import clean_work  # noqa: E402

# local collection -> R2 prefix, from the single verified table. Collections with no local export
# are skipped at run time, so a prefix may be listed long before there is anything to send.
PLAN = [(collection, prefix) for collection, prefix in sorted(clean_work.R2_BY_COLLECTION.items())]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    failures = []
    skipped = 0
    for collection, prefix in PLAN:
        if args.only and args.only not in collection:
            continue
        folder = OUT / collection
        if not folder.is_dir():
            skipped += 1
            continue
        videos = [p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".ts")]
        if not videos:
            skipped += 1
            continue

        command = [PYTHON, str(UPLOADER), "--local", str(folder), "--prefix", prefix,
                   "--include-images"]
        if args.apply:
            command.append("--apply")
        print(f"\n{'=' * 80}")
        print(f"{collection}  ({len(videos)} videos) -> {prefix}")
        print("=" * 80, flush=True)
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        for line in (result.stdout or "").splitlines():
            if (line.startswith(("local:", "target:", "mode:", "bucket already", "SKIP", "UPLOAD",
                                 "uploaded", "verified", "nothing", "  !", "   !"))
                    or "FAILED" in line):
                print("  " + line)
        if result.returncode != 0:
            failures.append((collection, result.returncode, (result.stderr or "")[-300:]))
            print(f"  !! rc={result.returncode} {result.stderr[-200:]}")

    print(f"\n{skipped} collection(s) in the table have nothing on disk yet")
    if failures:
        print(f"{len(failures)} collection(s) failed:")
        for collection, code, err in failures:
            print(f"  {collection}: rc={code} {err}")
        return 1
    print("all collections with local videos processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
