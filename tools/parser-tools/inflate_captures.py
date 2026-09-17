"""Squeeze the remaining lesson lists out of the API captures that were never inflated.

Keys come from `tk`, `tk` comes from a lesson list, and every list we have was mined from the files the
capture tool has already inflated. But the raw capture directories hold payloads too -- request and
response bodies, some gzipped, some plain -- and a list that is sitting there un-inflated is keys we
already paid for and never collected. This walks those bodies, inflates whatever is compressed, keeps
any document carrying a `k_l` array, and writes it where the key miner looks.

    python inflate_captures.py [--write captured/inflated] [--dry-run]
"""

import argparse
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURED = os.path.join(HERE, "captured")


def candidates():
    for directory, _, files in os.walk(os.path.join(CAPTURED, "api")):
        for name in files:
            if name.endswith(".json") and name.startswith("index"):
                continue
            yield os.path.join(directory, name)
    for name in ("bodies.bin", "payloads.bin"):
        path = os.path.join(CAPTURED, name)
        if os.path.exists(path):
            yield path


def bodies(blob):
    """Yield every readable document inside a capture file: the whole blob, and each gzip member."""
    yield blob
    start = blob.find(b"\x1f\x8b\x08")
    while start != -1:
        try:
            yield gzip.decompress(blob[start:])
        except Exception:
            pass
        start = blob.find(b"\x1f\x8b\x08", start + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default=os.path.join(CAPTURED, "inflated"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seen, kept, scanned = set(), 0, 0
    for path in candidates():
        try:
            if os.path.getsize(path) > 32 * 1024 * 1024:
                continue
            blob = open(path, "rb").read()
        except OSError:
            continue
        scanned += 1
        for document in bodies(blob):
            if b"k_l" not in document:
                continue
            try:
                parsed = json.loads(document.decode("utf-8", "replace"))
            except Exception:
                continue
            entries = parsed.get("k_l") if isinstance(parsed, dict) else None
            if not isinstance(entries, list) or not entries:
                continue
            names = {str(item.get("sf", "")).split("?")[0].rsplit("/", 1)[-1]
                     for item in entries if isinstance(item, dict)}
            signature = (frozenset(names), len(entries))
            if signature in seen:
                continue
            seen.add(signature)
            kept += 1
            print(f"  {len(entries):4d} entr(ies) from {os.path.relpath(path, CAPTURED)}")
            if not args.dry_run:
                os.makedirs(args.write, exist_ok=True)
                target = os.path.join(args.write, f"mined_{len(seen):03d}.json")
                with open(target, "w", encoding="utf-8") as handle:
                    json.dump(parsed, handle)

    print(f"\n{scanned} capture file(s) scanned, {kept} distinct list(s) found")
    if kept and not args.dry_run:
        print(f"written under {args.write}; run key_inventory.py to fold their tk into the library")
    return 0


if __name__ == "__main__":
    sys.exit(main())
