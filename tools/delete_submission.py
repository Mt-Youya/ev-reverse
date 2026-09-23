"""Delete one of my own Bilibili 稿件 through the creator API.

The creator centre's row menu would not open under automation, and biliup has no delete
subcommand, so the request the page itself makes is sent directly with the account's own
cookies. Deleting a 稿件 is irreversible, so this prints what it will remove and does nothing
unless --apply is passed.

NOTE: the delete endpoint is stale -- `member.bilibili.com/x/vu/web/delete` answers HTTP 404.
Reading the aid via the view API works; only the DELETE call needs a new URL. To find it, watch
what the page sends when a human presses delete (`agent-browser network requests`), and see
docs/BILIBILI-UPLOAD.md §5.4.

    python delete_submission.py BV1zqhb6bE1W [--apply]
"""

import argparse
import json
import pathlib
import sys
import urllib.parse
import urllib.request

COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
VIEW = "https://api.bilibili.com/x/web-interface/view"
DELETE = "https://member.bilibili.com/x/vu/web/delete"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def cookie_header() -> tuple:
    data = json.loads(COOKIES.read_text(encoding="utf-8"))
    jar = {c["name"]: str(c["value"]) for c in data["cookie_info"]["cookies"]}
    header = "; ".join(f"{k}={v}" for k, v in jar.items())
    return header, jar


def get_json(url: str, header: str, params: dict = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://member.bilibili.com/",
        "Cookie": header,
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_form(url: str, header: str, form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA,
        "Referer": "https://member.bilibili.com/platform/upload-manager/article",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": header,
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bvid")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    header, jar = cookie_header()
    csrf = jar.get("bili_jct")
    if not csrf:
        print("no bili_jct cookie: cannot send a state-changing request", file=sys.stderr)
        return 1

    view = get_json(VIEW, header, {"bvid": args.bvid})
    if view.get("code") != 0:
        print(f"view failed: {view.get('code')} {view.get('message')}", file=sys.stderr)
        return 1
    data = view["data"]
    aid = data["aid"]
    parts = len(data.get("pages", []))
    print(f"bvid={args.bvid} aid={aid} title={data.get('title')!r} parts={parts} "
          f"duration={data.get('duration')}s owner={data.get('owner', {}).get('name')}")

    if not args.apply:
        print("\ndry run: nothing deleted (pass --apply)")
        return 0

    result = post_form(DELETE, header, {"aid": aid, "csrf": csrf})
    print(f"delete -> code={result.get('code')} message={result.get('message')}")
    return 0 if result.get("code") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
