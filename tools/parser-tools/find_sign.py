"""Recover the request signature from (fields, sign) pairs, given the format the player showed.

The live preimage caught at `0x1FD60` is `app_version=5.0.5&evs_playkey=...&...&ts_liststr=...` —
name order, `&`-joined — but the hook truncated it at 512 characters, so the *tail* of the signed
string (and therefore what it hashes) is still open. What is not open is that the player hashes a
string, and the captured request bodies carry both the fields and the server-accepted signature.

So: every subset of the request's fields in name order, with and without `type`, and with a small
set of candidate secrets before or after — scored against 238 real pairs. A hit on one pair means
nothing; a hit on all of them is the algorithm.

    python find_sign.py
"""

import base64
import glob
import hashlib
import itertools
import json
import sys

from Crypto.Cipher import AES

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BODIES = "tools/parser-tools/captured/bodies/*.params"
KEY = b"x!@#y.cn_xnk0506"
SECRETS = ["", "20220507", "x!@#y.cn_xnk0506", "11585ec1b1f8f30e", "5.0.5", "evplayer", "evs",
           "0", "1", "202", "-1"]
FIELDS = ["app_version", "evs_playkey", "need_zip", "os_name", "platform", "platform_type",
          "req_time", "type", "ts_liststr"]


def bodies():
    out = []
    for path in sorted(glob.glob(BODIES)):
        try:
            envelope = json.load(open(path, encoding="utf-8", errors="replace"))
            blob = base64.b64decode(envelope["params"])
            text = AES.new(KEY, AES.MODE_ECB).decrypt(blob).decode("utf-8", "ignore")
            doc = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception:
            continue
        if "sign" in doc:
            out.append(doc)
    return out


def candidate(doc, subset, secret, where):
    joined = "&".join(f"{name}={doc[name]}" for name in subset)
    if where == "prefix":
        return secret + joined
    if where == "suffix":
        return joined + secret
    return joined


def main():
    docs = bodies()
    print(f"{len(docs)} signed request(s)")
    if not docs:
        return 1
    sample = docs[0]

    subsets = []
    for size in range(1, len(FIELDS) + 1):
        subsets.extend(itertools.combinations(FIELDS, size))
    print(f"{len(subsets)} subset(s) x {len(SECRETS)} secret(s) x 3 placements")

    tried = 0
    for subset in subsets:
        for secret in SECRETS:
            for where in ("prefix", "suffix", "none"):
                tried += 1
                if hashlib.md5(candidate(sample, subset, secret, where).encode()).hexdigest() \
                        != sample["sign"]:
                    continue
                agree = sum(1 for doc in docs
                            if hashlib.md5(candidate(doc, subset, secret, where).encode())
                            .hexdigest() == doc["sign"])
                print(f"FIT subset={subset} secret={secret!r} where={where} "
                      f"agreeing={agree}/{len(docs)}")
    print(f"{tried} candidate(s) tried; no fit printed means none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
