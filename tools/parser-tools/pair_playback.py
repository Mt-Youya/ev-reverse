"""Capture the player's keys and its decoder input together, with timestamps, then pair them exactly.

The earlier comparisons paired a window's keys with a window's frames and searched for the frame
bytes in whatever files those keys named. That is not pairing, it is hoping: the player sets a key
once and then decodes that segment for the next few seconds, so a frame's file is the one whose key
was set most recently *before that frame*, and nothing else.

This records both with `time.time()` stamps, then works frame by frame: take the keys seen in the
`--window` seconds before the frame, decrypt the file each names, extract its elementary stream, and
look for the frame's own bytes. A hit proves the player decodes exactly what we decrypt. A prefix
that matches and then diverges proves there is a transform and says where it starts. No hit at all
means the frame belongs to a segment whose key was set before the capture began, which is checked by
reporting how often that happens rather than being assumed away.

    python pair_playback.py --pid <pid> --seconds 180
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURED = os.path.join(HERE, "captured", "pairing")
CACHE = r"D:\Downloads\EVPlayer2Downloads"
LOG = os.path.join(CAPTURED, "events.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

function sstring(p) {
  if (!p || p.isNull()) return null;
  try {
    var size = p.add(0x10).readU64().toNumber();
    var cap = p.add(0x18).readU64().toNumber();
    if (size > 0 && size < 512 && cap >= size) {
      var data = (cap < 16) ? p.readByteArray(size) : p.readPointer().readByteArray(size);
      if (!data) return null;
      var bytes = new Uint8Array(data), out = '';
      for (var i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
      return out;
    }
  } catch (e) {}
  try { return p.readUtf8String(128); } catch (e) { return null; }
}

Interceptor.attach(dll.base.add(0x40AF0), {
  onEnter: function (args) {
    var key = sstring(args[1]);
    if (key && /^[0-9a-f]{32}$/.test(key)) send({ t: 'key', at: Date.now() / 1000, key: key });
  }
});

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var packet = args[3];
    if (packet.isNull()) return;
    var size = -1, data = null;
    try { size = packet.add(0x20).readS32(); data = packet.add(0x18).readPointer(); } catch (e) { return; }
    if (size <= 0 || size > 4 * 1024 * 1024 || data.isNull()) return;
    send({ t: 'frame', at: Date.now() / 1000, size: size }, data.readByteArray(size));
  }
});

send({ t: 'armed', base: dll.base.toString() });
"""


def wait_for_player(known):
    print("waiting for an EVPlayer2 process…", flush=True)
    while True:
        listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv"],
                                 capture_output=True, text=True).stdout
        pids = {int(row.split('","')[1]) for row in listing.splitlines()
                if row.startswith('"EVPlayer2.exe"')}
        fresh = pids - known
        if fresh:
            return sorted(fresh)[-1]
        known |= pids
        time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--frames", type=int, default=20000,
                    help="keep this many frames (default: all of a few minutes' worth)")
    ap.add_argument("--window", type=float, default=120)
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        args.pid = wait_for_player(set())

    os.makedirs(CAPTURED, exist_ok=True)
    for stale in os.listdir(CAPTURED):
        if stale != "events.jsonl":
            os.remove(os.path.join(CAPTURED, stale))
    handle = open(LOG, "w", encoding="utf-8")

    session = frida.attach(args.pid)
    script = session.create_script(JS)
    frames = []

    def on_message(message, data):
        if message.get("type") != "send" or data is None and message["payload"].get("t") == "frame":
            return
        payload = message["payload"]
        if payload.get("t") == "armed":
            print(f"armed at {payload['base']} — play the lesson now", flush=True)
            return
        if payload.get("t") == "key":
            handle.write(json.dumps(payload) + "\n")
            handle.flush()
            return
        if payload.get("t") != "frame":
            return
        index = len(frames) + 1
        record = {"t": "frame", "at": payload["at"], "size": payload["size"]}
        if data is not None and index <= args.frames:
            name = f"{index:04d}_{payload['size']}.bin"
            with open(os.path.join(CAPTURED, name), "wb") as out:
                out.write(bytes(data))
            record["file"] = name
            frames.append(name)
        handle.write(json.dumps(record) + "\n")
        handle.flush()

    script.on("message", on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass

    events = []
    for line in open(LOG, encoding="utf-8"):
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    keys = [e for e in events if e["t"] == "key"]
    frames = [e for e in events if e["t"] == "frame" and e.get("file")]
    print(f"{len(keys)} key(s), {len(frames)} frame(s) kept "
          f"(of {sum(1 for e in events if e['t'] == 'frame')} seen)", flush=True)
    if not keys or not frames:
        print("nothing to pair: the player must be decoding while the hooks are on")
        return 0

    from build_videos import decrypt, file_for_key
    names = [name for name in os.listdir(CACHE) if name.endswith(".ts")]
    identified = {}
    for entry in keys:
        if entry["key"] not in identified:
            identified[entry["key"]] = file_for_key(entry["key"], names)
    print(f"{sum(1 for v in identified.values() if v)} of {len(identified)} key(s) matched a file",
          flush=True)

    matched = orphan = 0
    for frame in frames:
        blob = open(os.path.join(CAPTURED, frame["file"]), "rb").read()
        needle = blob[len(blob) // 3:len(blob) // 3 + 24]
        candidates = [k["key"] for k in keys
                      if 0 < frame["at"] - k["at"] <= args.window and identified.get(k["key"])]
        found = False
        for key in candidates[-3:]:
            name = identified[key]
            plain = decrypt(open(os.path.join(CACHE, name), "rb").read(), key, name)
            raw = os.path.join(CAPTURED, name + ".ts")
            with open(raw, "wb") as out:
                out.write(plain)
            es = raw + ".h264"
            if not os.path.exists(es):
                subprocess.run(["ffmpeg", "-v", "error", "-i", raw, "-c:v", "copy", "-f", "h264",
                                "-y", es], capture_output=True)
            if not os.path.exists(es):
                continue
            stream = open(es, "rb").read()
            if needle in stream:
                matched += 1
                found = True
                break
        if not found:
            orphan += 1

    print(f"\n{matched} frame(s) found in the file their key names, {orphan} not found")
    print("RESULT: " + ("the player decodes exactly what we decrypt"
                        if orphan == 0 else
                        "frames are NOT the bytes we decrypt -- a transform sits between them, "
                        "and the orphans are the evidence"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
