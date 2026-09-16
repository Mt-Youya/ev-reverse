"""Look for the second layer in the slice payload itself, from the bitstream's own tells.

A stock decoder of either generation turns these files grey, so something about the slice payload is
not H.264 while the container, the parameter sets and the audio are all intact. Two cheap properties
separate "encrypted payload" from "unusual but valid H.264":

  * emulation prevention. A real encoder inserts 0x03 whenever two zero bytes would otherwise be
    followed by 0x00..0x03. Random (encrypted) bytes essentially never contain that pattern, so a
    large slice with zero occurrences is a strong hint, and one with several is a strong hint the
    other way.
  * block alignment. A payload encrypted with a 16-byte block cipher has a length that is a multiple
    of 16 once the clear header is subtracted. Real slice data has no such habit.

To subtract the clear header this parses SPS, PPS and the slice header properly (exp-Golomb), so the
answer is the header's real length rather than a guess.

    python slice_probe.py <file.ts|file.h264> [...]
"""

import collections
import os
import sys

NAL_NAMES = {1: "non-IDR", 5: "IDR", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD"}
SCALING_LIST_SIZES = [16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16]


class Bits:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def bit(self):
        byte = self.data[self.pos >> 3]
        value = (byte >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return value

    def bits(self, count):
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self):
        zeros = 0
        while self.pos < len(self.data) * 8 and self.bit() == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("exp-Golomb runaway")
        return (1 << zeros) - 1 + (self.bits(zeros) if zeros else 0)

    def se(self):
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)

    def bytes_used(self):
        return (self.pos + 7) // 8


def unescape(data):
    """Remove emulation prevention bytes, counting how many were there."""
    out = bytearray()
    removed = 0
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte == 3:
            zeros = 0
            removed += 1
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out), removed


def parse_sps(rbsp):
    bits = Bits(rbsp)
    sps = {}
    sps["profile_idc"] = bits.bits(8)
    bits.bits(8)
    sps["level_idc"] = bits.bits(8)
    sps["id"] = bits.ue()
    if sps["profile_idc"] in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        sps["chroma_format_idc"] = bits.ue()
        if sps["chroma_format_idc"] == 3:
            bits.bit()
        bits.ue()
        bits.ue()
        bits.bit()
        if bits.bit():
            count = 8 if sps["chroma_format_idc"] != 3 else 12
            for index in range(count):
                if bits.bit():
                    size = 16 if index < 6 else 64
                    last = 8
                    for _ in range(size):
                        if last:
                            last = (last + bits.se() + 256) % 256
    sps["log2_max_frame_num"] = bits.ue() + 4
    sps["pic_order_cnt_type"] = bits.ue()
    if sps["pic_order_cnt_type"] == 0:
        sps["log2_max_pic_order_cnt_lsb"] = bits.ue() + 4
    elif sps["pic_order_cnt_type"] == 1:
        bits.bit()
        bits.se()
        bits.se()
        for _ in range(bits.ue()):
            bits.se()
    sps["max_num_ref_frames"] = bits.ue()
    bits.bit()
    sps["width_mbs"] = bits.ue() + 1
    sps["height_map_units"] = bits.ue() + 1
    sps["frame_mbs_only"] = bits.bit()
    if not sps["frame_mbs_only"]:
        bits.bit()
    bits.bit()
    if bits.bit():
        bits.ue()
        bits.ue()
        bits.ue()
        bits.ue()
    sps["header_bytes"] = bits.bytes_used()
    return sps


def parse_pps(rbsp):
    bits = Bits(rbsp)
    pps = {}
    pps["id"] = bits.ue()
    pps["sps_id"] = bits.ue()
    pps["entropy_coding_mode"] = bits.bit()
    pps["bottom_field_pic_order_in_frame_present"] = bits.bit()
    pps["num_slice_groups_minus1"] = bits.ue()
    if pps["num_slice_groups_minus1"] > 0:
        raise ValueError("slice groups not handled")
    pps["num_ref_idx_l0_default_active_minus1"] = bits.ue()
    pps["num_ref_idx_l1_default_active_minus1"] = bits.ue()
    pps["weighted_pred_flag"] = bits.bit()
    pps["weighted_bipred_idc"] = bits.bits(2)
    pps["pic_init_qp_minus26"] = bits.se()
    pps["pic_init_qs_minus26"] = bits.se()
    pps["chroma_qp_index_offset"] = bits.se()
    pps["deblocking_filter_control_present"] = bits.bit()
    pps["constrained_intra_pred"] = bits.bit()
    pps["redundant_pic_cnt_present"] = bits.bit()
    pps["header_bytes"] = bits.bytes_used()
    return pps


def parse_slice_header(rbsp, sps, pps, nal_type):
    bits = Bits(rbsp)
    header = {}
    header["first_mb_in_slice"] = bits.ue()
    header["slice_type"] = bits.ue()
    header["pps_id"] = bits.ue()
    header["frame_num"] = bits.bits(sps["log2_max_frame_num"])
    if not sps["frame_mbs_only"]:
        header["field_pic_flag"] = bits.bit()
        if header["field_pic_flag"]:
            header["bottom_field_flag"] = bits.bit()
    if nal_type == 5:
        header["idr_pic_id"] = bits.ue()
    if sps["pic_order_cnt_type"] == 0:
        header["pic_order_cnt_lsb"] = bits.bits(sps["log2_max_pic_order_cnt_lsb"])
        if pps["bottom_field_pic_order_in_frame_present"] and not header.get("field_pic_flag"):
            header["delta_pic_order_cnt_bottom"] = bits.se()
    if pps.get("redundant_pic_cnt_present"):
        header["redundant_pic_cnt"] = bits.ue()
    header["header_bytes"] = bits.bytes_used()
    return header


def nals_of(blob):
    if blob[:1] == b"G" and len(blob) % 188 == 0:
        blob = elementary_stream(blob)
    starts = []
    i = 0
    while i < len(blob) - 3:
        if blob[i] == 0 and blob[i + 1] == 0 and blob[i + 2] == 1:
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    out = []
    for index, body in enumerate(starts):
        end = starts[index + 1] - 3 if index + 1 < len(starts) else len(blob)
        if end > body:
            out.append((blob[body] & 0x1F, blob[body:end]))
    return out


def elementary_stream(blob):
    out = bytearray()
    started = set()
    for offset in range(0, len(blob) - 187, 188):
        packet = blob[offset:offset + 188]
        if packet[0] != 0x47:
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        if not packet[1] & 0x40:
            if pid in started:
                control = (packet[3] >> 4) & 0x03
                start = 4 + (1 + packet[4] if control == 3 else 0)
                if control & 0x01:
                    out += packet[start:]
            continue
        control = (packet[3] >> 4) & 0x03
        start = 4 + (1 + packet[4] if control == 3 else 0)
        payload = packet[start:]
        if payload[:3] != b"\x00\x00\x01" or not 0xE0 <= payload[3] <= 0xEF:
            continue
        started.add(pid)
        out += payload[9 + payload[8]:]
    return bytes(out)


def report(path):
    blob = open(path, "rb").read()
    nals = nals_of(blob)
    sps = pps = None
    for nal_type, body in nals:
        rbsp, _ = unescape(body[1:])
        try:
            if nal_type == 7:
                sps = parse_sps(rbsp)
            elif nal_type == 8:
                pps = parse_pps(rbsp)
        except Exception as exc:
            print(f"  parameter set parse failed: {exc}")

    print(f"\n=== {os.path.basename(path)}  ({len(blob):,} bytes, {len(nals)} NALs)")
    if sps:
        print(f"  SPS: profile={sps['profile_idc']} level={sps['level_idc']} "
              f"{sps['width_mbs'] * 16}x{sps['height_map_units'] * 16} "
              f"frame_num_bits={sps['log2_max_frame_num']} poc_type={sps['pic_order_cnt_type']} "
              f"refs={sps['max_num_ref_frames']} header={sps['header_bytes']}B")
    if pps:
        print(f"  PPS: id={pps['id']} cabac={pps['entropy_coding_mode']} "
              f"deblock={pps['deblocking_filter_control_present']} header={pps['header_bytes']}B")

    epb = collections.Counter()
    header_bytes = collections.Counter()
    mod16 = collections.Counter()
    first_mb = collections.Counter()
    slice_types = collections.Counter()
    problems = 0
    for nal_type, body in nals:
        if nal_type not in (1, 5):
            continue
        rbsp, removed = unescape(body[1:])
        epb[(NAL_NAMES[nal_type], removed > 0)] += 1
        try:
            header = parse_slice_header(rbsp, sps, pps, nal_type)
        except Exception:
            problems += 1
            continue
        header_bytes[header["header_bytes"]] += 1
        first_mb[header["first_mb_in_slice"]] += 1
        slice_types[header["slice_type"]] += 1
        payload = len(body) - 1 - header["header_bytes"]
        mod16[(NAL_NAMES[nal_type], payload % 16)] += 1

    print(f"  slice headers parsed, {problems} failed")
    print(f"  header length (bytes): {dict(header_bytes)}")
    print(f"  first_mb_in_slice: {dict(first_mb)}")
    print(f"  slice_type: {dict(slice_types)}")
    print(f"  emulation-prevention present (type, any): {dict(epb)}")
    for name in ("IDR", "non-IDR"):
        rows = {k[1]: v for k, v in mod16.items() if k[0] == name}
        if rows:
            print(f"  payload length mod 16, {name}: {dict(sorted(rows.items()))}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for path in sys.argv[1:]:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
