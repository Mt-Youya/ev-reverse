"""Map harvested keys to the files they belong to, without testing every key against every file.

The player sets up a segment's key whenever it decrypts that segment, so watching it play is a source
of keys that needs no API, no token and no list -- but a key arrives with no name attached, and the
only thing that ties it to a file is that it opens it. Testing K keys against F files naively re-masks
every file's header for every key (the mask depends on the name, not the key), which is where the
time goes. Masking each header once and then running only the AES step per key turns the expensive
part into a cheap one: 576 bytes twenty thousand times is a fifth of a second, not a minute.

    python map_keys.py [--source captured/player_keys.jsonl] [--recent-hours 6]
"""

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"D:\Downloads\EVPlayer2Downloads"
MAPPED = os.path.join(HERE, "captured", "key_files.json")
LIBRARY = os.path.join(HERE, "captured", "keys_merged.json")


def masked_heads(names, cache_dir, head=576):
    """Read each file's first packets and apply the name mask once, keeping the result in memory."""
    out = []
    for name in names:
        path = os.path.join(cache_dir, name)
        try:
            with open(path, "rb") as handle:
                blob = handle.read(head)
        except OSError:
            continue
        if len(blob) < head:
            continue
        mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
        out.append((name, bytes(b ^ mask[i % 16] for i, b in enumerate(blob))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join(HERE, "captured", "player_keys.jsonl"))
    ap.add_argument("--cache", default=CACHE_DIR)
    ap.add_argument("--recent-hours", type=float, default=0,
                    help="only consider files written this recently (0 = all of them)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from Crypto.Cipher import AES

    keys = []
    if os.path.exists(args.source):
        for line in open(args.source, encoding="utf-8"):
            try:
                record = json.loads(line)
            except Exception:
                continue
            key = record.get("key")
            if key and key not in keys:
                keys.append(key)
    print(f"{len(keys)} distinct harvested key(s)")

    mapped = json.load(open(MAPPED, encoding="utf-8")) if os.path.exists(MAPPED) else {}
    already = {key for key in keys if key in mapped}
    todo = [key for key in keys if key not in mapped]
    print(f"{len(already)} already mapped, {len(todo)} to place")

    names = sorted(n for n in os.listdir(args.cache) if n.endswith(".ts"))
    if args.recent_hours:
        cutoff = time.time() - args.recent_hours * 3600
        names = [n for n in names
                 if os.path.getmtime(os.path.join(args.cache, n)) > cutoff]
        print(f"{len(names)} file(s) written in the last {args.recent_hours:.0f}h")
    if args.limit:
        names = names[:args.limit]

    print("masking headers once ...", flush=True)
    heads = masked_heads(names, args.cache)
    print(f"{len(heads)} header(s) prepared", flush=True)

    placed = 0
    for key in todo:
        cipher = AES.new(key.encode(), AES.MODE_ECB)
        for name, masked in heads:
            plain = cipher.decrypt(masked)
            if plain[0] == 0x47 and plain[188] == 0x47 and plain[376] == 0x47:
                mapped[key] = name
                placed += 1
                print(f"  {key} -> {name}")
                break
        else:
            continue

    if placed:
        with open(MAPPED, "w", encoding="utf-8") as handle:
            json.dump(mapped, handle, indent=1, sort_keys=True)
        library = json.load(open(LIBRARY, encoding="utf-8")) if os.path.exists(LIBRARY) else {}
        added = 0
        for key, name in mapped.items():
            if name and name not in library:
                library[name] = key
                added += 1
        with open(LIBRARY, "w", encoding="utf-8") as handle:
            json.dump(library, handle, indent=1, sort_keys=True)
        print(f"\n{placed} key(s) placed; {added} new file(s) added to the library "
              f"({len(library)} total)")
    else:
        print("\nno new key could be placed against the files on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
