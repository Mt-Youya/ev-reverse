"""Structural variants of the signature preimage, scored against captured request bodies.

The preimage the player builds is known — fields joined by `&`, seen live at `0x1FD60` — but the
signature it produces does not match a plain `MD5` of that string rebuilt from a captured body.
So one of the *values* or the *shape* differs between what is signed and what is sent. This tries
the shapes and the value substitutions that the binary itself suggests: the string table next to
`need_zip` holds `1.0.0` as well as `windows`, so the signed `app_version` may not be the sent one.

    python sign_variants.py
"""

import base64
import glob
import hashlib
import itertools
import json
import sys
import urllib.parse

from Crypto.Cipher import AES

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BODIES = "tools/parser-tools/captured/bodies/*.params"
KEY = b"x!@#y.cn_xnk0506"
SIGNED = ["app_version", "evs_playkey", "need_zip", "os_name", "platform", "platform_type",
          "req_time", "ts_liststr"]


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
        if "sign" in doc and "ts_liststr" in doc:
            out.append(doc)
    return out


def value_variants(doc, name):
    """What the signed string might use for one field, beyond what the body carries."""
    raw = str(doc[name])
    options = [raw]
    if name == "app_version":
        options += ["1.0.0", "5.0.5", "2.0.0", "200"]
    if name == "os_name":
        options += ["Windows", "win", "1"]
    if name in ("platform", "platform_type", "need_zip", "type"):
        options += [raw, f'"{raw}"']
    return list(dict.fromkeys(options))


def main():
    docs = bodies()
    print(f"{len(docs)} signed request(s)")
    sample = docs[0]
    print("fields:", sorted(sample))

    orders = {
        "alphabetical": SIGNED,
        "reverse": list(reversed(SIGNED)),
        "type-first": ["type"] + SIGNED,
        "type-last": SIGNED + ["type"],
        "playkey-first": ["evs_playkey"] + [f for f in SIGNED if f != "evs_playkey"],
    }
    traits = list(itertools.product(["&", "", "|"], ["", "&"], [False, True]))
    # (separator, trailing, url-encode values)

    tried = 0
    hits = []
    for order_name, order in orders.items():
        for separator, trailing, encode in traits:
            # only vary app_version/os_name; the rest are large and unlikely to be substituted
            for app, osname in itertools.product(value_variants(sample, "app_version"),
                                                  value_variants(sample, "os_name")):
                def render(doc, app=app, osname=osname, order=order, separator=separator,
                           trailing=trailing, encode=encode):
                    parts = []
                    for name in order:
                        if name not in doc:
                            continue
                        value = app if name == "app_version" else (
                            osname if name == "os_name" else str(doc[name]))
                        if encode:
                            value = urllib.parse.quote(value, safe="")
                        parts.append(f"{name}={value}")
                    return separator.join(parts) + trailing

                tried += 1
                if hashlib.md5(render(sample).encode()).hexdigest() != sample["sign"]:
                    continue
                agree = sum(1 for doc in docs
                            if hashlib.md5(render(doc).encode()).hexdigest() == doc["sign"])
                hits.append((order_name, separator, trailing, encode, app, osname, agree))
                print(f"FIT order={order_name} sep={separator!r} trailing={trailing!r} "
                      f"encode={encode} app={app} os={osname} agreeing={agree}/{len(docs)}")
    print(f"{tried} variant(s) tried; {len(hits)} fit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
