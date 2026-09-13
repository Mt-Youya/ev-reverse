"""Test the hook-captured AES keys against real encrypted data.

Two targets, both decisive:

1. Segments the player downloaded to disk. If one of these keys opens one, the download path
   *does* hold segment keys, and the "a lesson must be played before it can be decrypted"
   constraint is dead.
2. The `params` blobs captured from the API, which are AES-ECB over JSON. If one opens those,
   we can read and write the API directly.

The segment scheme is: XOR with MD5(filename)[:16] as ASCII, then AES-256-ECB, then strip '#'.
Only the first 376 bytes are needed to check the MPEG-TS sync bytes, which keeps this fast.
"""

import base64
import glob
import hashlib
import json
import os
import re
import sys

from Crypto.Cipher import AES

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = os.path.join(HERE, "captured", "aes_keys.json")
DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
HEAD = 376  # two TS packets: enough to prove the key


def load_keys():
    """Read whatever has been written so far.

    The hook rewrites this file on every capture, so a read can land mid-write and the JSON will
    not parse. Pulling the hex fields out textually tolerates that.
    """
    with open(KEYS, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    seen = []
    for match in re.finditer(r'"key"\s*:\s*"([0-9a-fA-F]+)"', text):
        raw = match.group(1)
        if len(raw) in (32, 48, 64):
            seen.append(bytes.fromhex(raw))
    if not seen:
        raise SystemExit("no keys parsed from %s" % KEYS)
    return seen


def mask_of(name):
    return hashlib.md5(name.encode()).hexdigest()[:16].encode()


def opens_segment(key, path):
    name = os.path.basename(path)
    with open(path, "rb") as handle:
        raw = handle.read(HEAD)
    if len(raw) < HEAD:
        return None
    unmasked = bytes(b ^ mask_of(name)[i % 16] for i, b in enumerate(raw))
    try:
        plain = AES.new(key, AES.MODE_ECB).decrypt(unmasked)
    except Exception:
        return None
    if plain[0] == 0x47 and plain[188] == 0x47:
        return plain
    return None


def opens_params(key, blob):
    try:
        plain = AES.new(key, AES.MODE_ECB).decrypt(blob)
    except Exception:
        return None
    if plain[:1] == b"{" and b'"' in plain[:64]:
        return plain
    return None


def main():
    keys = load_keys()
    print("loaded %d distinct keys" % len(set(keys)))
    hits = 0

    segments = sorted(glob.glob(os.path.join(DOWNLOADS, "*.ts")))[:6]
    if not segments:
        print("no downloaded segments found in %s" % DOWNLOADS)
    else:
        print("\n-- against %d downloaded segment(s) --" % len(segments))
        for path in segments:
            name = os.path.basename(path)
            for key in keys:
                plain = opens_segment(key, path)
                if plain:
                    hits += 1
                    print("*** SEGMENT OPENS ***")
                    print("    file   : %s" % name)
                    print("    key    : %s" % key.hex())
                    print("    head   : %s" % plain[:32].hex())
                    break
            else:
                continue

    # Fresh params captured in this same session.
    newest = sorted(glob.glob(os.path.join(HERE, "captured", "bodies", "*.params")),
                    key=os.path.getmtime, reverse=True)
    if newest:
        blob = base64.b64decode(json.loads(open(newest[0], "rb").read())["params"])
        print("\n-- against %s (%d bytes) --" % (os.path.basename(newest[0]), len(blob)))
        for key in keys:
            plain = opens_params(key, blob)
            if plain:
                hits += 1
                print("*** PARAMS OPENS ***")
                print("    key  : %s" % key.hex())
                print("    plain: %s" % plain[:300])
                break
        else:
            print("    no key from the hook opens it")

    print("\n%d hit(s)" % hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
