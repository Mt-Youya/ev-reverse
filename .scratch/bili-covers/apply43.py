"""Put the 4:3 poster into every 稿件's 首页推荐封面 slot.

Bilibili's 稿件 editor has two cover slots -- 首页推荐封面 (4:3) and 个人空间封面 (16:9) -- and
`biliup upload --cover` fills only one of them, the 16:9 that the API calls `cover`. The result was
45 稿件s whose 4:3 slot held a **crop of the 16:9 poster** (Bilibili's "双比例同步" default) instead
of the 4:3 poster that was rendered for it.

There is no CLI or documented API for the second slot: `biliup` has no `cover43` field, the edit
endpoint takes only POST with a whole-archive payload, and the community API docs were shut down in
January 2026. So this drives the editor page itself, which is safe by construction -- the payload is
Bilibili's own, built from the form's state.

The flow, all by selector, after cookies from biliup are injected:

    upload/video/frame?type=edit&bvid=<bv>      the editor for one 稿件
      click  div.cover-img                      open the cover dialog
      upload .cover-editor input[type=file]     the 4:3 poster (the 4:3 slot is active by default)
      click  #ab-done                           apply -> may raise the sync question
      click  #ab-nosync                         keep the two ratios different
      click  #ab-done                           close the dialog
      click  .submit-add                        re-submit the 稿件

    python apply43.py --list              # what would be done, one line per 稿件
    python apply43.py --only rollup       # one collection
    python apply43.py --apply [--limit N] # do them, one at a time
"""

import argparse
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / ".scratch" / "bili-covers"))
from ab import ab, login  # noqa: E402
import clean_work as cw  # noqa: E402
import bili_upload as bu  # noqa: E402

SUCCESS_TEXT = "稿件投递成功"                            # what the page says after a good submit


def poster(rel: str, ratio: str):
    folder = REPO / "verify_fresh" / "out" / rel
    return folder / "封面" / f"{folder.name}-合集封面-{ratio}.png"


def targets(only=None):
    """[(collection, rel, bv, 4:3 poster)] for every 稿件 that should carry both posters."""
    rows = []
    for rel, bvs in cw.BILI_BY_COLLECTION.items():
        name = pathlib.PurePosixPath(rel).name
        if only and not any(key in rel for key in only):
            continue
        four = poster(rel, "4x3")
        if not four.exists():
            continue
        for bv in bvs:
            if bv in bu.SUPERSEDED:       # the watermarked duplicate nobody will keep
                continue
            rows.append((name, rel, bv, four, poster(rel, "16x9")))
    return rows


def apply_one(bv: str, four: pathlib.Path, sixteen: pathlib.Path, log) -> bool:
    """Returns True when the 稿件 was re-submitted with both posters in their own slots.

    Both ratios are uploaded on purpose. Uploading only the 4:3 makes Bilibili ask "检测到个人空间
    封面（16:9）未修改 ... 是否同步" and answering that dialog needs a text-based click -- the dialog
    has no stable class, and an id injected by eval dies as soon as Vue re-renders. Changing both
    slots means there is nothing to ask about.
    """
    ab("open", f"https://member.bilibili.com/platform/upload/video/frame?type=edit&bvid={bv}", timeout=120)
    time.sleep(4)

    # The cover dialog is taller than a default window: at 1258x622 its 完成 button sits below the
    # viewport, the page has nothing to scroll (the dialog is fixed), and a click by selector then
    # reports success while hitting nothing. docs/BROWSER-AUTOMATION.md §3.5.
    ab("set", "viewport", "1400", "1300", timeout=40)
    time.sleep(1)

    # The browser daemon restarts on its own and comes back with an empty profile, landing on the
    # passport page. Cookies injected once at the start of a batch would be gone by the second 稿件,
    # so the check is per 稿件.
    if "cover-img" not in ab("eval", "(document.querySelector('div.cover-img') ? 'cover-img' : document.location.host)", timeout=40):
        log.append("session lost; injecting cookies again")
        login()
        ab("open", f"https://member.bilibili.com/platform/upload/video/frame?type=edit&bvid={bv}", timeout=120)
        time.sleep(4)
        ab("set", "viewport", "1400", "1300", timeout=40)
        time.sleep(1)

    log.append(ab("scrollintoview", "div.cover-img").strip())
    log.append(ab("click", "div.cover-img").strip())
    time.sleep(2)

    opened = ab("eval", "(document.querySelector('.cover-editor') ? 'open' : 'closed')")
    if "open" not in opened:
        log.append(f"cover dialog did not open: {opened.strip()[:80]}")
        return False

    # The 4:3 slot is the active one when the dialog opens.
    log.append(ab("upload", ".cover-editor input[type=file]", str(four), timeout=180).strip())
    time.sleep(4)
    # Then the 16:9 slot, with its own poster (the same image it already held, re-sent so the ratio
    # counts as touched).
    log.append(ab("click", ".cover-editor .inactive").strip())
    time.sleep(2)
    log.append(ab("upload", ".cover-editor input[type=file]", str(sixteen), timeout=180).strip())
    time.sleep(4)

    # Clicked by class, never by an injected id: Vue replaces the node on re-render and the id goes
    # with it, which is how an earlier run left the dialog open and the submit click blocked.
    log.append(ab("click", ".cover-editor-button .button.submit").strip())
    time.sleep(3)
    if "closed" not in ab("eval", "(document.querySelector('.cover-editor') ? 'open' : 'closed')"):
        log.append("cover dialog still open after 完成")
        return False

    # save the 稿件: the page's own submit, so the payload is Bilibili's, not ours
    log.append(ab("scrollintoview", ".submit-add").strip())
    time.sleep(1)
    log.append(ab("click", ".submit-add").strip())

    # The success view arrives a while after the click (the 稿件 is re-submitted and re-reviewed),
    # so poll rather than sleeping a fixed amount: a check that runs too early reads the form's own
    # text and calls a good submission a failure.
    for _ in range(12):
        time.sleep(5)
        text = ab("eval", "document.body.innerText.slice(-400).replace(/[\\r\\n]+/g,' ')", timeout=40)
        if SUCCESS_TEXT in text:
            return True
    log.append(f"no success message: {text.strip()[:140]}")
    return False


def ensure_browser(log) -> bool:
    """Make sure the daemon answers before spending a 稿件 on it.

    The daemon idle-exits, restarts with an empty profile, and occasionally stops answering CDP
    altogether ("os error 10060" on every call). One 稿件 costs ~90 seconds, so it is worth one
    cheap probe first; a dead browser would otherwise fail every remaining item.
    """
    for attempt in range(2):
        if "http" in ab("get", "url", timeout=30):
            return True
        log.append("browser not answering; restarting it")
        ab("close", timeout=30)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process -Name 'agent-browser' -ErrorAction SilentlyContinue | "
                        "Stop-Process -Force"], capture_output=True, text=True)
        time.sleep(3)
        login()
    return "http" in ab("get", "url", timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = targets(args.only)
    if args.limit:
        rows = rows[:args.limit]

    if args.list or not args.apply:
        print(f"{'collection':<32}{'稿件':<14} 4:3 poster")
        for name, rel, bv, four, sixteen in rows:
            print(f"{name[:32]:<32}{bv:<14} {four.name} + {sixteen.name}")
        print(f"\n{len(rows)} 稿件(s) would get their 4:3 cover")
        return 0

    print("logging in with the cookies biliup holds...")
    login()
    done, failed = 0, []
    for index, (name, rel, bv, four, sixteen) in enumerate(rows, 1):
        log = []
        print(f"\n=== [{index}/{len(rows)}] {name} ({bv}) ===", flush=True)
        try:
            ensure_browser(log)
            ok = apply_one(bv, four, sixteen, log)
        except Exception as error:                      # noqa: BLE001
            ok, log = False, log + [f"exception: {error}"]
        for line in log:
            if line:
                print("   ", line[:150])
        if ok:
            done += 1
            print("    submitted")
        else:
            failed.append((name, bv))
            print("    FAILED")
    print(f"\nsubmitted {done}/{len(rows)}")
    for name, bv in failed:
        print(f"   failed: {name} ({bv})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
