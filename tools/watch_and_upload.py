"""Watch the exporter GUI, then run the pending uploads once it goes idle.

The GUI runs `evmedia.exe export-evs` children while it works and leaves the work directory in
place between lessons, so the only reliable "still working" signal is the presence of those child
processes. Idleness is required to hold for several consecutive samples, because a queue that is
swapping lessons briefly shows no children at all.

Usage: python watch_and_upload.py [--idle-samples 3] [--interval 60] [--skip-upload]
Writes progress to the log path it prints on start.
"""

import argparse
import datetime
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
PYTHON = sys.executable
LOG = pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\watch-upload.log")
OUT = REPO / "verify_fresh" / "out"
STATUS = REPO / "verify_fresh" / "upload-status" / "courses.json"


def stale_minutes(path: pathlib.Path) -> float:
    """How old a file is, in minutes; a missing file counts as infinitely old."""
    try:
        return (time.time() - path.stat().st_mtime) / 60
    except OSError:
        return float("inf")


def log(message: str) -> None:
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def exporter_children() -> list:
    """Command lines of the running export children, newest first."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='evmedia.exe'\").CommandLine"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def lesson_of(command_line: str) -> str:
    marker = "--output "
    if marker not in command_line:
        return "?"
    tail = command_line.split(marker, 1)[1]
    return tail.split('"')[1].split("\\")[-1] if tail.startswith('"') else tail.split()[0]


def output_snapshot() -> str:
    """Total mp4 count and bytes under out/, to show the export advancing."""
    count = 0
    total = 0
    for path in OUT.rglob("*.mp4"):
        count += 1
        total += path.stat().st_size
    return f"out/: {count} mp4, {total / 1024 ** 3:.2f} GB"


def _count_matching(pattern: str) -> int:
    """How many python processes match, or 0 when the query cannot be answered.

    `@(...)` is not decoration. `(Get-CimInstance ... | Where-Object {...}).Count` returns nothing
    at all when exactly one process matches -- a single CimInstance has no Count property -- so the
    old form read "one uploader running" as "none running". r2_busy only looked right because an R2
    pass has both a driver and a child, two matches. Forcing an array makes one match count as one.
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         f"Where-Object {{ $_.CommandLine -match '{pattern}' }}).Count"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (result.stdout or "").strip()
    return int(text) if text.isdigit() else 0


def bili_busy() -> bool:
    return _count_matching("bili_upload") > 0


def r2_busy() -> bool:
    """True when an R2 upload is already running, so a second pass would fight it."""
    return _count_matching("upload_to_r2|r2_upload_all") > 0


def publish_busy() -> bool:
    """True when a publish chain is already running.

    Publishing is CPU-bound -- one chain already saturates the machine, and three concurrent
    ones measured *worse* in total (453 s per lesson against 85 s) -- so this gates on itself
    rather than on the uploads. Its purpose is recovery: the chain runs for hours, and if it
    dies the remaining lessons would sit unpublished with nothing to restart them.
    """
    return _count_matching("export_missing") > 0


def run(command: list, label: str) -> None:
    log(f"--- {label} ---")
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    for line in (result.stdout or "").splitlines():
        log("    " + line)
    if result.returncode != 0:
        log(f"    !! {label} rc={result.returncode}: {(result.stderr or '')[-400:]}")
    else:
        log(f"    {label} done")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle-samples", type=int, default=3)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="leave local files and work/ scratch alone at the end of a pass "
                             "instead of deleting what both platforms provably hold")
    parser.add_argument("--no-publish", action="store_true",
                        help="only upload; do not publish the merged scratch that has no finished "
                             "file yet (see docs/EXPORT-PIPELINE.md §3)")
    parser.add_argument("--courses-max-age", type=int, default=45,
                        help="refresh verify_fresh/upload-status/courses.json once it is this many "
                             "minutes old (0 disables). The status page reads it, so it should not "
                             "go stale while the backlog runs, but a refresh costs 40 Bilibili "
                             "calls and 44 bucket listings.")
    parser.add_argument("--loop-minutes", type=int, default=0,
                        help="keep uploading on this interval instead of exiting after one pass. "
                             "The exporter works through a long queue, so waiting for it to finish "
                             "before uploading anything means nothing is ever uploaded; both "
                             "uploaders skip what the platforms already hold, so repeating a pass "
                             "is safe and picks up whatever the exporter finished since.")
    args = parser.parse_args()

    LOG.write_text("", encoding="utf-8")
    log("watching the exporter" +
        (f"; uploading every {args.loop_minutes} min" if args.loop_minutes else "; one pass when it goes idle"))

    while True:
        # In loop mode the exporter is not waited on: it works through a long queue and is
        # rarely idle, so requiring idleness would mean never uploading. The uploaders skip
        # what the platforms already hold, so a pass over incomplete material is harmless.
        if not args.loop_minutes:
            idle = 0
            while True:
                children = exporter_children()
                if children:
                    idle = 0
                    lessons = ", ".join(lesson_of(line) for line in children[:6])
                    log(f"exporting {len(children)}: {lessons}   |   {output_snapshot()}")
                else:
                    idle += 1
                    log(f"idle {idle}/{args.idle_samples}   |   {output_snapshot()}")
                    if idle >= args.idle_samples:
                        break
                time.sleep(args.interval)
            log("exporter is idle; starting uploads")
        else:
            log(f"pass start   |   {output_snapshot()}   |   "
                f"exporter children {len(exporter_children())}")

        if args.skip_upload:
            log("--skip-upload set, stopping here")
            return 0

        # The Bilibili pass is not gated on an R2 pass being in flight: R2 runs for hours at a
        # time, and skipping whole passes until it finished left newly exported collections waiting
        # behind it. The two use different routes (domestic vs Cloudflare), Bilibili's share is
        # small, and a rare SSL failure in R2 is retried at the object level. R2 itself is still
        # gated, because two R2 passes would genuinely fight over the same connections.
        if bili_busy():
            log("a bilibili pass is already running; skipping the bilibili half")
        else:
            run([PYTHON, str(REPO / "tools" / "bili_upload.py"), "--apply"],
                "bilibili uploads")
            # A 稿件 created by that pass is invisible to the status page and to the cleanup until
            # its id is in the table, and an empty entry claims "nothing holds these lessons".
            run([PYTHON, str(REPO / "tools" / "sync_bili_ids.py"), "--apply"], "稿件 id sync")
        if r2_busy():
            log("an r2 upload is already running; skipping the r2 half so they do not compete")
        else:
            run([PYTHON, str(REPO / "tools" / "r2_upload_all.py"), "--apply"], "r2 uploads")

        # Keep the publish half alive as well. Eleven collections were downloaded and merged days
        # before anything published them, and the gap was only noticed when the status page called
        # them never exported -- so publishing belongs in the loop, not in someone's shell history.
        if args.no_publish:
            log("--no-publish set; only uploading")
        elif publish_busy():
            log("a publish chain is already running; skipping publishing")
        else:
            run([PYTHON, str(REPO / "tools" / "export_missing.py"),
                 "--from-cache", "--apply", "--smallest-first"], "publish from cache")

        # Reclaim space at the end of a pass instead of when the disk is nearly full. Both of these
        # delete only what is provably redundant: verify_and_delete requires the file to match R2 by
        # MD5 *and* a Bilibili part by duration, clean_work requires the same lesson to be on both
        # platforms before removing its scratch. A backlog of a few hundred lessons is tens of
        # gigabytes of new files, so the space has to come back as it goes.
        if args.no_cleanup:
            log("--no-cleanup set; leaving local files and scratch alone")
        else:
            run([PYTHON, str(REPO / "tools" / "verify_and_delete.py"), "--apply"], "local cleanup")
            run([PYTHON, str(REPO / "tools" / "clean_work.py"), "--apply"], "scratch cleanup")

        # The status page is a deliverable, and it is only as current as courses.json. Refreshing it
        # every pass would mean 40 `biliup show` calls and 44 bucket listings an hour, so it happens
        # once the data is older than --courses-max-age minutes. Keyed on the file's age rather than
        # on a pass counter: the counter restarted with this process, so a watchdog restarted every
        # few minutes would defer the refresh forever.
        if args.courses_max_age and stale_minutes(STATUS) > args.courses_max_age:
            run([PYTHON, str(REPO / "tools" / "courses_json.py")], "courses.json")
            # The page reads courses.data.js and the reports read courses.json, so a refresh that
            # produced a document the schema rejects would go unnoticed until someone looked.
            run([PYTHON, str(REPO / "tools" / "validate_status.py")], "status data check")
            # And the answer to "what is still missing, and why" belongs in a file next to the
            # page, not only in whatever the last report happened to say.
            run([PYTHON, str(REPO / "tools" / "upload_gaps.py"),
                 "--markdown", str(REPO / "verify_fresh" / "upload-status" / "gaps.md")],
                "gaps document")
        elif args.courses_max_age:
            log(f"courses.json is {stale_minutes(STATUS):.0f} min old; refresh after "
                f"{args.courses_max_age} min")

        if not args.loop_minutes:
            log("all uploads finished")
            return 0
        log(f"pass done; next pass in {args.loop_minutes} min")
        time.sleep(args.loop_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
