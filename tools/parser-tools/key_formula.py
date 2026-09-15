"""Test candidate derivations for a segment key against contexts recorded from a live player.

`ctx_dump.json` holds one record per playback context: the filename, whatever the dump called
"token", the XOR mask, and the raw 32-byte schedule. The schedule is not the key verbatim — the
player keeps `key[0..16]` and MixColumns's `key[16..32]` — so each record's key is rebuilt with
the same arithmetic the Rust product uses (`evmedia-core::crypto::schedule_to_key`), and that
rebuilt key is the target every candidate is scored against.

    python key_formula.py ctx_dump.json
"""

import argparse
import hashlib
import json
import sys


def xtime(value):
    return ((value << 1) & 0xFF) ^ (0x1B if value & 0x80 else 0)


def mix(data):
    """Inverse of the MixColumns step the player applies to the second half of the schedule."""
    out = bytearray(16)
    for base in range(0, 16, 4):
        t = data[base] ^ data[base + 1] ^ data[base + 2] ^ data[base + 3]
        for i in range(4):
            out[base + i] = data[base + i] ^ t ^ xtime(data[base + i] ^ data[base + (i + 1) % 4])
    return bytes(out)


def schedule_to_key(schedule_hex):
    raw = bytes.fromhex(schedule_hex)
    if len(raw) != 32 or raw[:4] == bytes([0x0D, 0xF0, 0xAD, 0xBA]) or not any(raw):
        return None
    key = raw[:16] + mix(raw[16:])
    try:
        text = key.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if len(text) == 32 and all(c in "0123456789abcdef" for c in text) else None


def md5_hex(value):
    if isinstance(value, str):
        value = value.encode()
    return hashlib.md5(value).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    args = ap.parse_args(argv)

    records = json.load(open(args.dump, encoding="utf-8"))
    print(f"{len(records)} context(s) in {args.dump}")

    pairs = []
    for rec in records:
        key = schedule_to_key(rec["schedule"])
        if key:
            pairs.append((rec, key))
    print(f"{len(pairs)} of them hold a key")

    candidates = {
        "md5(token_text + file)": lambda r: md5_hex(r["token"] + r["file"]),
        "md5(file + token_text)": lambda r: md5_hex(r["file"] + r["token"]),
        "md5(token_raw + file)": lambda r: md5_hex(bytes.fromhex(r["token"]) + r["file"].encode()),
        "md5(file + token_raw)": lambda r: md5_hex(r["file"].encode() + bytes.fromhex(r["token"])),
        "md5(file)": lambda r: md5_hex(r["file"]),
        "token_text itself": lambda r: r["token"],
        "token_text + file": lambda r: r["token"] + r["file"],
        "md5(mask_text + file)": lambda r: md5_hex(r["mask"] + r["file"]),
    }
    score = {name: 0 for name in candidates}
    shown = 0
    for rec, key in pairs:
        for name, fn in candidates.items():
            try:
                if fn(rec) == key:
                    score[name] += 1
            except Exception:
                pass
        if shown < 3:
            shown += 1
            print(f"  {rec['file']}\n    key     {key}\n    token   {rec['token']}"
                  f"\n    schedule {rec['schedule']}")
    print("\ncandidate                                        matches")
    for name, count in sorted(score.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<46} {count}/{len(pairs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
