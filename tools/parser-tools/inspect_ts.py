"""Structural inventory of a transport stream: PIDs, NAL types, and where the video really is.

The grey-picture lesson files are structurally perfect -- 100% sync, clean AAC, parsable SPS/PPS --
so nothing about the container explains why the picture is flat. This asks the container a narrower
question instead: which PIDs actually carry data, which of them carry H.264, and what NAL types are
present. A stream whose slides live in a second elementary stream, or whose slices are all one type,
looks different here from one that decodes normally.

    python inspect_ts.py <file.ts> [<file.ts> ...]
"""

import collections
import os
import sys

SYNC = 0x47
PACKET = 188
NAL_NAMES = {1: "non-IDR slice", 5: "IDR slice", 6: "SEI", 7: "SPS", 8: "PPS",
             9: "AUD", 10: "end seq", 11: "end stream", 12: "filler"}


def packets(path):
    blob = open(path, "rb").read()
    if len(blob) % PACKET:
        yield None, None, b"", None            # length not a whole number of packets
        return
    for i in range(0, len(blob), PACKET):
        p = blob[i:i + PACKET]
        if p[0] != SYNC:
            yield None, None, b"", p
            continue
        pid = ((p[1] & 0x1F) << 8) | p[2]
        pusi = bool(p[1] & 0x40)
        afc = (p[3] >> 4) & 0x3
        off = 4
        if afc & 0x2:
            off += 1 + p[4]
        if not (afc & 0x1) or off >= PACKET:
            yield pid, pusi, b"", p
            continue
        yield pid, pusi, p[off:], p


def elementary_streams(path):
    """Reassemble each PID's payload, honouring PES headers on payload-unit start."""
    streams = collections.defaultdict(bytearray)
    pes_ids = {}
    for pid, pusi, payload, _ in packets(path):
        if pid is None or not payload:
            continue
        if pusi:
            if len(payload) < 9 or payload[0] != 0 or payload[1] != 0 or payload[2] != 1:
                continue
            pes_ids.setdefault(pid, payload[3])
            header = 9 + payload[8]
            payload = payload[header:]
        if pid in pes_ids:
            streams[pid] += payload
    return streams, pes_ids


def nals(es):
    """Split Annex-B and yield (type, size, offset)."""
    starts = []
    i = 0
    while i < len(es) - 3:
        if es[i] == 0 and es[i + 1] == 0 and es[i + 2] == 1:
            starts.append((i + 3, i > 0 and es[i - 1] == 0))
            i += 3
        else:
            i += 1
    out = []
    for index, (body, _) in enumerate(starts):
        end = starts[index + 1][0] - 3 if index + 1 < len(starts) else len(es)
        if end > body:
            out.append((es[body] & 0x1F, end - body, body))
    return out


def report(path):
    print(f"\n=== {os.path.basename(path)}  ({os.path.getsize(path):,} bytes)")
    counts = collections.Counter()
    bad = 0
    for pid, _, _, raw in packets(path):
        if pid is None:
            bad += 1
        else:
            counts[pid] += 1
    print(f"  packets: {sum(counts.values()):,}  unusable/short: {bad}")
    streams, pes_ids = elementary_streams(path)
    for pid in sorted(streams):
        es = bytes(streams[pid])
        found = nals(es)
        types = collections.Counter(t for t, _, _ in found)
        print(f"  PID 0x{pid:04x}  pes=0x{pes_ids.get(pid, 0):02x}  payload={len(es):,}  "
              f"nal={len(found)}")
        if types:
            summary = ", ".join(f"{NAL_NAMES.get(t, t)}={n}" for t, n in sorted(types.items()))
            print(f"      NAL types: {summary}")
            first = [t for t, _, _ in found[:8]]
            print(f"      first NAL types: {first}")
            big = sorted(found, key=lambda x: -x[1])[:5]
            print(f"      largest NALs: {[(t, s) for t, s, _ in big]}")
        else:
            head = es[:32].hex()
            print(f"      no Annex-B start codes; head={head}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for path in sys.argv[1:]:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
