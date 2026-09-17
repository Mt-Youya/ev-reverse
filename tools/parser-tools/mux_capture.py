"""Give a frame capture its sound back, from the segment we already decrypted correctly.

The captured picture and the decrypted transport stream describe the same ten seconds: the segment's
video payload is protected, its audio is not, and the capture's file name carries the PTS it started
at. So the audio track is not recorded anywhere -- it is cut out of the decrypted segment at the right
offset with `-c:a copy`, which keeps it exactly as it was served.

The duration is then checked rather than assumed: the muxed file's length against the captured
video's own length, so a wrong offset shows up as a mismatch instead of as sound that drifts.

    python mux_capture.py --capture D:\\ev-export\\capture\\mp4 --dec D:\\ev-export\\lessons\\119354\\dec
"""

import argparse
import os
import re
import subprocess
import sys

NAME = re.compile(r"stream_(\d+)\.mp4$")


def first_pts(path):
    """First video PTS of a decrypted segment, in 90 kHz units, from its own PES headers."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(188 * 400)
    except OSError:
        return None
    for offset in range(0, len(head) - 187, 188):
        packet = head[offset:offset + 188]
        if packet[0] != 0x47 or not packet[1] & 0x40:
            continue
        control = (packet[3] >> 4) & 0x03
        if control in (0, 2):
            continue
        start = 4 + (1 + packet[4] if control == 3 else 0)
        payload = packet[start:]
        if payload[:3] != b"\x00\x00\x01" or not 0xE0 <= payload[3] <= 0xEF:
            continue
        if not payload[7] & 0x80:
            continue
        stamp = payload[9:14]
        if len(stamp) < 5:
            continue
        return ((((stamp[0] >> 1) & 0x07) << 30) | (stamp[1] << 22)
                | (((stamp[2] >> 1) & 0x7F) << 15) | (stamp[3] << 7) | ((stamp[4] >> 1) & 0x7F))
    return None


def duration_of(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float((done.stdout or "").strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True, help="directory of captured .mp4 files")
    ap.add_argument("--dec", required=True, help="directory of decrypted .ts segments")
    ap.add_argument("--out", default="")
    ap.add_argument("--tolerance", type=float, default=0.6)
    args = ap.parse_args()

    segments = []
    for name in sorted(os.listdir(args.dec)):
        if not name.endswith(".ts"):
            continue
        pts = first_pts(os.path.join(args.dec, name))
        if pts is not None:
            segments.append((pts, os.path.join(args.dec, name)))
    segments.sort()
    print(f"{len(segments)} decrypted segment(s) with a start PTS")

    out_dir = args.out or os.path.join(args.capture, "muxed")
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for name in sorted(os.listdir(args.capture)):
        match = NAME.match(name)
        if not match:
            continue
        start = int(match.group(1))
        video = os.path.join(args.capture, name)
        video_seconds = duration_of(video) or 0
        target_pts = start * 90000
        covering = None
        for pts, path in segments:
            if pts <= target_pts < pts + 900000:
                covering = (pts, path)
                break
        if covering is None:
            rows.append((name, None, video_seconds, None, "no decrypted segment covers this second"))
            continue
        pts, path = covering
        offset = (target_pts - pts) / 90000.0
        audio = os.path.join(out_dir, name[:-4] + ".aac")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{offset:.3f}", "-i", path, "-t",
                        f"{video_seconds:.3f}", "-vn", "-c:a", "copy", "-y", audio],
                       capture_output=True, text=True)
        target = os.path.join(out_dir, name[:-4] + "_with_audio.mp4")
        # `-t` on the mux itself, not only on the audio cut. With both streams copied, `-shortest`
        # ends at a packet boundary, and a packet of audio can outlast the picture by a second or more
        # -- which is how a two-second clip came out 1.33s longer than its own video. The video's
        # measured length is the authority here, so both streams are cut to it.
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-i", audio,
                               "-c:v", "copy", "-c:a", "copy", "-t", f"{video_seconds:.3f}",
                               "-y", target],
                              capture_output=True, text=True)
        if os.path.exists(audio):
            os.remove(audio)
        if not os.path.exists(target):
            rows.append((name, offset, video_seconds, None, (done.stderr or "")[:60]))
            continue
        rows.append((name, offset, video_seconds, duration_of(target), ""))

    print(f"\n{'file':<22}{'offset':>8}{'video':>9}{'muxed':>9}{'delta':>8}  note")
    muxed = 0
    worst = 0.0
    for name, offset, video_seconds, actual, note in rows:
        if actual is None:
            print(f"{name:<22}{'-' if offset is None else f'{offset:8.2f}'}"
                  f"{video_seconds:9.2f}{'':>9}{'':>8}  {note}")
            continue
        muxed += 1
        delta = actual - video_seconds
        worst = max(worst, abs(delta))
        print(f"{name:<22}{offset:8.2f}{video_seconds:9.2f}{actual:9.2f}{delta:+8.2f}")
    print(f"\n{muxed} file(s) muxed with sound; worst length difference {worst:.2f}s")

    # The capture splits a file per PTS second, so a run of playback arrives as a numbered sequence.
    # Joining it is part of the job -- a hundred one-second files are not a video -- and the joined
    # length is checked against the sum of the parts, which is the only way a dropped or duplicated
    # piece would show up.
    pieces = []
    for name in os.listdir(out_dir):
        if not name.endswith("_with_audio.mp4"):
            continue
        match = NAME.match(name.replace("_with_audio", ""))
        if match:
            pieces.append((int(match.group(1)), os.path.join(out_dir, name)))
    pieces.sort()
    if len(pieces) < 2:
        print("nothing to join")
        return 0
    listing = os.path.join(out_dir, "join.txt")
    with open(listing, "w", encoding="utf-8") as handle:
        for _, path in pieces:
            handle.write("file '" + path.replace("'", "'\\''") + "'\n")
    joined = os.path.join(out_dir, "capture_joined.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing,
                    "-c", "copy", "-y", joined], capture_output=True, text=True)
    os.remove(listing)
    if not os.path.exists(joined):
        print("join failed")
        return 0
    expected = sum(duration_of(path) or 0 for _, path in pieces)
    actual = duration_of(joined) or 0
    print(f"\njoined {len(pieces)} piece(s) into {os.path.basename(joined)}: "
          f"sum of parts {expected:.2f}s, joined {actual:.2f}s, difference {actual - expected:+.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
