"""Replay a captured request body against the live API with a fresh token.

The captured request bodies are still valid requests: their signature covers their own fields, and
the server checks the token, not the age (a verbatim replay of the list request got past parsing and
signature and failed only on an expired token). So every endpoint whose *body* was captured can be
asked again without the player — all it needs is a token the player has just used, which
`capture_api.py` records.

What comes back is an envelope of ciphertext. Decrypting it is the point: some endpoints answer with
a **request descriptor** (`{"host", "req", "dkey", …}`), which names the key for that endpoint's
payloads, and the descriptor key is one of the two this project already has.

    python replay_bodies.py --token-from captured/tokens.jsonl [--endpoint getDownEVSKey]
"""

import argparse
import base64
import glob
import gzip
import hashlib
import io
import json
import os
import re
import sys
import urllib.request

from Crypto.Cipher import AES

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BODIES = os.path.join(HERE, "captured", "bodies")
KEYS = {"descriptor": b"11585ec1b1f8f30e", "data": b"x!@#y.cn_xnk0506"}
HOST = "https://en2v4.ieway.cn"


def fresh_token(path):
    """The most recent `authorization` the player sent, scheme included."""
    if not os.path.exists(path):
        raise SystemExit(f"no token capture at {path}; run capture_api.py while the player works")
    best = None
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("authorization"):
            best = record
    if not best:
        raise SystemExit("no authorization in the token capture")
    return best["authorization"]


def open_envelope(body):
    envelope = json.loads(body.decode("utf-8"))
    result = envelope.get("result")
    if not isinstance(result, str) or len(result) < 16:
        return envelope, None, None
    cipher = base64.b64decode(result)
    for name, key in KEYS.items():
        usable = cipher[: len(cipher) // 16 * 16]
        try:
            plain = AES.new(key, AES.MODE_ECB).decrypt(usable)
        except Exception:
            continue
        if plain[:2] == b"\x1f\x8b":
            try:
                return envelope, name, gzip.GzipFile(fileobj=io.BytesIO(plain)).read()
            except Exception:
                pass
        stripped = plain.lstrip()
        if stripped[:1] == b"{" and b"}" in plain:
            return envelope, name, plain[: plain.rindex(b"}") + 1]
    return envelope, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-from", default=os.path.join(HERE, "captured", "tokens.jsonl"))
    ap.add_argument("--endpoint", default="")
    ap.add_argument("--limit", type=int, default=3)
    args = ap.parse_args()

    token = fresh_token(args.token_from)
    print(f"token: {token[:40]}… ({len(token)} chars)")

    seen = {}
    for path in sorted(glob.glob(os.path.join(BODIES, "*.params"))):
        endpoint = os.path.basename(path).rsplit("-", 1)[0]
        if args.endpoint and endpoint != args.endpoint:
            continue
        seen.setdefault(endpoint, []).append(path)

    for endpoint, paths in seen.items():
        for path in paths[:args.limit]:
            raw = open(path, "rb").read()
            request = urllib.request.Request(
                HOST + "/student/" + endpoint, data=raw,
                headers={"content-type": "application/json", "accept": "*/*",
                         "user-agent": "restclient-cpp/@restclient-cpp_VERSION@",
                         "authorization": token})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read()
                status = response.status
            except Exception as error:
                print(f"\n{endpoint}: request failed: {type(error).__name__} {error}")
                continue
            envelope, key, plain = open_envelope(body)
            print(f"\n== {endpoint} ({os.path.basename(path)}) -> HTTP {status}, "
                  f"errcode={envelope.get('errcode')} errmsg={envelope.get('errmsg')!r} "
                  f"zip={envelope.get('zip')} encrypt={envelope.get('encrypt')}")
            if plain is None:
                result = envelope.get("result") or ""
                print(f"   undecrypted, {len(result)} b64 chars: {str(result)[:80]}")
                continue
            text = plain.decode("utf-8", "replace")
            print(f"   opened with the {key} key, {len(plain)} bytes:")
            print("   " + text[:400].replace("\n", " "))
            out = os.path.join(HERE, "captured", f"replay-{endpoint}.json")
            with open(out, "wb") as handle:
                handle.write(plain)
            print(f"   -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
