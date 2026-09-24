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
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         f"Where-Object {{ $_.CommandLine -match '{pattern}' }}).Count"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (result.stdout or "").strip()
    return int(text) if text.isdigit() else 0


def bili_busy() -> bool:
    return _count_matching("bili_upload") > 0


def r2_busy() -> bool:
    """True when an R2 upload is already running, so a second pass would fight it."""
    return _count_matching("upload_to_r2|r2_upload_all") > 0


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
            # 前端工具链 is excluded because its only remaining local file is one the 稿件 already
            # holds under a different part title, so appending it would create a duplicate part.
            run([PYTHON, str(REPO / "tools" / "bili_upload.py"), "--skip", "工具链", "--apply"],
                "bilibili uploads")
        if r2_busy():
            log("an r2 upload is already running; skipping the r2 half so they do not compete")
        else:
            run([PYTHON, str(REPO / "tools" / "r2_upload_all.py"), "--apply"], "r2 uploads")

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

        if not args.loop_minutes:
            log("all uploads finished")
            return 0
        log(f"pass done; next pass in {args.loop_minutes} min")
        time.sleep(args.loop_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
