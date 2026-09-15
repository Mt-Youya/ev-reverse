"""Search a dictionary of strings captured from the player's memory for the third input of
`MD5hex(tk + filename + extra)`.

The shape of the derivation is read off the code; the third input is a runtime value this build
fetches locally. If that value is a 32-hex token it is already in `captured/keys.txt`, which is a
dump of exactly such strings, so the whole dictionary can be scored against real (tk, filename,
key) triples in one pass.

    python search_third.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pair_capture import key_sources, response_sources  # noqa: E402


def dictionary(bench):
    words = set()
    path = os.path.join(bench, "captured", "keys.txt")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            word = line.strip()
            if word:
                words.add(word)
    return sorted(words)


def main():
    bench = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "tools", "parser-tools")
    keys = key_sources()
    responses = response_sources()
    shared = sorted(set(keys) & set(responses))
    if not shared:
        print("no filename appears in both a key list and a response")
        return 1

    words = dictionary(bench)
    print(f"{len(shared)} paired segment(s), {len(words)} dictionary word(s)")

    import hashlib
    # md5(prefix + word) for every word, once per pair prefix
    hits = []
    for name in shared:
        rec = responses[name]
        tk = rec["tk"]
        prefix = (tk + name).encode()
        for origin, key in keys[name].items():
            for word in words:
                digest = hashlib.md5(prefix + word.encode()).hexdigest()
                if digest == key:
                    hits.append((name, origin, word))
    print(f"hits (tk+file+word): {len(hits)}")
    for hit in hits[:10]:
        print("   ", hit)

    # the other orders, over the same dictionary
    for label, build in (
        ("word+tk+file", lambda tk, n, w: w + tk + n),
        ("tk+word+file", lambda tk, n, w: tk + w + n),
        ("file+tk+word", lambda tk, n, w: n + tk + w),
        ("word+file+tk", lambda tk, n, w: w + n + tk),
    ):
        hits = []
        for name in shared[:60]:
            rec = responses[name]
            tk = rec["tk"]
            for origin, key in keys[name].items():
                for word in words:
                    if hashlib.md5(build(tk, name, word).encode()).hexdigest() == key:
                        hits.append((name, origin, word))
        print(f"hits ({label}): {len(hits)}")
        for hit in hits[:5]:
            print("   ", hit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
