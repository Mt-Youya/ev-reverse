"""Check whether a 稿件 carries Bilibili's "bilibili + nickname" watermark.

Bilibili stamps that watermark at 稿件 creation unless the request says otherwise, and
`biliup` has no flag for it -- `tools/bili_upload.py` therefore sends
`--extra-fields '{"watermark":{"state":0}}'` and this is how that claim is verified. The
creator-centre endpoints that would expose the stored setting return 404, so the check has
to be visual: pull the first seconds of the video back down and look at the top-left corner.

It writes pngs and prints their paths: the first frame, and a strip of six top-left corners
sampled across the clip. The strip is the one to judge by -- a lesson often opens on a blank
or white slide, where a white watermark would not show, and the mark is small enough that a
full frame hides it.

    python check_watermark.py BV1P1aw6AEoB [--part 1] [--seconds 60] [--out <dir>]
"""

import argparse
import json
import pathlib
import subprocess
import sys
import urllib.request

BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
PLAYURL = ("https://api.bilibili.com/x/player/playurl"
           "?bvid={bv}&cid={cid}&qn=64&fnval=1&fourk=1")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def cookie_header() -> str:
    data = json.loads(COOKIES.read_text(encoding="utf-8"))
    pairs = [f"{c['name']}={c['value']}" for c in data["cookie_info"]["cookies"]]
    return "; ".join(pairs)


def parts(bv: str) -> list:
    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    brace = result.stdout.find("{")
    if brace < 0:
        sys.exit(f"could not read {bv}: {result.stdout[-300:]}{result.stderr[-300:]}")
    return json.loads(result.stdout[brace:]).get("videos", [])


def stream_url(bv: str, cid) -> str:
    request = urllib.request.Request(PLAYURL.format(bv=bv, cid=cid), headers={
        "User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{bv}",
        "Cookie": cookie_header()})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != 0:
        sys.exit(f"playurl refused: {payload.get('code')} {payload.get('message')}")
    durl = payload["data"].get("durl") or []
    if not durl:
        sys.exit("playurl returned no durl (dash-only response?)")
    return durl[0]["url"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bvid")
    parser.add_argument("--part", type=int, default=1, help="1-based part number")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--start", type=int, default=0,
                        help="seconds into the part to sample from. Lessons that open on a white "
                             "slide hide a white watermark, so sampling later is often the only "
                             "way to see it.")
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\watermark"))
    parser.add_argument("--compare",
                        help="path to the local original of the same lesson. Writes a difference "
                             "image of the top-left corner: the reliable check, because a lesson "
                             "on a white slide hides a white watermark from the eye")
    args = parser.parse_args()

    videos = parts(args.bvid)
    if not videos:
        sys.exit(f"{args.bvid} has no parts")
    part = videos[min(args.part, len(videos)) - 1]
    print(f"{args.bvid} part {args.part}/{len(videos)}: {part.get('title')} "
          f"(cid {part.get('cid')}, {part.get('duration')}s)")

    url = stream_url(args.bvid, part.get("cid"))
    args.out.mkdir(parents=True, exist_ok=True)
    clip = args.out / f"{args.bvid}-p{args.part}.mp4"
    # The CDN refuses the request without a User-Agent as well as the Referer; the cookie is what
    # makes an 仅自己可见 稿件 readable at all.
    headers = (f"Referer: https://www.bilibili.com/video/{args.bvid}\r\n"
               f"User-Agent: {UA}\r\n"
               f"Cookie: {cookie_header()}\r\n")

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-headers", headers,
                    "-ss", str(args.start), "-i", url, "-t", str(args.seconds),
                    "-c", "copy", str(clip)], check=True)
    print(f"clip  {clip}  ({clip.stat().st_size / 1024 ** 2:.1f} MB)")

    frame = args.out / f"{args.bvid}-p{args.part}-frame.png"
    corner = args.out / f"{args.bvid}-p{args.part}-corner-strip.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1",
                    str(frame)], check=True)
    # Six samples across the clip, tiled left to right: the watermark is on every frame, so any
    # one of them shows it, and sampling avoids judging a lesson by a blank opening slide.
    every = max(1, args.seconds // 6)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf",
                    f"fps=1/{every},crop=560:150:0:0,scale=560:150,tile=6x1",
                    "-frames:v", "1", str(corner)], check=True)
    print(f"frame {frame}")
    print(f"strip {corner}   <- look here: six top-left corners across the clip; a watermark is "
          f"small white text")

    if args.compare:
        # The strip is a judgement call -- a lesson on a white slide hides a white watermark. This
        # is the proof: the same content from the local original, scaled to the stream's size, and
        # the difference between the two corners. A watermark is a shape that is not in the
        # original, so it shows up as legible glyphs; re-encoding noise does not.
        original = pathlib.Path(args.compare)
        if not original.is_file():
            sys.exit(f"--compare {original} is not a file")
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-show_entries", "stream=width,height", "-of", "csv=p=0",
                               str(clip)], capture_output=True, text=True).stdout.strip()
        width, _, height = size.partition(",")
        if not width.isdigit():
            sys.exit(f"could not read the stream size: {size!r}")
        mine = args.out / f"{args.bvid}-p{args.part}-original.png"
        theirs = args.out / f"{args.bvid}-p{args.part}-stream.png"
        diff = args.out / f"{args.bvid}-p{args.part}-diff.png"
        moment = args.start + 2
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(moment), "-i", str(original),
                        "-frames:v", "1", "-vf", f"scale={width}:{height}", str(mine)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2", "-i", str(clip),
                        "-frames:v", "1", str(theirs)], check=True)
        crop = f"crop={min(560, int(width))}:{min(150, int(height))}:0:0"
        for source, target in ((mine, args.out / "corner-original.png"),
                               (theirs, args.out / "corner-stream.png")):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf", crop,
                            str(target)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-i", str(args.out / "corner-original.png"),
                        "-i", str(args.out / "corner-stream.png"), "-filter_complex",
                        "[0][1]blend=all_mode=difference,eq=contrast=8:brightness=0.15,"
                        "scale=1120:300:flags=neighbor", str(diff)], check=True)
        print(f"diff  {diff}   <- flat means no watermark; legible glyphs mean Bilibili added one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
