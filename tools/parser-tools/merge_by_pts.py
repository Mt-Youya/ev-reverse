"""Merge a lesson's segments in playback order, using the order the stream itself carries.

Capture order is not playback order: the player downloads several segments at once and several
sessions' payloads interleave, so a merge built on "the order the lists arrived" produces a file
whose every 188-byte packet starts with 0x47 and whose video is still scrambled -- 29,398 decode
errors and 28,510 reference-frame errors on a 28-minute lesson, none of which the sync-byte check
can see.

What does carry the order is each segment's own PTS. Decrypt the segment, read the first video
packet's presentation timestamp, sort on it, and the stream reassembles itself. This tool does that
and nothing else:

    python merge_by_pts.py --lesson <lesson-uuid> --cache D:\\Downloads\\EVPlayer2Downloads

Segments are found from the captured lists (same sources as `export_cached.py`), decrypted with the
documented scheme -- key = MD5_hex(tk + filename + "20220507"), plaintext = AES-256-ECB-decrypt(
ciphertext XOR MD5(filename)[:16]) -- and reported with their PTS so a gap or a duplicate is visible
rather than assumed away.
"""

import argparse
import hashlib
import os
import struct
import sys

from Crypto.Cipher import AES

from export_cached import collect

HERE = os.path.dirname(os.path.abspath(__file__))


def decrypt(blob, tk, name):
    """The documented scheme, with both of its easy-to-get-wrong details.

    The key is the 32-character hex *string* used directly as 32 bytes, and the mask is the first 16
    characters of `MD5(filename)` **hex** -- not the 16 raw digest bytes. Using the raw digest
    produces a file whose packets are 0.1% aligned instead of 100%, which reads like a wrong key
    rather than a wrong mask. The plaintext also ends in `#` padding up to the AES block, which has
    to come off before the stream is a whole number of 188-byte packets.
    """
    key = hashlib.md5((tk + name + "20220507").encode()).hexdigest().encode()
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    masked = bytes(b ^ mask[i % 16] for i, b in enumerate(blob))
    plain = AES.new(key, AES.MODE_ECB).decrypt(masked)
    padding = len(plain) % 188
    if 0 < padding <= 15 and plain[-padding:] == b"#" * padding:
        plain = plain[:-padding]
    return plain


def first_pts(stream):
    """The presentation timestamp of the first video packet, in 90 kHz units.

    Reads the PES header of whichever PID carries video, rather than resolving the PID through the
    PAT and PMT: a segment cut for HTTP delivery usually begins mid-program and carries no program
    tables at all, so a lookup that waits for them finds nothing and reports every segment as
    unusable.
    """
    for offset in range(0, len(stream) - 187, 188):
        packet = stream[offset:offset + 188]
        if packet[0] != 0x47:
            continue
        if not packet[1] & 0x40:                  # payload-unit start only
            continue
        # Where the payload begins depends on the adaptation-field control bits, and a video packet
        # usually carries an adaptation field: assuming the payload starts at byte 4 reads the
        # adaptation bytes as if they were a PES header, which is why every segment reported "no
        # video PTS" while the decryption itself checked out at 100%.
        control = (packet[3] >> 4) & 0x03
        if control in (0, 2):                     # reserved, or adaptation field only
            continue
        start = 4 + (1 + packet[4] if control == 3 else 0)
        payload = packet[start:]
        if payload[:3] != b"\x00\x00\x01":
            continue
        stream_id = payload[3]
        if not 0xE0 <= stream_id <= 0xEF:          # video elementary stream
            continue
        flags = payload[7]
        if not flags & 0x80:                       # no PTS in this header
            continue
        stamp = payload[9:14]
        if len(stamp) < 5:
            continue
        return (((stamp[0] >> 1) & 0x07) << 30) | (stamp[1] << 22) \
            | (((stamp[2] >> 1) & 0x7F) << 15) | (stamp[3] << 7) | ((stamp[4] >> 1) & 0x7F)
    return None


def sync_ratio(stream):
    """What fraction of 188-byte packets start with the sync byte -- the decryption self-check."""
    total = len(stream) // 188
    if not total:
        return 0.0
    good = sum(1 for i in range(0, total * 188, 188) if stream[i] == 0x47)
    return good / total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", required=True)
    ap.add_argument("--cache", default=r"D:\Downloads\EVPlayer2Downloads")
    ap.add_argument("--out", default=r"D:\ev-export")
    args = ap.parse_args()

    lessons, _sources = collect()
    entries = None
    for lesson, value in lessons.items():
        if lesson.startswith(args.lesson):
            entries = value
            args.lesson = lesson
            break
    if not entries:
        print(f"no captured list names a lesson starting with {args.lesson}")
        return 1

    cache = set(os.listdir(args.cache))
    present = {name: value for name, value in entries.items() if name in cache}
    print(f"lesson {args.lesson}: {len(present)} of {len(entries)} captioned segment(s) on disk")

    placed = []
    failed = []
    ratios = []
    for name, (_window, _idx, tk) in present.items():
        try:
            plain = decrypt(open(os.path.join(args.cache, name), "rb").read(), tk, name)
        except Exception as error:
            failed.append((name, f"decrypt: {error}"))
            continue
        ratios.append(sync_ratio(plain))
        pts = first_pts(plain)
        if pts is None:
            failed.append((name, "no video PTS"))
            continue
        placed.append((pts, name, plain))

    if ratios:
        print(f"decryption self-check: sync byte in {min(ratios) * 100:.2f}%..{max(ratios) * 100:.2f}% "
              f"of packets over {len(ratios)} segment(s)")

    placed.sort(key=lambda item: item[0])
    print(f"{len(placed)} segment(s) placed, {len(failed)} unusable")
    for name, why in failed[:8]:
        print(f"  {name}: {why}")

    if not placed:
        return 1

    # A gap or a repeat in PTS is the signal that the set is not a whole lesson, so print the shape
    # of the timeline rather than only the total.
    step = placed[1][0] - placed[0][0] if len(placed) > 1 else 0
    gaps = [(placed[i][1], placed[i + 1][0] - placed[i][0])
            for i in range(len(placed) - 1)
            if step and abs((placed[i + 1][0] - placed[i][0]) - step) > step // 2]
    print(f"first PTS {placed[0][0]} ({placed[0][0] / 90000:.3f}s), "
          f"last {placed[-1][0]} ({placed[-1][0] / 90000:.3f}s), typical step "
          f"{step / 90000:.3f}s")
    if gaps:
        print(f"{len(gaps)} gap(s) or repeat(s) in the timeline, first few:")
        for name, delta in gaps[:6]:
            print(f"  after {name}: {delta / 90000:+.3f}s")

    os.makedirs(args.out, exist_ok=True)
    target = os.path.join(args.out, f"{args.lesson}.pts.ts")
    with open(target, "wb") as handle:
        for _pts, _name, plain in placed:
            handle.write(plain)
    print(f"wrote {target}  ({os.path.getsize(target)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
