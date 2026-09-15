"""Read the real layout of the decoder's AVFrame / AVCodecContext out of the live player.

`h264_decode_frame` is statically linked FFmpeg, so its struct offsets have to come from the binary
rather than from a header. Guessing them is how an earlier frame dump produced nothing at all: the
width read back as nonsense, the code bailed silently, and the silence looked like "the player is not
decoding". This prints the raw header words of both structs instead, so the offsets can be read off
the bytes (1920 and 1080 appear as 0x780 and 0x438) instead of assumed.

    python probe_avframe.py --pid 13448 --seconds 20
"""

import argparse
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var calls = 0, shown = 0, lastRet = null;

Interceptor.attach(dll.base.add(0xB9648), {
  onEnter: function (args) {
    calls += 1;
    this.record = shown < 3;
    if (!this.record) return;
    shown += 1;
    send({ t: 'structs', call: calls,
           avctx: hexdump(args[0], { length: 0x100, header: false, ansi: false }),
           avframe: hexdump(args[1], { length: 0x100, header: false, ansi: false }),
           pkt: args[3].isNull() ? null : hexdump(args[3], { length: 0x40, header: false, ansi: false }) });
  },
  onLeave: function (retval) { lastRet = retval.toInt32(); }
});

setInterval(function () { send({ t: 'count', calls: calls, lastRet: lastRet }); }, 2000);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--seconds", type=float, default=20)
    args = ap.parse_args()

    session = frida.attach(args.pid)
    script = session.create_script(JS)

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "structs":
            print(f"\n--- call {payload['call']}")
            for key in ("avctx", "avframe", "pkt"):
                if payload.get(key):
                    print(f"  {key}:")
                    for line in payload[key].splitlines():
                        print("    " + line)
        else:
            print(f"calls={payload['calls']} lastRet={payload['lastRet']}")

    script.on("message", on_message)
    script.load()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
