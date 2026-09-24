"""Drive agent-browser for the cover work, without cmd's quoting hazards.

`docs/BROWSER-AUTOMATION.md` says to call agent-browser through `cmd /c "... > log 2>&1"`, because
the browser daemon inherits stdout and a PowerShell pipe would never see EOF. cmd brings its own
problem here: cookie values contain `%2C`, and `%` is cmd's variable syntax, so SESSDATA would be
mangled on the way through. Start-Process redirects to a file directly -- no pipe, no cmd -- which
satisfies both constraints.

    from ab import ab
    print(ab("get", "url"))
    ab("open", "https://member.bilibili.com/platform/home")
"""

import pathlib
import subprocess
import sys
import uuid

AB = pathlib.Path.home() / ".cargo" / "bin" / "agent-browser.exe"
LOG_DIR = pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp")
BILIUP_COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"


def ab(*args: str, timeout: int = 90) -> str:
    """Run one agent-browser command; returns its output."""
    out = LOG_DIR / f"ab-{uuid.uuid4().hex[:8]}.out"
    err = LOG_DIR / f"ab-{uuid.uuid4().hex[:8]}.err"
    process = subprocess.Popen([str(AB), *args], stdout=out.open("wb"),
                               stderr=err.open("wb"), stdin=subprocess.DEVNULL)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    text = out.read_text(encoding="utf-8", errors="replace")
    errors = err.read_text(encoding="utf-8", errors="replace")
    for path in (out, err):
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            # The browser daemon keeps the stdout handle of the call that started it, so that one
            # log file cannot be deleted until the browser exits. Unique names mean it does not
            # matter; docs/BROWSER-AUTOMATION.md §1.2 is about exactly this.
            pass
    if errors.strip() and not text.strip():
        return errors
    return text + (("\n[stderr] " + errors.strip()) if errors.strip() else "")


def login() -> list:
    """Copy the four cookies biliup holds into this browser profile. Returns the log lines."""
    import json
    cookies = json.loads(BILIUP_COOKIES.read_text(encoding="utf-8"))
    wanted = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")
    lines = []
    for cookie in cookies["cookie_info"]["cookies"]:
        if cookie["name"] not in wanted:
            continue
        lines.append(ab("cookies", "set", cookie["name"], cookie["value"],
                        "--domain", ".bilibili.com", "--path", "/").strip())
    return lines


if __name__ == "__main__":
    print(ab(*sys.argv[1:]))
