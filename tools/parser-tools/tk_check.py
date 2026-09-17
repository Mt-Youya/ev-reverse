"""Does a tk fetched with the wrong play key fail loudly, or quietly?

The API signs whatever file names it is given, even when they belong to a different lesson than the
play key does. That returns a full set of plausible-looking tokens -- which would be dangerous if
they decrypted to something that still looked like a video. This decrypts the same on-disk segment
twice, once with the token the player itself obtained and once with a token fetched under another
lesson's play key, and reports the TS sync ratio for each.

    python tk_check.py
"""

import glob
import gzip
import hashlib
import io
import json
import os
import re
import sys

from Crypto.Cipher import AES

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
CACHE = r"D:\Downloads\EVPlayer2Downloads"
LIST = re.compile(rb'\{"d_p":"[^"]+","k_l":\[.*?\]\}')


def decrypt(blob, tk, name):
    key = hashlib.md5((tk + name + "20220507").encode()).hexdigest().encode()
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    masked = bytes(b ^ mask[i % 16] for i, b in enumerate(blob))
    return AES.new(key, AES.MODE_ECB).decrypt(masked)


def sync(plain):
    packets = len(plain) // 188
    good = sum(1 for i in range(0, packets * 188, 188) if plain[i] == 0x47)
    return good / max(1, packets)


def player_tokens(lesson):
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


def main():
    right = player_tokens("91773801")
    victim = player_tokens("63aac7ca")
    wrong_document = json.load(open(os.path.join(REPO, "verify_out5", "_exp_big.json"), encoding="utf-8"))
    wrong = {str(e["sf"]).split("?")[0].rsplit("/", 1)[-1]: e["tk"] for e in wrong_document["k_l"]}

    # `_exp_big.json` was fetched with lesson 91773801's play key over a list that mixed 91773801's
    # own names with 63aac7ca's. Only the 63aac7ca names are the wrong pairing: their ciphertext is
    # on disk from 63aac7ca's own download, and the token now attached to them came from another
    # lesson's play key. A name that is in both sets would prove nothing, so they are excluded.
    foreign = [name for name in wrong if name in victim and name not in right]
    print(f"{len(right)} player token(s) for 91773801, {len(victim)} for 63aac7ca; "
          f"{len(foreign)} token(s) issued for a 63aac7ca file under 91773801's play key")

    print("\n--- token fetched under the WRONG play key, against 63aac7ca's ciphertext ---")
    shown = 0
    for name in foreign:
        path = os.path.join(CACHE, name)
        if not os.path.exists(path):
            continue
        blob = open(path, "rb").read()
        if len(blob) % 16:
            continue
        plain = decrypt(blob, wrong[name], name)
        print(f"  {name[:46]}  sync {sync(plain) * 100:6.2f}%  ({len(plain)} B)")
        shown += 1
        if shown >= 3:
            break

    print("\n--- token the player itself used for the same lesson ---")
    shown = 0
    for name, tk in victim.items():
        path = os.path.join(CACHE, name)
        if not os.path.exists(path):
            continue
        blob = open(path, "rb").read()
        if len(blob) % 16:
            continue
        plain = decrypt(blob, tk, name)
        print(f"  {name[:46]}  sync {sync(plain) * 100:6.2f}%  ({len(plain)} B)")
        shown += 1
        if shown >= 3:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
