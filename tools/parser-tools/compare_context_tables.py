"""Compare the whole CABAC context-initialisation table between the player's decoder and stock FFmpeg.

The table's first three pairs match everywhere, which says the start of it was not touched and nothing
about the other 1021 entries. The protection being looked for lives somewhere in this decoder, and a
context-init table that agrees for six bytes and differs after would be exactly the sort of thing that
makes one decoder read a stream no other decoder can -- so the comparison has to be over the whole
table, entry by entry.

Both files hold the table as int8 pairs, one pair per context, so a window of 2048 bytes is the 1024
entries of one `cabac_context_init` table.

    python compare_context_tables.py <player dll> <stock ffmpeg> [more stock builds]
"""

import sys

PAIR = bytes([20 & 0xFF, (-15) & 0xFF, 2, 54, 3, 74])
WINDOW = 2048


def windows(blob, limit=8):
    out, at = [], blob.find(PAIR)
    while at != -1 and len(out) < limit:
        out.append(at)
        at = blob.find(PAIR, at + 1)
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    dll = open(sys.argv[1], "rb").read()
    dll_at = windows(dll)
    print(f"{sys.argv[1]}: table starts at {[hex(a) for a in dll_at]}")

    for path in sys.argv[2:]:
        stock = open(path, "rb").read()
        stock_at = windows(stock)
        print(f"\n{path}: table starts at {[hex(a) for a in stock_at]}")
        for mine in dll_at:
            for theirs in stock_at:
                left = dll[mine:mine + WINDOW]
                right = stock[theirs:theirs + WINDOW]
                if len(left) < WINDOW or len(right) < WINDOW:
                    continue
                if left == right:
                    print(f"  dll 0x{mine:x} == stock 0x{theirs:x}: all {WINDOW} bytes identical")
                    continue
                first = next(i for i in range(WINDOW) if left[i] != right[i])
                differing = sum(1 for i in range(WINDOW) if left[i] != right[i])
                print(f"  dll 0x{mine:x} vs stock 0x{theirs:x}: {differing} of {WINDOW} bytes differ, "
                      f"first at +{first} (pair {first // 2}): dll {left[first:first + 6].hex(' ')}, "
                      f"stock {right[first:first + 6].hex(' ')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
