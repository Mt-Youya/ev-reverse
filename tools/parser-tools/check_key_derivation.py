"""Wait for a file whose key is known both ways, then say whether the derivation is right.

The offline derivation (`MD5_hex(tk + filename + "20220507")`) produces files that are structurally
perfect and visually grey, while the key the player actually set up produces the same kind of file
with zero decode errors. Both cannot be true of the same file, so the question is what the two keys
are for a file where both are known -- and the two sources rarely overlap: the derivation needs a
captured segment list, the live key needs the player to have decrypted that segment.

This watches for the overlap. Every `--interval` seconds it looks through
`captured/player_keys.jsonl` (live keys, each already matched to its file by
`build_videos.py`'s strong oracle) and `captured/`'s segment lists (`filename -> tk`), and the first
file that appears in both is reported with the two keys side by side. Equal keys mean the derivation
is right and the grey frames come from somewhere else; different keys mean the derivation is wrong
for this format and the difference is the thing to fix.

    python check_key_derivation.py --seconds 3600
"""

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from export_cached import collect  # noqa: E402

KEYS = os.path.join(HERE, "captured", "player_keys.jsonl")
MATCHED = os.path.join(HERE, "captured", "key_files.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3600)
    ap.add_argument("--interval", type=float, default=60)
    args = ap.parse_args()

    lessons, sources = collect()
    tk_by_name = {}
    for lesson, entries in lessons.items():
        for name, (_window, _index, tk) in entries.items():
            tk_by_name.setdefault(name, tk)
    print(f"{len(tk_by_name)} filename(s) with a tk from {sources} captured list(s)", flush=True)

    deadline = time.time() + args.seconds
    reported = set()
    while time.time() < deadline:
        if os.path.exists(MATCHED):
            matched = json.load(open(MATCHED, encoding="utf-8"))
            for key, name in matched.items():
                if not name or name in reported:
                    continue
                tk = tk_by_name.get(name)
                if not tk:
                    continue
                reported.add(name)
                derived = hashlib.md5((tk + name + "20220507").encode()).hexdigest()
                verdict = "IDENTICAL" if derived == key else "DIFFERENT"
                print(f"\n*** overlap found ({len(reported)}) ***", flush=True)
                print(f"  file      {name}", flush=True)
                print(f"  player    {key}", flush=True)
                print(f"  derived   {derived}", flush=True)
                print(f"  tk        {tk}", flush=True)
                print(f"  verdict   {verdict}", flush=True)
                if derived != key:
                    print("  -> the derivation is wrong for this format; the two keys differ in the "
                          "way that has to be explained", flush=True)
        time.sleep(args.interval)
    print(f"\nno overlap among {len(reported)} checked file(s)" if not reported
          else f"\n{len(reported)} overlap(s) reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
