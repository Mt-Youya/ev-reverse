"""Every AES key the player sets up, with the code that set it.

`0x20EC0` is the app's own key setup: `(schedule, const char* key, bits, direction)`. Everything
that encrypts or decrypts goes through it — segment keys, response bodies, the descriptor path —
so one hook enumerates the *key material* of every flow at once, and the return address says which
flow it was. That is the question the earlier probes answered one endpoint at a time.

Hook `0x1EA10` alongside it for the plaintext: the key setup says what opened a payload, this says
what the payload was.

    python capture_keys.py --pid 18340 --seconds 900
    python capture_keys.py --follow --seconds 1800

Writes `captured/keys.jsonl` (one line per setup) and `captured/api/<stamp>/NNN-<caller>.bin`.
"""

import argparse
import json
import os
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "api")
KEYS = os.path.join(HERE, "captured", "keys.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

function cstring(ptr, max) {
  if (!ptr || ptr.isNull()) return null;
  try {
    var text = ptr.readUtf8String(max);
    return text;
  } catch (e) { return null; }
}

// An MSVC `std::string` is { union { char buf[16]; char* ptr; }, size_t size, size_t capacity }.
// Reading one as a C string returns the bytes of its buffer pointer, which is why every payload
// was reported with `key=None`: the key was right there, one indirection away.
function sstring(ptr) {
  if (!ptr || ptr.isNull()) return null;
  try {
    var size = ptr.add(8).readU64().toNumber();
    var cap = ptr.add(16).readU64().toNumber();
    if (size > 0 && size < 512 && cap >= size) {
      var text = (cap < 16) ? ptr.readUtf8String(size) : ptr.readPointer().readUtf8String(size);
      if (text && text.length === size) return text;
    }
  } catch (e) {}
  return cstring(ptr, 256);
}

// --- key setup: (rcx = schedule, rdx = key text, r8d = bits, r9d = 1 encrypt) ----------------
Interceptor.attach(dll.base.add(0x20EC0), {
  onEnter: function (args) {
    var key = cstring(args[1], 256);
    if (!key || key.length < 4) return;
    send({ t: 'key', key: key, bits: args[2].toInt32(), direction: args[3].toInt32(),
           caller: this.returnAddress.sub(dll.base).toInt32() });
  }
});

// --- response decryption: the plaintext, attributed to the same caller ----------------------
Interceptor.attach(dll.base.add(0x1EA10), {
  onEnter: function (args) {
    this.out = args[0];
    this.caller = this.returnAddress.sub(dll.base).toInt32();
    this.key = sstring(args[2]);
  },
  onLeave: function () {
    try {
      var size = this.out.add(0x10).readU64().toNumber();
      var cap = this.out.add(0x18).readU64().toNumber();
      if (size <= 0 || size > 4 * 1024 * 1024) return;
      var data = (cap < 16) ? this.out.readByteArray(size)
                            : this.out.readPointer().readByteArray(size);
      if (!data) return;
      send({ t: 'payload', caller: this.caller, key: this.key }, data);
    } catch (e) {}
  }
});

send({ t: 'ready', base: dll.base.toString() });
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--follow", action="store_true")
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        known = set()
        print("waiting for an EVPlayer2 process…", flush=True)
        while True:
            listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv"],
                                     capture_output=True, text=True).stdout
            pids = {int(row.split('","')[1]) for row in listing.splitlines()
                    if row.startswith('"EVPlayer2.exe"')}
            fresh = pids - known
            if fresh:
                args.pid = sorted(fresh)[-1]
                break
            known |= pids
            time.sleep(3)

    print(f"attaching pid={args.pid}", flush=True)
    session = frida.attach(args.pid)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(OUT, stamp)
    os.makedirs(out, exist_ok=True)

    seen_payload = set()
    seen_key = set()
    payloads = 0

    def on_message(message, data):
        nonlocal payloads
        if message.get('type') != 'send':
            print(json.dumps(message)[:200].encode("ascii", "replace").decode(), flush=True)
            return
        payload = message['payload']
        kind = payload.get('t')
        if kind == 'ready':
            print(f"armed at base {payload['base']}", flush=True)
            return
        if kind == 'key':
            signature = (payload['caller'], payload['key'], payload['bits'], payload['direction'])
            if signature in seen_key:
                return
            seen_key.add(signature)
            record = {"at": time.time(), "kind": "key", **payload}
            with open(KEYS, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            direction = "encrypt" if payload['direction'] else "decrypt"
            print(f"KEY  caller={payload['caller']:#08x} {payload['bits']:4d} bit {direction:7s} "
                  f"{payload['key']!r}", flush=True)
            return
        if kind != 'payload' or data is None:
            return
        blob = bytes(data)
        digest = hash(blob)
        if digest in seen_payload:
            return
        seen_payload.add(digest)
        payloads += 1
        name = f"{payloads:03d}-{payload['caller']:#08x}.bin"
        with open(os.path.join(out, name), "wb") as handle:
            handle.write(blob)
        print(f"[{payloads:03d}] payload caller={payload['caller']:#08x} key={payload['key']!r} "
              f"{len(blob)} B", flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s — use the player normally", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass
    print(f"\n{len(seen_key)} distinct key setup(s), {payloads} payload(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
