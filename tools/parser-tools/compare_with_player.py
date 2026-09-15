"""Put the player's own decoded frame next to ours, for the same segment and the same timestamp.

`dump_player_frames.py` writes the Y plane straight out of the player's FFmpeg decoder, and it also
records the PTS of the packet that produced each frame. Every frame can therefore be attributed to a
segment -- the file named by the key the player set most recently before that frame -- and the same
frame index can be pulled out of our own decode of that file. If our frame is flat grey where the
player's is a picture, the bytes are not the problem and nothing about key derivation needs revisiting.

    python compare_with_player.py
"""

import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, "captured", "decode_frames")
WORK = os.path.join(HERE, "captured", "compare")
CACHE = os.path.join(HERE, "captured", "key_files.json")
DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
FPS = 25


def frame_stats(plane, width, height, stride):
    rows = [plane[y * stride:y * stride + width] for y in range(height)]
    flat = b"".join(rows)
    return statistics.fmean(flat), statistics.pstdev(flat)


def main():
    if not os.path.isdir(FRAMES):
        print("no captured/decode_frames -- run dump_player_frames.py first")
        return 1
    events = [json.loads(line) for line in open(os.path.join(FRAMES, "events.jsonl"),
                                                encoding="utf-8") if line.strip()]
    keys = [e for e in events if "key" in e]
    frames = [e for e in events
              if "w" in e and not e.get("skipped") and e.get("index") is not None]
    if not frames:
        print("no frames captured")
        return 1

    from build_videos import decrypt, file_for_key, first_pts
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    names = [n for n in os.listdir(DOWNLOADS) if n.endswith(".ts")]
    os.makedirs(WORK, exist_ok=True)

    print(f"{len(frames)} player frame(s), {len(keys)} key(s)\n")
    for frame in frames:
        prior = [k for k in keys if k["at"] <= frame["at"]]
        if not prior:
            print(f"frame {frame['index']}: no key seen before it — skipped")
            continue
        key = prior[-1]["key"]
        name = cache.get(key)
        if name is None or not os.path.exists(os.path.join(DOWNLOADS, name)):
            name = file_for_key(key, names)
            cache[key] = name
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
        if not name:
            print(f"frame {frame['index']}: key {key} matches no file")
            continue

        raw = os.path.join(WORK, name + ".ts")
        if not os.path.exists(raw):
            plain = decrypt(open(os.path.join(DOWNLOADS, name), "rb").read(), key, name)
            with open(raw, "wb") as out:
                out.write(plain)
        start = first_pts(open(raw, "rb").read())
        if start is None:
            print(f"frame {frame['index']}: {name} has no video PTS")
            continue
        index = round((frame["pts"] - start) / (90000 / FPS))
        if index < 0:
            index = 0

        pixels = frame["w"] * frame["h"]
        player_file = os.path.join(FRAMES,
                                   f"p{frame['pid']}_{frame['index']:02d}_{frame['w']}x{frame['h']}"
                                   f"_s{frame['stride']}.gray")
        player = open(player_file, "rb").read()
        player_stats = frame_stats(player, frame["w"], 1080, frame["stride"])

        ours_file = os.path.join(WORK, f"ours_{frame['index']:02d}.gray")
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", raw,
                               "-vf", f"select=eq(n\\,{index})", "-vsync", "0",
                               "-pix_fmt", "gray", "-f", "rawvideo", "-y", ours_file],
                              capture_output=True, text=True)
        errors = len([line for line in (done.stderr or "").splitlines() if line.strip()])
        if not os.path.exists(ours_file) or os.path.getsize(ours_file) < 1920 * 1080:
            print(f"frame {frame['index']}: our decode produced no frame at index {index} "
                  f"({errors} error line(s))")
            continue
        ours = open(ours_file, "rb").read()
        ours_stats = frame_stats(ours, 1920, 1080, 1920)

        rows = min(1080, frame["h"])
        diff = [abs(ours[y * 1920 + x] - player[y * frame["stride"] + x])
                for y in range(0, rows, 8) for x in range(0, 1920, 8)]
        same = sum(1 for d in diff if d <= 2) / len(diff)

        print(f"frame {frame['index']:02d}  {name[:46]:<46} idx={index:4d}  "
              f"player mean={player_stats[0]:6.1f} sd={player_stats[1]:6.1f}   "
              f"ours mean={ours_stats[0]:6.1f} sd={ours_stats[1]:6.1f}   "
              f"identical={same * 100:5.1f}%  errors={errors}  ({pixels:,} px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
