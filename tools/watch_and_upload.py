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
    args = parser.parse_args()

    LOG.write_text("", encoding="utf-8")
    log("watching the exporter; uploads start after it goes idle")

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
    if args.skip_upload:
        log("--skip-upload set, stopping here")
        return 0

    # Bilibili first: it is a domestic upload and runs an order of magnitude faster than R2.
    # 前端工具链 is excluded because its only remaining local file is one the 稿件 already holds
    # under a different part title, so appending it would create a duplicate part.
    run([PYTHON, str(REPO / "tools" / "bili_upload.py"), "--skip", "工具链", "--apply"],
        "bilibili uploads")
    run([PYTHON, str(REPO / "tools" / "r2_upload_all.py"), "--apply"], "r2 uploads")
    log("all uploads finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
