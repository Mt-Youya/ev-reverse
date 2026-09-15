"""Find request descriptors offline: base64 blobs anywhere in the player that decrypt to one.

The player holds a descriptor per API request — `{"host", "req", "dkey", "dkey_ver", "cache_key",
"base_key", ...}` — and decrypts it with a constant key. If those descriptors ship *with* the
player rather than arriving from the network, then every endpoint's data key is knowable without
running anything, and a capture taken under an older `dkey_ver` becomes readable too.

So: walk a directory, pull every base64-looking run out of every file, and try the key on each.
A hit is a blob that decrypts to something starting with `{` and parsing as JSON.

    python find_descriptors.py --root "D:\\Learning\\EVPlayer2"
    python find_descriptors.py --root "$env:LOCALAPPDATA\\EVPlayer2" --min 24
"""

import argparse
import base64
import binascii
import gzip
import io
import json
import os
import re
import sys

from Crypto.Cipher import AES

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The constant that opens a request descriptor, caught live at 0x1EA10.
KEY = b"11585ec1b1f8f30e"

BASE64_RUN = re.compile(rb"[A-Za-z0-9+/]{24,}={0,2}")


def candidates(blob):
    """Everything a base64 run in `blob` could decode to, at either alignment."""
    for match in BASE64_RUN.finditer(blob):
        text = match.group()
        for start in (0, 1, 2, 3):
            chunk = text[start:]
            chunk = chunk[: len(chunk) // 4 * 4]
            if len(chunk) < 24:
                continue
            try:
                yield match.start() + start, base64.b64decode(chunk)
            except (binascii.Error, ValueError):
                continue


def looks_like_descriptor(plain):
    stripped = plain.lstrip(b"\x00 \t\r\n")
    if not stripped.startswith(b"{"):
        return None
    end = stripped.find(b"}")
    if end < 0:
        return None
    try:
        return json.loads(stripped[: end + 1].decode("utf-8"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--key", default=KEY.decode("ascii"))
    ap.add_argument("--min", type=int, default=24, help="shortest base64 run to try")
    ap.add_argument("--max-file", type=int, default=200 * 1024 * 1024)
    args = ap.parse_args()

    key = args.key.encode()
    cipher = AES.new(key[:16], AES.MODE_ECB)
    found = 0
    scanned = 0

    for base, _, files in os.walk(args.root):
        for name in files:
            path = os.path.join(base, name)
            try:
                if os.path.getsize(path) > args.max_file:
                    continue
                blob = open(path, "rb").read()
            except OSError:
                continue
            scanned += 1
            for offset, decoded in candidates(blob):
                if len(decoded) % 16:
                    continue
                plain = cipher.decrypt(decoded)
                doc = looks_like_descriptor(plain)
                if doc is None:
                    # try the same bytes as a gzip stream, which some payloads are
                    try:
                        plain = gzip.GzipFile(fileobj=io.BytesIO(plain)).read()
                    except Exception:
                        continue
                    doc = looks_like_descriptor(plain)
                if doc is None:
                    continue
                found += 1
                print(f"{path} +{offset:#x}: {json.dumps(doc, ensure_ascii=False)[:300]}")
    print(f"\n{found} descriptor(s) in {scanned} file(s) under {args.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
