"""Decrypt captured API response bodies offline, trying the keys the player was caught using.

Every response on this API is an envelope — `{"errcode", ..., "encrypt": 1, "result": "<base64>"}`
— and the `result` is base64 of ciphertext. `tools/parser-tools/capture_plaintext.py` catches, live,
the base64 input, the key and the plaintext of each decryption, so the keys are known; each endpoint
turns out to have its own constant, and this tool tries them all against the archive.

That makes captures taken before anyone could read them readable now, including `getDownEVSKey`
(ticket 08) and the response material that was ticket 06's whole subject.

    python decrypt_response.py                        # every envelope in captured/bodies.bin
    python decrypt_response.py --keys-from <jsonl>    # add keys captured live (default: all known)
    python decrypt_response.py --body <file.params>   # one captured request body
    python decrypt_response.py --envelope <file.bin>  # one captured plaintext payload

Readable payloads land in `captured/decrypted/`.
"""

import argparse
import base64
import gzip
import io
import json
import os
import re
import sys
import zlib

from Crypto.Cipher import AES

# The console on this machine is GBK, and decrypted payloads are not: a print that raises would
# stop the run one response in, which is how a tool reports nothing and looks like a failure of
# the decryption rather than of the terminal.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BODIES = os.path.join(HERE, "captured", "bodies.bin")
CAPTURED_KEYS = os.path.join(HERE, "captured", "plaintext_keys.jsonl")
OUT = os.path.join(HERE, "captured", "decrypted")
MARKER = re.compile(rb"\n===== (\S+) ?(.*?) =====\n")

# The first key caught live, kept so the tool still works without a capture of its own: this is
# the segment-list key, and it is a constant, not a session value.
KNOWN_KEYS = [b"x!@#y.cn_xnk0506", b"11585ec1b1f8f30e"]

# The request descriptors the player keeps — `{"req": "/student/...", "dkey": "x!@#y.cn_xnk0506",
# "dkey_ver": 202, "base_key": "9299133a..."}` — name both the key for a response and a 16-byte
# value that is the right size for an IV. Trying it costs one decrypt per response and is how a
# mode gets settled rather than assumed.
IV_CANDIDATES = [(b"\0" * 16, "iv=0"),
                 (bytes.fromhex("9299133a4bcc7c6281f4b77f24765e5c"), "iv=base_key")]


def keys(path=CAPTURED_KEYS):
    found = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                record = json.loads(line)
            except Exception:
                continue
            key = record.get("key")
            if isinstance(key, str) and key:
                raw = key.encode("utf-8", "replace")
                if raw not in found:
                    found.append(raw)
    for raw in KNOWN_KEYS:
        if raw not in found:
            found.append(raw)
    return found


def gunzip_prefix(plain):
    """Gunzip the stream at the front of `plain`, ignoring whatever follows it.

    The decrypted buffer is a fixed-size allocation whose tail is whatever was there before, so
    `gzip.decompress` on the whole buffer fails a few hundred bytes in — which reads as a wrong
    key when the key was right. The stream itself is well formed.
    """
    try:
        return gzip.GzipFile(fileobj=io.BytesIO(plain)).read()
    except Exception:
        return None


def decrypt(blob, key):
    """Ciphertext -> (kind, plaintext) for one key, across the modes the app could be using.

    ECB is what this build uses, measured rather than guessed: against a live `(ciphertext, key,
    plaintext)` triple caught at `0x1EA10`, ECB reproduces all 500 bytes and CBC diverges at byte
    16. The other modes stay in the list because they cost nothing and would catch a change.
    """
    usable = blob[: len(blob) // 16 * 16]
    attempts = [(AES.MODE_ECB, None, "ecb")]
    attempts += [(AES.MODE_CBC, iv, label) for iv, label in IV_CANDIDATES]
    for mode, iv, label in attempts:
        try:
            cipher = AES.new(key, mode) if iv is None else AES.new(key, mode, iv=iv)
            plain = cipher.decrypt(usable)
        except Exception:
            continue
        if plain[:2] == b"\x1f\x8b":
            text = gunzip_prefix(plain)
            if text is not None:
                return f"{label}+gzip", text
        if plain[:1] == b"\x78":
            try:
                return f"{label}+zlib", zlib.decompress(plain)
            except Exception:
                pass
        # A wrong key produces random bytes, and one in ~128 of them starts with `{` or `[`. Ask
        # for something that actually parses, or the tool reports a success that is not one.
        stripped = plain.lstrip()
        if stripped[:2] in (b'{"', b"[{"):
            try:
                json.loads(stripped.decode("utf-8", "strict"))
                return f"{label}+json", plain
            except Exception:
                pass
    return None, None


def best_effort(blob, candidates):
    for key in candidates:
        kind, plain = decrypt(blob, key)
        if kind:
            return key, kind, plain
    return None, "undecrypted", blob


def payload_of(envelope_bytes):
    try:
        envelope = json.loads(envelope_bytes.decode("utf-8"))
    except Exception:
        return None, None
    result = envelope.get("result")
    if not isinstance(result, str) or len(result) < 16:
        return envelope, None
    try:
        return envelope, base64.b64decode(result)
    except Exception:
        return envelope, None


def sections():
    blob = open(BODIES, "rb").read()
    marks = list(MARKER.finditer(blob))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(blob)
        yield mark.group(1).decode(), mark.group(2).decode(), blob[mark.end():end]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--body")
    ap.add_argument("--envelope", action="append", default=[])
    ap.add_argument("--keys-from", default=CAPTURED_KEYS)
    args = ap.parse_args()

    candidates = keys(args.keys_from)
    print(f"{len(candidates)} candidate key(s): "
          f"{[k.decode('utf-8', 'replace') for k in candidates]}")

    if args.body:
        envelope, cipher = payload_of(open(args.body, "rb").read())
        key, kind, plain = best_effort(cipher, candidates) if cipher else (None, "no result", b"")
        print(f"{args.body}: {kind} (key {key!r})")
        print(plain[:800].decode("utf-8", "replace"))
        return 0

    if args.envelope:
        for path in args.envelope:
            key, kind, plain = best_effort(open(path, "rb").read(), candidates)
            print(f"{os.path.basename(path)}: {kind} (key {key!r}) {len(plain)} bytes")
            print(plain[:800].decode("utf-8", "replace"))
        return 0

    os.makedirs(OUT, exist_ok=True)
    seen_endpoint_keys = {}
    readable = total = 0
    for tag, header, payload in sections():
        if tag != "H2DATA":
            continue
        endpoint = ((re.search(r"POST (\S+)", header) or [None, header])[1]).split("/")[-1]
        envelope, cipher = payload_of(payload)
        if cipher is None:
            continue
        total += 1
        key, kind, plain = best_effort(cipher, candidates)
        seen_endpoint_keys.setdefault(endpoint, set()).add(
            key.decode("utf-8", "replace") if key else None)
        text = plain[:120].decode("utf-8", "replace").replace("\n", " ")
        if key:
            readable += 1
            with open(os.path.join(OUT, f"{total:03d}-{endpoint}.json"), "wb") as handle:
                handle.write(plain)
        print(f"[{total:03d}] {endpoint:32s} {kind:14s} {len(cipher):6d} B  {text[:70]}")

    print(f"\n{readable}/{total} response(s) decrypted; readable ones in {OUT}")
    print("\nendpoint -> key(s) seen")
    for endpoint, found in sorted(seen_endpoint_keys.items()):
        print(f"  {endpoint:34s} {sorted(k or '(none)' for k in found)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
