"""Catch the player decrypting and decoding at the same moment, then check the two against each other.

Everything else in this directory works around a gap: the transport stream can be reproduced here
(sync 100%, NAL structure intact, PES timestamps readable) and its video still decodes to grey,
while the audio in the same ciphertext is clean. Is the player feeding its decoder the same bytes,
or bytes that have been through one more transform?

Two earlier attempts failed for reasons worth keeping:

  * comparing against a fixed set of lessons assumed the player was playing one of them; it can
    switch course at any moment, and it downloads what it switches to;
  * comparing boxes a *byte* (`plain[0] == 0x47`) as the oracle for "this key belongs to this file"
    produced 335 "hits" for 8 keys over 10,770 files, which is exactly the 336 false positives a
    1-in-256 test is expected to produce. Three sync bytes (offsets 0, 188, 376) turn the same test
    into a 1-in-16-million one and returned a clean one-file-per-key mapping.

So this tool does both halves in one window: it logs the key `hls_decode` is given (`0x40AF0`) and
keeps the frames handed to the decoder (`0xB9648`), then identifies each key's file with the strong
oracle, decrypts those files with the player's own key, and looks for the captured frames inside
them. A hit means the player's input and ours are the same bytes; no hit means the difference
between them is the transform being hunted.

    python verify_playback_bytes.py --pid <pid> --seconds 180
    python verify_playback_bytes.py --follow --seconds 600
"""

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURED = os.path.join(HERE, "captured", "playtruth")
CACHE = r"D:\Downloads\EVPlayer2Downloads"

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

// MSVC std::string: the 16-byte union comes first, so size is at +0x10 and capacity at +0x18, and
// a capacity below 16 means the bytes are inline. Reading a C string instead returns the bytes of
// the buffer pointer -- which is how this hook first reported a key of `None` on every call.
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
  onEnter: function (args) { send({ t: 'key', key: sstring(args[1]) }); }
});

// h264_decode_frame: AVPacket has data at +0x18 and size at +0x20.
Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var packet = args[3];
    if (packet.isNull()) return;
    var size = -1, data = null;
    try { size = packet.add(0x20).readS32(); data = packet.add(0x18).readPointer(); } catch (e) { return; }
    if (size <= 0 || size > 4 * 1024 * 1024 || data.isNull()) return;
    send({ t: 'frame', size: size });
  }
});

send({ t: 'armed', base: dll.base.toString() });
"""

JS_KEEP = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    var packet = args[3];
    if (packet.isNull()) return;
    var size = -1, data = null;
    try { size = packet.add(0x20).readS32(); data = packet.add(0x18).readPointer(); } catch (e) { return; }
    if (size <= 0 || size > 4 * 1024 * 1024 || data.isNull()) return;
    send({ t: 'frame', size: size }, data.readByteArray(size));
  }
});
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


def decrypt(blob, key, name):
    from Crypto.Cipher import AES
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    masked = bytes(b ^ mask[i % 16] for i, b in enumerate(blob))
    plain = AES.new(key.encode(), AES.MODE_ECB).decrypt(masked)
    padding = len(plain) % 188
    if 0 < padding <= 15 and plain[-padding:] == b"#" * padding:
        plain = plain[:-padding]
    return plain


def file_for_key(key, files):
    """The strong oracle: three sync bytes, not one."""
    from Crypto.Cipher import AES
    for name in files:
        try:
            with open(os.path.join(CACHE, name), "rb") as handle:
                head = handle.read(576)
        except Exception:
            continue
        if len(head) < 576:
            continue
        mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
        masked = bytes(b ^ mask[i % 16] for i, b in enumerate(head))
        plain = AES.new(key.encode(), AES.MODE_ECB).decrypt(masked)
        if plain[0] == 0x47 and plain[188] == 0x47 and plain[376] == 0x47:
            return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--frames", type=int, default=60)
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        args.pid = wait_for_player(set())

    os.makedirs(CAPTURED, exist_ok=True)
    for stale in os.listdir(CAPTURED):
        os.remove(os.path.join(CAPTURED, stale))

    session = frida.attach(args.pid)
    keys = []
    frames = []

    key_script = session.create_script(JS)

    def on_key(message, data):
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "armed":
            print(f"armed at {payload['base']}; play a lesson (switching course is fine)", flush=True)
            return
        if payload.get("t") == "key" and payload.get("key"):
            keys.append(payload["key"])
            if len(keys) <= 10:
                print(f"KEY   {payload['key']}", flush=True)

    key_script.on("message", on_key)
    key_script.load()

    frame_script = session.create_script(JS_KEEP)

    def on_frame(message, data):
        if message.get("type") != "send" or data is None:
            return
        payload = message["payload"]
        if payload.get("t") != "frame":
            return
        index = len(frames) + 1
        if index <= args.frames:
            name = f"{index:03d}_{payload['size']}.bin"
            with open(os.path.join(CAPTURED, name), "wb") as handle:
                handle.write(bytes(data))
            frames.append(name)

    frame_script.on("message", on_frame)
    frame_script.load()

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

    print(f"\n{len(keys)} key(s), {len(frames)} frame(s) kept", flush=True)
    if not keys or not frames:
        print("nothing to compare: the player has to be decoding while both hooks are on")
        return 0

    files = [name for name in os.listdir(CACHE) if name.endswith(".ts")]
    identified = {}
    for key in dict.fromkeys(keys):
        name = file_for_key(key, files)
        identified[key] = name
        print(f"  key {key} -> {name or '(no file on disk matches)'}", flush=True)

    needles = []
    for name in frames:
        blob = open(os.path.join(CAPTURED, name), "rb").read()
        if len(blob) >= 96:
            needles.append((name, blob[len(blob) // 3:len(blob) // 3 + 24]))
    print(f"{len(needles)} needle(s) from {len(frames)} frame(s)", flush=True)

    hits = 0
    for key, name in identified.items():
        if not name:
            continue
        plain = decrypt(open(os.path.join(CACHE, name), "rb").read(), key, name)
        found = [label for label, needle in needles if needle in plain]
        sync = sum(1 for i in range(0, len(plain) // 188 * 188, 188) if plain[i] == 0x47)
        print(f"  {name}: {len(plain)} B, sync {sync}/{len(plain) // 188}, "
              f"frame bytes found: {len(found)} {found[:4]}", flush=True)
        hits += len(found)

    print("\n" + ("RESULT: the player's decoder input IS in the files as decrypted here"
                  if hits else
                  "RESULT: the player's decoder input is NOT what the file decrypts to -- "
                  "one more transform sits between them"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
