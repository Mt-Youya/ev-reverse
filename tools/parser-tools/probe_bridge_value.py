"""Read the Bridge value that API responses are decrypted with.

Every response body on this player's API is encrypted (`docs/API.md`: `encrypt: 1` on all 317
captured responses), and `0x42A30` decrypts them with a string it obtains from the same Bridge
lookup that yields the key derivation's constant. That lookup's result lands in a `std::string`
passed to `0x1EA10` (`(out, input, const std::string* key, bool* ok)`), so hooking there names the
material that would make the tool able to fetch a segment list by itself.

    python probe_bridge_value.py --pid 1234 --seconds 120
"""

import argparse
import json
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var HOOK_RVA = 0x1EA10;

function readStdString(ptr) {
  // MSVC std::string: [0] buffer or pointer, [0x10] size, [0x18] capacity
  try {
    var size = ptr.add(0x10).readU64().toNumber();
    var cap = ptr.add(0x18).readU64().toNumber();
    if (size <= 0 || size > 4096 || cap > 65536) return null;
    var data = (cap < 16) ? ptr.readByteArray(size) : ptr.readPointer().readByteArray(size);
    if (!data) return null;
    var u = new Uint8Array(data), out = '';
    for (var i = 0; i < u.length; i++) {
      if (u[i] < 32 || u[i] > 126) return null;
      out += String.fromCharCode(u[i]);
    }
    return out;
  } catch (e) { return null; }
}

var target = dll.base.add(HOOK_RVA);
Interceptor.attach(target, {
  onEnter: function (args) {
    var key = readStdString(args[2]);
    if (key) send({ t: 'key', key: key });
  }
});
send({ t: 'ready', at: target.toString() });
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=120)
    args = ap.parse_args()

    pid = args.pid
    if pid is None:
        cands = [p.pid for p in frida.get_local_device().enumerate_processes()
                 if p.name.lower() == 'evplayer2.exe']
        pid = cands[0] if cands else None
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1

    session = frida.attach(pid)
    seen = set()

    def on_message(message, data):
        if message.get('type') != 'send':
            print(f"[frida] {json.dumps(message)[:200]}", flush=True)
            return
        payload = message['payload']
        if payload.get('t') == 'ready':
            print(f"armed at {payload['at']}", flush=True)
            return
        key = payload.get('key')
        if key and key not in seen:
            seen.add(key)
            print(f"bridge value for response decryption ({len(key)} chars): {key}", flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s for API response decryption", flush=True)
    try:
        time.sleep(args.seconds)
    finally:
        session.detach()
    print(f"{len(seen)} distinct value(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
