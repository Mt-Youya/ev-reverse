"""Compare the parameter sets the decoder receives against the ones inside the files we decrypt.

The packets reaching `h264_decode_frame` carry the file's bytes, so the SPS and PPS in the stream ought
to be the ones we can read out of our own decryption. If they are not -- if the decoder is handed a
different SPS, even a subtly different one such as another level -- then the stream being decoded is not
the stream we have been decoding, and every "identical input, different output" measurement has a much
simpler explanation.

    python compare_sps.py captured/nals D:\\ev-export\\...ts [more files]
"""

import os
import sys

from slice_probe import nals_of


def parameter_sets(blob):
    out = {}
    for nal_type, body in nals_of(blob):
        if nal_type in (7, 8) and nal_type not in out:
            out[nal_type] = body
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    nal_dir, files = sys.argv[1], sys.argv[2:]

    decoder_side = {}
    for name in sorted(os.listdir(nal_dir)):
        if name.startswith("nal7_") or name.startswith("nal8_"):
            kind = 7 if name.startswith("nal7_") else 8
            decoder_side.setdefault(kind, open(os.path.join(nal_dir, name), "rb").read())

    for kind, blob in sorted(decoder_side.items()):
        print(f"decoder received NAL {kind} ({len(blob)} B): {blob.hex(' ')}")

    matches = 0
    for path in files:
        sets = parameter_sets(open(path, "rb").read())
        line = []
        for kind in (7, 8):
            mine = sets.get(kind)
            theirs = decoder_side.get(kind)
            if mine is None:
                line.append(f"NAL {kind}: absent in file")
            elif theirs is None:
                line.append(f"NAL {kind}: nothing captured")
            elif mine == theirs:
                line.append(f"NAL {kind}: IDENTICAL")
                matches += 1
            else:
                first = next((i for i in range(min(len(mine), len(theirs)))
                              if mine[i] != theirs[i]), min(len(mine), len(theirs)))
                line.append(f"NAL {kind}: DIFFERENT at byte {first} "
                            f"(file {len(mine)} B {mine[:8].hex(' ')}, "
                            f"decoder {len(theirs)} B {theirs[:8].hex(' ')})")
        print(f"\n{os.path.basename(path)[:60]}\n  " + "\n  ".join(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
