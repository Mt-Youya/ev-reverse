"""One cached reading of the account's 稿件 list, shared by every tool that needs it.

`biliup list --max-pages 60` is not a cheap call: with ~490 稿件s it walks dozens of pages of
`member.bilibili.com/x/web/archives`, and Bilibili answers that endpoint with **HTTP 412** when
it decides the caller asks too often (seen 2026-09-24 after a few verification scripts ran in
one round). biliup reports the refusal as `Unknown Error / error decoding response body` with
nothing on stdout, which every reader below has to treat as "no answer" rather than "no 稿件s"
-- an empty account is never the truth here and reading it as one makes uploaders create
duplicate submissions.

So: fetch at most once per `--max-age` minutes, keep the text in `build/bili-listing.txt`, and
let the callers filter it. The cache is a convenience, not a source of truth: it expires, and an
empty or unreadable listing is still an error.

    from bili_listing import listing
    text = listing()                     # cached, up to 10 minutes old
    text = listing(max_age_minutes=0)     # always fresh
"""

import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"
CACHE = REPO / "build" / "bili-listing.txt"


def _read_cache(max_age_minutes):
    """The cached listing, or None. `None` for the age means "any age".

    The earlier form took a negative number for "any age" and then bailed on it, so the fallback
    that is supposed to serve a stale listing when Bilibili refuses returned nothing -- exactly
    when it was needed.
    """
    if not CACHE.is_file():
        return None
    if max_age_minutes is not None:
        age_minutes = (time.time() - CACHE.stat().st_mtime) / 60
        if age_minutes > max_age_minutes:
            return None
    text = CACHE.read_text(encoding="utf-8", errors="replace")
    return text if any(line.startswith("BV") for line in text.splitlines()) else None


def listing(max_age_minutes: float = 10) -> str:
    """The account's 稿件 listing, cached; raises SystemExit when the account answers nothing."""
    cached = _read_cache(max_age_minutes)
    if cached is not None:
        return cached

    result = subprocess.run([str(BILIUP), "-u", str(COOKIES), "list", "--max-pages", "60"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not any(line.startswith("BV") for line in result.stdout.splitlines()):
        stale = _read_cache(None)
        if stale is not None:
            print("biliup list answered nothing (HTTP 412 from Bilibili?); using the cached "
                  "listing from a previous run", file=sys.stderr)
            return stale
        raise SystemExit(
            "biliup list returned no 稿件 at all (HTTP 412 from Bilibili?), and no cached listing "
            "exists. Refusing to run: an empty listing would make every collection look like it "
            "needs a new submission. Retry in a few minutes.")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(result.stdout, encoding="utf-8")
    return result.stdout


def entries(max_age_minutes: float = 10) -> list:
    """[(bvid, title, status)] parsed from the listing, newest first as the account returns it."""
    rows = []
    for line in listing(max_age_minutes).splitlines():
        if not line.startswith("BV"):
            continue
        fields = line.split("\t")
        if len(fields) >= 2:
            rows.append((fields[0], fields[1].strip(), fields[2].strip() if len(fields) > 2 else ""))
    return rows
