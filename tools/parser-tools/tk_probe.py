"""Does tk depend on the lesson's play key, or only on the file name?

Answering this decides whether an old download can ever be decrypted later: if tk is a property of
the file, re-fetching it is enough; if it is a property of (play key, file), the ciphertext on disk
is bound to the play key that was in force when it was downloaded.

Method: take file names whose tk the player itself obtained (they are in the captured lists, tied to
a known lesson host), ask the API for the same names with that lesson's play key, and compare.

    python tk_probe.py --playkey <base64 play key> --lesson 91773801 --count 5
"""

import argparse
import glob
import gzip
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EVMEDIA = os.path.join(REPO, "target", "release", "evmedia.exe")
TOKEN = os.path.join(REPO, "verify_out5", "token.txt")
LIST = re.compile(rb'\{"d_p":"[^"]+","k_l":\[.*?\]\}')


def captured(lesson):
    """filename -> tk, for every list that names this lesson, as the player received it."""
    out = {}
    for path in glob.glob(os.path.join(HERE, "captured", "api", "*", "*.bin")):
        blob = open(path, "rb").read()
        plain = gzip.GzipFile(fileobj=io.BytesIO(blob)).read() if blob[:2] == b"\x1f\x8b" else blob
        for match in LIST.finditer(plain):
            try:
                document = json.loads(match.group().decode("utf-8"))
            except Exception:
                continue
            if lesson not in str(document.get("d_p", "")):
                continue
            for entry in document.get("k_l", []):
                name = str(entry.get("sf", "")).split("?")[0].rsplit("/", 1)[-1]
                if name and entry.get("tk"):
                    out[name] = entry["tk"]
    return out


def ask(playkey, names, token, output):
    liststr = ",".join(f"{index}|0|{name}" for index, name in enumerate(names))
    done = subprocess.run([EVMEDIA, "fetch", "--playkey", playkey, "--liststr", liststr,
                           "--token", token, "--output", output],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        print(f"  fetch failed: {(done.stdout + done.stderr).strip()[:200]}")
        return None
    document = json.load(open(output, encoding="utf-8"))
    return {str(entry["sf"]).split("?")[0].rsplit("/", 1)[-1]: entry["tk"]
            for entry in document["k_l"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--playkey", required=True)
    ap.add_argument("--lesson", required=True)
    ap.add_argument("--count", type=int, default=5)
    args = ap.parse_args()

    known = captured(args.lesson)
    if not known:
        print(f"no captured list names lesson {args.lesson}")
        return 1
    names = list(known)[:args.count]
    print(f"{len(known)} captured name(s) for lesson {args.lesson}; asking about {len(names)}")

    token = open(TOKEN, encoding="utf-8").read().strip()
    output = os.path.join(REPO, "verify_out5", "_tk_probe.json")
    fresh = ask(args.playkey, names, token, output)
    if fresh is None:
        return 1

    same = differ = 0
    for name in names:
        old, new = known[name], fresh.get(name)
        if old == new:
            same += 1
        else:
            differ += 1
        print(f"  {name[:52]}")
        print(f"      player's: {old}")
        print(f"      ours now: {new}")
    print(f"\n{same} identical, {differ} different")
    print("identical -> tk is reproducible for (play key, file); a later fetch can still open the "
          "ciphertext. different -> the on-disk ciphertext is bound to the play key in force when it "
          "was downloaded, and re-fetching later will not open it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
