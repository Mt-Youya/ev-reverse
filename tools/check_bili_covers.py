"""Report which cover each 稿件 actually carries, and in which aspect ratio.

Bilibili's 稿件 editor has two cover slots -- 首页推荐封面 and 个人空间封面 -- and `biliup` can only
set one of them, so a 稿件 can end up with one slot filled and the other empty. The API's GET
(`biliup show`) exposes a single `cover` URL, which makes this the way to see what is really there:
fetch that image and measure it. A 4:3 image means the feed slot holds it, 16:9 means the space
slot; the two posters are rendered separately per ratio, so the aspect tells which one was used.

Run it after any cover upload, and to answer "are the covers right?" across all collections.

    python check_bili_covers.py [--only <substring> ...] [--json <path>]
"""

import argparse
import json
import pathlib
import subprocess
import sys
import urllib.request

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
CACHE = REPO / "build" / "bili-covers"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

sys.path.insert(0, str(REPO / "tools"))
import clean_work as cw  # noqa: E402


def cover_url(bv: str):
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    brace = result.stdout.find("{")
    if brace < 0:
        return None, None
    try:
        data = json.loads(result.stdout[brace:])
    except json.JSONDecodeError:
        return None, None
    archive = data.get("archive", {})
    return archive.get("cover"), archive.get("state")


def fetch(url: str, bv: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{bv}.png"
    if path.exists() and path.stat().st_size > 0:
        return path
    if url.startswith("//"):
        url = "https:" + url
    request = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bv}"})
    path.write_bytes(urllib.request.urlopen(request, timeout=30).read())
    return path


def dimensions(path: pathlib.Path):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        width, height = (int(x) for x in result.stdout.strip().split(",")[:2])
        return width, height
    except ValueError:
        return None, None


def describe(width: int, height: int) -> str:
    if not width:
        return "unreadable"
    ratio = width / height
    if abs(ratio - 16 / 9) < 0.08:
        return "16:9"
    if abs(ratio - 4 / 3) < 0.08:
        return "4:3"
    if abs(ratio - 1.0) < 0.08:
        return "1:1"
    return f"{ratio:.2f}:1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--json", type=pathlib.Path)
    args = parser.parse_args()

    rows = []
    print(f"{'collection':<34}{'稿件':<14}{'state':>6}  cover")
    print("-" * 84)
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        name = pathlib.PurePosixPath(rel).name
        if args.only and not any(key in rel for key in args.only):
            continue
        for bv in bvs:
            url, state = cover_url(bv)
            if not url:
                print(f"{name[:34]:<34}{bv:<14}{str(state):>6}  (no cover)")
                rows.append({"collection": name, "bv": bv, "state": state,
                             "cover": None, "ratio": "none"})
                continue
            path = fetch(url, bv)
            width, height = dimensions(path)
            label = describe(width, height)
            # A 4:3 render is the feed poster, 16:9 the space one; anything else is neither.
            print(f"{name[:34]:<34}{bv:<14}{str(state):>6}  {label:<8} {width}x{height}")
            rows.append({"collection": name, "bv": bv, "state": state, "cover": url,
                         "ratio": label, "width": width, "height": height})

    counts = {}
    for row in rows:
        counts[row["ratio"]] = counts.get(row["ratio"], 0) + 1
    print()
    print("aspect summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
