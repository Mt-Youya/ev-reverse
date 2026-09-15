"""Capture every payload the player decrypts, with the call site and the key that opened it.

Why a hook and not a proxy: the API is HTTP/2 over TLS, and every response body is encrypted
before it reaches the socket (`docs/API.md`: `encrypt: 1` on all 317 captured responses). A proxy
鈥?even a trusted-certificate MITM 鈥?would hand back the same ciphertext that `captured/bodies.bin`
already holds. The plaintext exists only inside the process, one instruction after the decryption.

`0x1EA10(out, input, const std::string* key, bool* ok)` is that instruction, and it is the only
response decryption in the DLL 鈥?five call sites, each of which first asks the Bridge layer for a
key. Payloads differ per call site (JSON for the segment lists, raw bytes for other endpoints), so
each capture records the return address too: that names the caller, and the caller names the
endpoint.

    python capture_plaintext.py --pid 1234 --seconds 900
    python capture_plaintext.py --seconds 900        # attach to the only EVPlayer2 running

While it is attached, open a lesson or press download. Nothing is played, nothing is written to the
player, and the capture directory is `captured/plaintext/`.
"""

import argparse
import json
import os
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
PLAIN = os.path.join(HERE, "captured", "plaintext")
KEYS = os.path.join(HERE, "captured", "plaintext_keys.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var FUNC = 0x1EA10;

/** MSVC std::string: [0] buffer or heap pointer, [0x10] size, [0x18] capacity. */
function readStdBytes(ptr, max) {
  try {
    var size = ptr.add(0x10).readU64().toNumber();
    var cap = ptr.add(0x18).readU64().toNumber();
    if (size <= 0 || size > max || cap < size || cap > 8 * 1024 * 1024) return null;
    return (cap < 16) ? ptr.readByteArray(size) : ptr.readPointer().readByteArray(size);
  } catch (e) { return null; }
}

function toAscii(ptr, max) {
  var buffer = readStdBytes(ptr, max);
  if (!buffer) return null;
  var u = new Uint8Array(buffer), out = '';
  for (var i = 0; i < u.length; i++) out += String.fromCharCode(u[i]);
  return out;
}

Interceptor.attach(dll.base.add(FUNC), {
  onEnter: function (args) {
    this.out = args[0];
    this.key = toAscii(args[2], 4096);
    this.input = toAscii(args[1], 4 * 1024 * 1024);   // base64 text, the envelope's `result`
    this.caller = this.returnAddress.sub(dll.base).toInt32();
  },
  onLeave: function () {
    var data = readStdBytes(this.out, 4 * 1024 * 1024);
    if (!data || data.byteLength < 8) return;
    // `send`'s payload must be JSON and its data must be a buffer; a string here raises
    // "expected a buffer-like object" once per call and captures nothing.
    send({ t: 'payload', caller: this.caller, key: this.key, input: this.input }, data);
  }
});
send({ t: 'ready', at: dll.base.add(FUNC).toString(), base: dll.base.toString() });
"""


def pick_pid(explicit):
    if explicit:
        return explicit
    cands = [p.pid for p in frida.get_local_device().enumerate_processes()
             if p.name.lower() == 'evplayer2.exe']
    return cands[0] if cands else None


def describe(data):
    """What a payload looks like, without assuming it is JSON."""
    head = data[:8]
    if head[:1] == b"\x47":
        return "mpeg-ts?"
    stripped = data.lstrip()
    if stripped[:1] in (b"{", b"["):
        return "json"
    try:
        data.decode("utf-8")
        return "utf-8 text"
    except UnicodeDecodeError:
        pass
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    return "binary"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=900)
    args = ap.parse_args()

    pid = pick_pid(args.pid)
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print(f"attaching pid={pid}", flush=True)
    session = frida.attach(pid)

    # One directory per run, and the run's stamp on every record. Numbered files restart at 001
    # each time, so a log that is appended to across runs ends up pointing later records at earlier
    # files: the join looks sound and reports the wrong payload for a key.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    plain = os.path.join(PLAIN, stamp)
    os.makedirs(plain, exist_ok=True)

    state = {"count": 0}
    seen = set()
    keys = {}

    def on_message(message, data):
        if message.get('type') != 'send':
            print(json.dumps(message)[:200].encode("ascii", "replace").decode(), flush=True)
            return
        payload = message['payload']
        if payload.get('t') == 'ready':
            print(f"armed at {payload['at']} (base {payload['base']}); "
                  f"open a lesson or press download", flush=True)
            return
        if data is None:
            return
        blob = bytes(data)
        digest = hash(blob)
        if digest in seen:
            return
        seen.add(digest)
        state["count"] += 1
        index = state["count"]
        path = os.path.join(plain, f"{index:03d}.bin")
        with open(path, "wb") as handle:
            handle.write(blob)
        kind = describe(blob)
        key = payload.get('key')
        cipher = payload.get('input') or ''
        if key:
            keys.setdefault(key, 0)
            keys[key] += 1
        record = {"session": stamp, "index": index, "caller_rva": payload.get('caller'),
                  "bytes": len(blob), "kind": kind, "key": key, "cipher": cipher,
                  "path": os.path.join(stamp, os.path.basename(path))}
        with open(KEYS, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        line = (f"[{index:03d}] caller={payload.get('caller'):#08x} {kind:10s} "
                f"{len(blob):7d} B  key={key!r}")
        print(line.encode("ascii", "replace").decode(), flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
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
    print(f"\n{state['count']} distinct payload(s) in {plain}")
    for key, times in sorted(keys.items(), key=lambda kv: -kv[1]):
        printable = key.encode("ascii", "replace").decode()
        print(f"  key {len(key):3d} chars, {times} payload(s): {printable!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
