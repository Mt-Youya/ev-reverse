"""Extract the captured request bodies from captured/bodies.bin.

bodies.bin is a sequence of `\n===== TAG args =====\n` markers each followed by raw bytes, so it
can be split on the marker. The H2REQBODY sections are the encrypted `params` blobs posted to
the API — a known-ciphertext target for validating a recovered AES key.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "captured", "bodies.bin")
OUT = os.path.join(HERE, "captured", "bodies")
MARKER = re.compile(rb"\n===== (\S+) ?(.*?) =====\n")


def sections(blob):
    marks = list(MARKER.finditer(blob))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(blob)
        yield mark.group(1).decode(), mark.group(2).decode(), blob[mark.end():end]


def endpoint_of(header):
    match = re.search(r"POST (\S+)", header)
    return match.group(1) if match else ""


def main():
    with open(SRC, "rb") as handle:
        blob = handle.read()
    os.makedirs(OUT, exist_ok=True)

    counts = {}
    written = []
    for tag, header, payload in sections(blob):
        counts[tag] = counts.get(tag, 0) + 1
        if tag != "H2REQBODY" or not payload:
            continue
        endpoint = endpoint_of(header).split("/")[-1] or "unknown"
        index = counts[tag]
        name = "%s-%02d.params" % (endpoint, index)
        path = os.path.join(OUT, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        written.append((name, len(payload), payload[:16].hex()))

    print("sections:", ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print()
    print("wrote %d request bodies to %s" % (len(written), OUT))
    for name, size, head in written[:24]:
        print("  %-46s %5d bytes  %s..." % (name, size, head))


if __name__ == "__main__":
    main()
