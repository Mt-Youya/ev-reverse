"""Print how Bilibili reports each 稿件's visibility (throwaway probe)."""
import json
import pathlib
import subprocess
import sys

BILIUP = pathlib.Path.home() / ".biliup" / "bin" / "biliup.exe"
COOKIES = pathlib.Path.home() / ".biliup" / "cookies.json"

for bv in sys.argv[1:]:
    raw = subprocess.run([str(BILIUP), "-u", str(COOKIES), "show", bv],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    brace = raw.find("{")
    if brace < 0:
        print(f"{bv}: no json")
        continue
    archive = json.loads(raw[brace:])["archive"]
    print(f"{bv}  {archive.get('title')!r}")
    print(f"    state={archive.get('state')} state_desc={archive.get('state_desc')!r} "
          f"is_only_self={archive.get('is_only_self')} no_public={archive.get('no_public')} "
          f"attrs.no_public={archive.get('attrs', {}).get('no_public')}")
