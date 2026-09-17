"""How many hot pages can probe_write.py actually watch?

The tested code is not a copy: the JavaScript is EXTRACTED from
tools/parser-tools/probe_write.py at run time, so what runs here is what ships. Only the bootstrap is
substituted -- the synthetic driver replaces `if (chooseTargets()) arm();`, which needs
PlayerLibRender56_vs.dll and a live player, with a direct arm over the storm target's pages.
scheduleRearm, onAccess, beat and arm are untouched. `Process.getModuleByName` is stubbed because a
Rust process has none.

Arms:
  quiet   floor=2, no storm            control. If this shows no heartbeat, the harness is broken and
                                       nothing the storm arms say means anything.
  storm   floor=2, sweep of page counts
  mutant  floor=2 -> 0 at the largest workable count. This is the falsification: if the unthrottled
                                       re-arm does just as well, the rate limit is not load-bearing
                                       and the ticket's claim about it is wrong.

Usage:  <python> -u storm_test.py [page counts...]
        defaults to 24 8 4 2 1

The built target goes to the OS temp directory; nothing is written into the repo.
"""
import re
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
PROBE = HERE.parent / "probe_write.py"

# 12 s storm + 8 s quiet gap, then 24 stores at 200 ms each.
SECS = 34
STORM_MS = 12000


def extract_js():
    text = PROBE.read_text(encoding="utf-8")
    m = re.search(r'JS = r"""(.*?)\n"""', text, re.S)
    if not m:
        print("could not extract the JS out of %s" % PROBE)
        sys.exit(1)
    return m.group(1)


def build():
    EXE.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["rustc", "-O", "-o", str(EXE), str(SRC)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stderr[-2000:])
        sys.exit(1)


def run(label, floor, storm_ms, secs=SECS, pages_n=24):
    p = subprocess.Popen([str(EXE), str(storm_ms)], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    threading.Thread(target=lambda: [lines.append(l.rstrip()) for l in p.stdout],
                     daemon=True).start()
    # Wait for the banner to be COMPLETE, not for a line count. Counting lines is what broke the
    # first version of this harness: the target prints 26 lines, the wait asked for 27, and the loop
    # burned its full 400 * 20 ms = 8 s timeout before attaching -- which put the quiet arm's attach
    # right at the moment its stores begin and made a working heartbeat look starved.
    for _ in range(400):
        if any(l.startswith("storm_ms=") for l in lines):
            break
        time.sleep(0.02)
    assert any(l.startswith("storm_ms=") for l in lines), "the target never finished its banner"
    pid = int([l for l in lines if l.startswith("pid=")][0].split("=")[1])
    pages = [l.split("=")[1] for l in lines if l.startswith("page=")]

    js = extract_js()
    js = js.replace("var dll = Process.getModuleByName(DLL);", "var dll = null;")
    js = js.replace("var REARM_FLOOR_MS = 2;", "var REARM_FLOOR_MS = %d;" % floor)
    assert "var REARM_FLOOR_MS = %d;" % floor in js, "the floor constant moved -- update this test"
    pages_js = ", ".join("'0x" + a + "'" for a in pages[:pages_n])
    js = js.replace("if (chooseTargets()) arm();", """(function () {
  var PAGES = [%s];
  watched = PAGES.map(function (a, i) {
    return { page: a, fieldOff: 0x120, file: 'x' + i + '.ts', index: i, ctx: a };
  });
  ranges = PAGES.map(function (a) { return { base: ptr(a), size: 0x1000 }; });
  focus = watched[0];
  arm();
})();""" % pages_js)
    assert "chooseTargets()) arm()" not in js

    session = frida.attach(pid)
    script = session.create_script(js)
    got = []
    script.on("message", lambda m, d: got.append(m.get("payload") or {}))
    script.load()
    time.sleep(secs)
    try:
        session.detach()
    except Exception:
        pass
    p.kill()

    kinds = {}
    for g in got:
        kinds[g.get("t")] = kinds.get(g.get("t"), 0) + 1
    hbs = [g for g in got if g.get("t") == "hb"]
    writes = [g for g in got if g.get("t") == "write"]
    stalls = 0
    last = 0.0
    for g in hbs:
        at = g["at"] / 1000.0
        if last and at - last > 15.0:
            stalls += 1
        last = at
    target_writes = len([l for l in lines if l.startswith("write #")])
    print("%-6s floor=%-2d pages=%-2d  target stores=%-3d  heartbeats=%-3d stalls=%-2d  "
          "caught=%-8s messages=%s" % (
              label, floor, len(pages[:pages_n]), target_writes, len(hbs), stalls,
              writes[0]["file"] if writes else "no", kinds))
    return {"heartbeats": len(hbs), "stalls": stalls, "caught": bool(writes),
            "target_writes": target_writes}


def attempt(label, floor, storm_ms, secs=SECS, pages_n=24):
    """An arm that kills the target, or the agent, is a result -- not a crash to hide."""
    try:
        return run(label, floor, storm_ms, secs, pages_n)
    except Exception as e:
        print("%-6s floor=%-2d  ARM DIED: %s: %s" % (label, floor, type(e).__name__, e))
        return {"heartbeats": 0, "stalls": 0, "caught": False, "target_writes": 0, "died": True}


def main():
    counts = [int(a) for a in sys.argv[1:]] or [24, 8, 4, 2, 1]
    build()
    print("target: 24 pages read in tight loops by 4 threads for 12 s, then a quiet gap, "
          "then 24 stores\n")

    attempt("quiet", 2, 0, secs=20)
    print()
    sweep = [(n, attempt("storm", 2, STORM_MS, pages_n=n)) for n in counts]

    print("\n--- how many hot pages this instrument can actually watch ---")
    ok = [n for n, r in sweep if r["heartbeats"] >= 3 and r["caught"] and not r.get("died")]
    print("page counts that stayed live AND caught the store: %s" % (ok or "none"))
    if not ok:
        print("NOTHING WORKS -- the instrument cannot watch even one hot page under this storm.")
        print("Do not record the fix as working.")
        return 1
    best = max(ok)
    print("largest workable page count: %d" % best)
    print("\nmutation: the same run with the re-arm floor removed, at %d page(s)" % best)
    before = attempt("mutant", 0, STORM_MS, pages_n=best)
    worse = before.get("died") or before["heartbeats"] < 3 or not before["caught"]
    print("\nold policy (floor=0) is measurably worse than the floor: %s%s"
          % ("YES" if worse else "NO -- the floor is not what makes this work",
             "  (arm died / target wedged)" if before.get("died") else ""))
    if worse:
        print("\nVERIFIED at %d page(s): throttled it reports and catches; unthrottled it does not."
              % best)
        return 0
    print("\nMUTATION DID NOT BITE -- the rate limit is not load-bearing here; say so.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
