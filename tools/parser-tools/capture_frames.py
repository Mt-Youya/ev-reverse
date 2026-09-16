"""Record the picture the player decodes, for streams our own decoders cannot read.

The level-40 segments are decrypted correctly -- the transport stream is right, the audio is right,
the bytes are exactly what the player feeds its decoder -- but the video payload is protected in a way
that only that decoder undoes, so every decoder here conceals the frame. The picture does exist, on
screen, which means it can be recorded: the player hands each decoded frame to the renderer, and the
frame is plain YUV420 in memory.

Writing those planes through Frida's message channel would be the obvious way and the wrong one: 1080p
YUV is three megabytes a frame and twenty-five frames a second, which is more than the channel will
carry without dropping them. So the target writes the file itself, through the CRT it already has
loaded, and only a line of text per frame comes back. Audio is not recorded at all -- our own
decryption already produces that track perfectly, and muxing it later keeps it lossless.

    python capture_frames.py --seconds 60 --out D:\\ev-export\\capture
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var msvcrt = Process.getModuleByName('msvcrt.dll');
var fopen = new NativeFunction(msvcrt.getExportByName('fopen'), 'pointer', ['pointer', 'pointer']);
var fwrite = new NativeFunction(msvcrt.getExportByName('fwrite'), 'size_t',
                               ['pointer', 'size_t', 'size_t', 'pointer']);
var fclose = new NativeFunction(msvcrt.getExportByName('fclose'), 'int', ['pointer']);

var OUT = OUTDIR;
var current = null, currentPts = -1, written = 0, frames = 0, skipped = 0;
var scratch = null, scratchSize = 0;
var opened = 0, firstAt = 0, lastAt = 0, fileFrames = 0, index = [];

// Timing per file, not per run. A single rate for the whole capture assumes every clip was recorded
// at the same speed, and clips from different moments -- or different runs -- are not: one batch here
// came out at 1.8 frames a second and was encoded as if it were 10, which made its duration wrong and
// broke the join against the decrypted pieces. The first and last frame's wall-clock times are what a
// clip's own rate is computed from.
function closeCurrent() {
  if (current !== null && fileFrames > 0) {
    index.push({ name: opened, frames: fileFrames, first: firstAt, last: lastAt });
  }
  if (current !== null) { fclose(current); }
  current = null;
}

function open(pts) {
  var name = OUT + '\\\\stream_' + pts + '.yuv';
  var handle = fopen(Memory.allocUtf8String(name), Memory.allocUtf8String('wb'));
  if (handle.isNull()) { send({ t: 'error', message: 'fopen failed for ' + name }); return null; }
  currentPts = pts;
  opened = name;
  fileFrames = 0;
  firstAt = 0; lastAt = 0;
  send({ t: 'file', pts: pts, name: name });
  return handle;
}

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    this.frame = args[1];
    this.pts = 0;
    try {
      var pkt = args[3];
      if (!pkt.isNull()) this.pts = Math.round(pkt.add(0x08).readS64().toNumber() / 90000);
    } catch (e) {}
  },
  onLeave: function () {
    try {
      var f = this.frame;
      var w = f.add(0x68).readS32(), h = f.add(0x6c).readS32();
      var yStride = f.add(0x40).readS32(), uStride = f.add(0x44).readS32();
      var vStride = f.add(0x48).readS32();
      var y = f.readPointer(), u = f.add(0x08).readPointer(), v = f.add(0x10).readPointer();
      if (w < 16 || h < 16 || yStride < w || y.isNull() || u.isNull() || v.isNull()) { skipped += 1; return; }
      // The decoder's frames are 1920x1088 -- macroblock padding -- while the picture is 1080 lines.
      // Writing all 1088 and encoding at 1080 shifts the chroma planes against the luma, which tints
      // the whole frame; the padding is dropped here instead.
      var rowsY = Math.min(h, 1080), rowsUV = Math.min(h / 2, rowsY / 2);
      // A new file per second keeps each one openable on its own and makes a capture that ran out of
      // disk space still mostly usable.
      if (current === null || this.pts !== currentPts) {
        closeCurrent();
        current = open(this.pts);
        if (current === null) return;
      }
      var now = Date.now() / 1000;
      if (fileFrames === 0) firstAt = now;
      lastAt = now;
      fileFrames += 1;
      // Pack the three planes into one scratch buffer and write it with a single call. Writing row by
      // row straight to the file is correct but slow enough that the decoder reuses the frame buffer
      // while it runs, which showed up as horizontal tearing; copying in memory costs microseconds and
      // one fwrite instead of two thousand.
      var packed = w * rowsY + (w / 2) * rowsUV * 2;
      if (scratch === null || scratchSize < packed) {
        scratch = Memory.alloc(packed);
        scratchSize = packed;
      }
      var cursor = scratch, row;
      for (row = 0; row < rowsY; row++) {
        Memory.copy(cursor, y.add(row * yStride), w);
        cursor = cursor.add(w);
      }
      for (row = 0; row < rowsUV; row++) {
        Memory.copy(cursor, u.add(row * uStride), w / 2);
        cursor = cursor.add(w / 2);
      }
      for (row = 0; row < rowsUV; row++) {
        Memory.copy(cursor, v.add(row * vStride), w / 2);
        cursor = cursor.add(w / 2);
      }
      fwrite(scratch, 1, packed, current);
      written += packed;
      frames += 1;
    } catch (e) { skipped += 1; }
  }
});

setInterval(function () {
  send({ t: 'stats', frames: frames, skipped: skipped, bytes: written });
}, 5000);
setInterval(function () {
  if (index.length) { send({ t: 'index', entries: index }); index = []; }
}, 3000);
send({ t: 'armed', base: dll.base.toString(), out: OUT });
"""


def renderer_pid():
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    best = None
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            weight = int(cells[4].replace(",", "").replace(" K", ""))
            if best is None or weight > best[0]:
                best = (weight, int(cells[1]))
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--out", default=r"D:\ev-export\capture")
    ap.add_argument("--fps", type=float, default=25)
    args = ap.parse_args()

    pid = args.pid or renderer_pid()
    if not pid:
        print("EVPlayer2 is not running")
        return 1
    os.makedirs(args.out, exist_ok=True)
    print(f"recording pid {pid} for {args.seconds:.0f}s into {args.out}")

    files = []
    stats = {}
    index = []
    script = frida.attach(pid).create_script(
        JS.replace("OUTDIR", json.dumps(args.out.replace("\\", "\\\\"))))

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "armed":
            print(f"armed at {payload['base']}")
        elif payload.get("t") == "stats":
            stats.update(payload)
            print(f"  {payload['frames']} frame(s), {payload['bytes'] / 1048576:.1f} MB, "
                  f"{payload['skipped']} skipped", flush=True)
        elif payload.get("t") == "index":
            index.extend(payload["entries"])
        elif payload.get("t") == "file":
            files.append(payload)
        elif payload.get("t") == "error":
            print(f"  {payload['message']}")

    script.on("message", on_message)
    script.load()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            script.unload()
        except Exception:
            pass

    print(f"\n{stats.get('frames', 0)} frame(s) written, {len(files)} file(s) started")
    if index:
        path = os.path.join(args.out, "capture_index.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(index, handle, indent=1)
        print(f"per-file timing written to {path} ({len(index)} file(s))")
    if not files:
        print("nothing was recorded: no frame came back while the hooks were on")
        return 0
    total = 0
    print("\nper file (duration = frames / fps):")
    for entry in files:
        path = entry["name"]
        if not os.path.exists(path):
            continue
        size = os.path.getsize(path)
        # 1920x1080 YUV420 is 3110400 bytes a frame; the file size is the frame count times that, so
        # the recorded duration is exact rather than assumed.
        frames = size // 3110400
        total += frames
        print(f"  {os.path.basename(path):<28} {size / 1048576:8.1f} MB  {frames:5d} frames  "
              f"{frames / args.fps:7.2f}s")
    print(f"\n{total} frame(s) in total = {total / args.fps:.2f}s of video at {args.fps:g} fps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
