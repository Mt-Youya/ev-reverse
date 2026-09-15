"""Ask the server for `tk` by name, then let decryption say which files belong to that lesson.

The user's question this answers: `tk` lives in the API response, so is one request enough to get a
whole lesson's keys? Measured: a request can carry many names (195 succeeded in one signature,
10,646 bytes of signed fields, no limit hit), but the server signs **only the names it is given** --
five names return five entries, 195 return 195, never one more. So one request is enough *once the
names are known*, and it cannot discover them.

What that makes possible is the reverse direction, and it is worth more than the question asked:
hand it **every filename on disk**, and the files that decrypt to a valid transport stream are
exactly the ones belonging to this playkey's lesson. Names and ownership both stop being unknowns,
and nothing has to be played.

The discriminator is decryption itself. A `tk` from the wrong lesson does not fail quietly -- the
first bytes come out as noise (measured 0.13%-0.29% sync against 100% for the right lesson), so
three sync bytes at offsets 0, 188 and 376 are enough, and a false positive needs all three to land,
one chance in sixteen million.

    python match_lesson_files.py --playkey <280-char playkey> --token <jwt> --all --limit 200
    python match_lesson_files.py --playkey <...> --token <...> --all
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EVMEDIA = os.path.join(REPO, "target", "release", "evmedia.exe")
CACHE = r"D:\Downloads\EVPlayer2Downloads"
PROBE = os.path.join(HERE, "captured", "lesson_files.json")


def ask_for_keys(playkey, token, names, workdir):
    """One request for up to a few hundred names; returns {filename: tk}."""
    os.makedirs(workdir, exist_ok=True)
    liststr = ",".join(f"{index}|0|{name}" for index, name in enumerate(names))
    target = os.path.join(workdir, "list.json")
    done = subprocess.run([EVMEDIA, "fetch", "--playkey", playkey, "--liststr", liststr,
                           "--token", token, "--output", target],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print(f"  fetch failed: {(done.stderr or done.stdout).strip()[:200]}", flush=True)
        return {}
    try:
        document = json.load(open(target, encoding="utf-8"))
    except Exception as error:
        print(f"  unreadable reply: {error}", flush=True)
        return {}
    out = {}
    for item in document.get("k_l", []):
        name = str(item.get("sf", "")).split("?")[0].rsplit("/", 1)[-1]
        if name and item.get("tk"):
            out[name] = item["tk"]
    return out


def opens_with_key(name, tk):
    """Three sync bytes after unmasking and decrypting -- see crypto::decrypt for the scheme."""
    from Crypto.Cipher import AES
    try:
        with open(os.path.join(CACHE, name), "rb") as handle:
            head = handle.read(576)
    except Exception:
        return False
    if len(head) < 576:
        return False
    key = hashlib.md5((tk + name + "20220507").encode()).hexdigest().encode()
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    masked = bytes(b ^ mask[i % 16] for i, b in enumerate(head))
    plain = AES.new(key, AES.MODE_ECB).decrypt(masked)
    return plain[0] == 0x47 and plain[188] == 0x47 and plain[376] == 0x47


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--playkey", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--all", action="store_true", help="every .ts in the download directory")
    ap.add_argument("--names", help="a file holding one filename per line")
    ap.add_argument("--batch", type=int, default=195)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many names (0 = all)")
    ap.add_argument("--workdir", default=os.path.join(HERE, "captured", "match"))
    args = ap.parse_args()

    if args.names:
        names = [line.strip() for line in open(args.names, encoding="utf-8") if line.strip()]
    else:
        names = sorted(name for name in os.listdir(CACHE) if name.endswith(".ts"))
    if args.limit:
        names = names[:args.limit]
    print(f"{len(names)} candidate filename(s), {args.batch} per request "
          f"({(len(names) + args.batch - 1) // args.batch} request(s))", flush=True)

    matched = []
    for start in range(0, len(names), args.batch):
        batch = names[start:start + args.batch]
        keys = ask_for_keys(args.playkey, args.token, batch, args.workdir)
        if not keys:
            break
        hits = [name for name in batch if name in keys and opens_with_key(name, keys[name])]
        matched.extend(hits)
        print(f"  batch {start // args.batch + 1}: {len(keys)} tk returned, "
              f"{len(hits)} open this lesson's stream  (total {len(matched)})", flush=True)

    print(f"\n{len(matched)} of {len(names)} file(s) belong to this playkey's lesson")
    for name in matched[:10]:
        print(f"  {name}")
    if os.path.exists(PROBE):
        known = json.load(open(PROBE, encoding="utf-8"))
    else:
        known = {}
    known[args.playkey[:24]] = matched
    json.dump(known, open(PROBE, "w", encoding="utf-8"), indent=1)
    print(f"wrote {PROBE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
