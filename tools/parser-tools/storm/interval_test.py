"""Does setInterval actually fire against an idle Rust target in this frida build?

This exists because a harness bug made the heartbeat look starved. storm_test.py's quiet arm showed
the arm-time beat and then nothing for eight seconds while its storm arm showed several, which is the
signature of either a real defect in probe_write.py's heartbeat or an artefact of the harness -- and
the difference decides whether the heartbeat can be trusted at all.

It was the harness (see storm_test.py). This keeps the narrow question answerable without the storm
attached: one setInterval, one target, no guards, and a count at the end.

Usage:  <python> -u interval_test.py
"""
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import frida

HERE = Path(__file__).resolve().parent
SRC = HERE / "storm.rs"
EXE = Path(tempfile.gettempdir()) / "ev-storm" / "storm.exe"

JS = r"""
'use strict';
var n = 0;
setInterval(function () { n++; send({ t: 'tick', n: n, at: Date.now() }); }, 1000);
send({ t: 'armed' });
"""


def main():
    EXE.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["rustc", "-O", "-o", str(EXE), str(SRC)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stderr[-2000:])
        return 1

    p = subprocess.Popen([str(EXE), "0"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    lines = []
    threading.Thread(target=lambda: [lines.append(l.rstrip()) for l in p.stdout],
                     daemon=True).start()
    for _ in range(400):
        if lines:
            break
        time.sleep(0.02)
    pid = int(lines[0].split("=")[1])

    session = frida.attach(pid)
    script = session.create_script(JS)
    got = []
    script.on("message", lambda m, d: got.append(m.get("payload") or {}))
    script.load()
    time.sleep(7)
    try:
        session.detach()
    except Exception:
        pass
    p.kill()

    ticks = [g for g in got if g.get("t") == "tick"]
    print("messages: %s" % got)
    print("a 1s interval over 7s produced %d tick(s)" % len(ticks))
    ok = len(ticks) >= 5
    print("RESULT: %s" % ("setInterval works" if ok else "setInterval DOES NOT FIRE here"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
