"""Summarise the captured API traffic: what was called, how the envelope is shaped, and which
responses can be read.

The capture is `captured/bodies.bin`, a sequence of `\\n===== TAG header =====\\n` markers each
followed by raw bytes; the H2DATA sections are whole response bodies and the header names the
request they answer. Every response is an envelope with `zip` and `encrypt` flags around a base64
`result`, so the readable/unreadable split is a property of the flags rather than of the endpoint —
which is exactly what the summary needs to state.

    python api_summary.py
"""

import base64
import collections
import json
import os
import re
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "captured", "bodies.bin")
MARKER = re.compile(rb"\n===== (\S+) ?(.*?) =====\n")


def sections(blob):
    marks = list(MARKER.finditer(blob))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(blob)
        yield mark.group(1).decode(), mark.group(2).decode(), blob[mark.end():end]


def endpoint_of(header):
    match = re.search(r"POST (\S+)", header)
    return match.group(1) if match else header.strip()


def readable(result):
    """How far a `result` decodes: base64, then zlib, then UTF-8."""
    try:
        raw = base64.b64decode(result, validate=True)
    except Exception:
        return "not base64"
    try:
        text = zlib.decompress(raw).decode("utf-8")
        if text.lstrip()[:1] in "{[":
            return "base64 + zlib + json"
        return "base64 + zlib"
    except Exception:
        pass
    try:
        raw.decode("utf-8")
        return "base64 + utf8"
    except Exception:
        return "base64 only"


def main():
    blob = open(SRC, "rb").read()
    rows = collections.defaultdict(collections.Counter)
    examples = {}
    envelopes = collections.Counter()
    for tag, header, payload in sections(blob):
        if tag != "H2DATA":
            continue
        path = endpoint_of(header)
        try:
            env = json.loads(payload.decode("utf-8"))
        except Exception:
            rows[path]["<not an envelope>"] += 1
            continue
        result = env.get("result")
        flags = "zip=%s encrypt=%s" % (env.get("zip"), env.get("encrypt"))
        envelopes[flags] += 1
        if isinstance(result, str) and result:
            verdict = readable(result)
            rows[path][f"{flags}  {verdict}  ({len(result)} b64 chars)"] += 1
            examples.setdefault(path, (flags, verdict, result[:48]))
        else:
            rows[path][f"{flags}  empty result"] += 1

    print(f"{sum(sum(c.values()) for c in rows.values())} response body(ies), "
          f"{len(rows)} endpoint(s)\n")
    print("envelope flags seen:", dict(envelopes), "\n")
    for path in sorted(rows):
        print(f"== {path}")
        for key, count in rows[path].most_common():
            print(f"   {count:4d}  {key}")
        if path in examples:
            flags, verdict, head = examples[path]
            print(f"        e.g. {flags} -> {verdict}: {head!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
