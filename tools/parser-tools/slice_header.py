"""Read the first bits of a slice header, to tell a corrupt payload from a decoder disagreement.

`ffmpeg` refuses both a stream whose slice data is wrong and a stream it merely dislikes, and the
two look identical from the outside: "error while decoding MB 1 0" plus grey frames. The first few
fields of a slice header are parsed without a decoder, so they separate the cases:

  * `first_mb_in_slice` must be 0 for the first slice of a picture;
  * `slice_type` for an IDR picture is 2 or 7 (I), 4 or 9 (SI), never 0 (P) or 1 (B);
  * `pps_id`, `frame_num` and the reference counts have to be inside their legal ranges.

Garbage at that level means the payload bytes are not what the encoder wrote, whatever the transport
stream says about itself. Sensible values mean the bytes are fine and the disagreement is in the
decoder.

    python slice_header.py <file.ts> [more.ts ...]
"""

import sys


class Bits:
    def __init__(self, data):
        self.data = data
        self.at = 0

    def bit(self):
        byte = self.data[self.at >> 3]
        value = (byte >> (7 - (self.at & 7))) & 1
        self.at += 1
        return value

    def bits(self, count):
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self):
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("ue(v) ran away")
        return (1 << zeros) - 1 + (self.bits(zeros) if zeros else 0)

    def se(self):
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def nal_units(stream):
    """Annex B NAL units as (type, payload)."""
    starts = []
    i = 0
    while i < len(stream) - 3:
        if stream[i] == 0 and stream[i + 1] == 0 and stream[i + 2] == 1:
            starts.append((i + 3, 3 if i == 0 or stream[i - 1] != 0 else 4))
            i += 3
        else:
            i += 1
    for index, (start, _length) in enumerate(starts):
        end = starts[index + 1][0] - 3 if index + 1 < len(starts) else len(stream)
        while end > start and stream[end - 1] == 0:
            end -= 1
        yield stream[start] & 0x1F, stream[start + 1:end]


def report(path):
    stream = open(path, "rb").read()
    print(f"=== {path} ===")
    units = [(kind, payload) for kind, payload in nal_units(stream)]
    for kind, payload in units[:8]:
        names = {1: "non-IDR", 5: "IDR", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD"}
        print(f"  NAL {kind:2d} ({names.get(kind, '?'):8s}) {len(payload):7d} B")
        if kind not in (1, 5) or len(payload) < 8:
            continue
        try:
            bits = Bits(payload[1:])
            first_mb = bits.ue()
            slice_type = bits.ue()
            pps_id = bits.ue()
            print(f"      first_mb_in_slice={first_mb}  slice_type={slice_type}  pps_id={pps_id}")
            if first_mb != 0:
                print("      -> first_mb_in_slice is not 0: this is not the start of a picture")
            if kind == 5 and slice_type not in (2, 7):
                print(f"      -> an IDR with slice_type={slice_type} is not an I slice")
        except Exception as error:
            print(f"      -> slice header unreadable: {error}")
    return 0


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        report(argument)
