"""Regenerate the export session by probing the running EVPlayer2 player.

The session is a short-lived credential that exists in exactly two places: inside the running
player, and in whatever file the probe wrote it to. Logging in again inside the player therefore
invalidates the copy the exporter keeps, and every download start fails with
`-123: "长时间未登录，请退出重新登录"` until that copy is regenerated.

`tools/parser-tools/probe_catalog_constants.py` reads it back out of the player's memory. It needs
Frida, and Frida needs a target it is allowed to open: `EVPlayer2.exe` declares
`requireAdministrator`, so the player always runs at high integrity and a medium-integrity attach is
refused with `PermissionDeniedError: unable to access process … from the current user account`.
That refusal is permanent for this process, not a busy moment, so this script has to be elevated
too. Started from a normal shell it re-launches itself through the UAC prompt and waits for the
elevated copy to report back.

The fresh credential is only allowed to replace the old one after the catalog API has accepted it,
which is the one check that separates "a file was written" from "downloads will work again".
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tools" / "parser-tools" / "probe_catalog_constants.py"
EVMEDIA = REPO / "target" / "release" / "evmedia.exe"
PLAYER = "EVPlayer2.exe"
DEFAULT_OUT = REPO / "verify_fresh" / "session.json"


def is_elevated() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def player_pids() -> list[int]:
    """Every running `EVPlayer2.exe`. The player is a launcher plus a rendering process, and only
    the one that holds the session answers the probe, so each candidate is tried in turn."""
    result = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {PLAYER}", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, encoding="gbk", errors="replace")
    pids = []
    for line in result.stdout.splitlines():
        cells = [cell.strip('"').strip() for cell in line.split('","')]
        if len(cells) > 1 and cells[0] == PLAYER and cells[1].isdigit():
            pids.append(int(cells[1]))
    return pids


def probe(pid: int, path: Path) -> str:
    """Run the player probe. It reports what it hooked and keeps the credentials in the file."""
    result = subprocess.run([sys.executable, "-u", str(PROBE), "--pid", str(pid),
                             "--session-out", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120)
    return ((result.stdout or "") + (result.stderr or "")).strip()[-300:]


def accepted(session: Path, account: str) -> tuple[bool, str]:
    """The catalog call is the cheapest way to learn whether the server still honours the token."""
    with tempfile.TemporaryDirectory(prefix="session-check-") as scratch:
        result = subprocess.run([str(EVMEDIA), "catalog", "--session", str(session),
                                 "--account", str(account), "--output", str(Path(scratch) / "catalog")],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode == 0, ((result.stderr or result.stdout or "").strip()[-300:])


def refresh(out: Path, account: str, only: int | None) -> dict:
    candidate = out.with_name(out.name + ".probe")
    candidate.unlink(missing_ok=True)
    pids = [only] if only else player_pids()
    if not pids:
        return {"ok": False, "error": f"{PLAYER} 没有运行。先打开播放器并登录，再重跑本脚本。"}

    failures = []
    for pid in pids:
        try:
            output = probe(pid, candidate)
        except subprocess.TimeoutExpired:
            failures.append(f"pid {pid}: 探针 120s 没有返回")
            continue
        if not candidate.is_file():
            failures.append(f"pid {pid}: 探针没有写出会话（{output}）")
            continue
        good, detail = accepted(candidate, account)
        if not good:
            failures.append(f"pid {pid}: 服务器不接受新会话（{detail}）")
            candidate.unlink(missing_ok=True)
            continue
        session = json.loads(candidate.read_text(encoding="utf-8"))
        os.replace(candidate, out)
        return {"ok": True, "pid": pid, "path": str(out), "fields": sorted(session),
                "token_length": len(str(session.get("token", ""))), "probe_output": output}
    return {"ok": False, "error": "；".join(failures)}


def relaunch(argv: list[str], status: Path, timeout: float) -> int:
    """Ask Windows for an elevated copy of this command, then wait for the file it reports in.

    Only ever called for an explicit `--elevate`: a UAC prompt needs a desktop to appear on, and a
    shell without one blocks in `ShellExecuteW` instead of failing."""
    before = status.stat().st_mtime if status.is_file() else 0.0
    params = " ".join(f'"{argument}"' for argument in argv)
    handle = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(REPO), 1)
    if handle <= 32:
        print(f"无法自动提权（ShellExecute={handle}）。请以管理员身份重跑：{sys.executable} {params}")
        return 1

    print("已请求管理员权限，请在 UAC 提示里点「是」…", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        if status.is_file() and status.stat().st_mtime > before:
            payload = json.loads(status.read_text(encoding="utf-8"))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload.get("ok") else 1
    print(f"{timeout:.0f}s 内没有等到提权进程的结果（{status}）")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="会话文件（默认 verify_fresh/session.json）")
    parser.add_argument("--account", default="119354", help="用于校验会话的账号 id")
    parser.add_argument("--pid", type=int, help="指定播放器进程，默认试所有 EVPlayer2.exe")
    parser.add_argument("--status", type=Path, help="提权子进程写结果的位置（默认与会话文件同目录）")
    parser.add_argument("--timeout", type=float, default=300.0, help="等待提权进程的秒数")
    parser.add_argument("--elevate", action="store_true",
                        help="未提权时弹 UAC 重新启动自己（需要有人能点这个提示）")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    out = args.out if args.out.is_absolute() else REPO / args.out
    status = args.status or out.with_name(out.name + ".status.json")
    if not is_elevated():
        if args.elevate and not args.elevated:
            return relaunch(sys.argv[1:] + ["--elevated"], status, args.timeout)
        print(f"{PLAYER} 以管理员权限运行（它的清单要求这样），Frida 只能从同样高的完整性级别附加，"
              f"所以本脚本必须以管理员身份运行。\n"
              f"  方式一：右键 tools\\refresh_session.py 所在的终端选「以管理员身份运行」，然后执行\n"
              f"          {sys.executable} -u tools\\refresh_session.py\n"
              f"  方式二：加 --elevate 让脚本自己弹 UAC：{sys.executable} -u tools\\refresh_session.py --elevate")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    payload = refresh(out, args.account, args.pid)
    payload["seconds"] = round(time.time() - started, 1)
    status.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
