"""Catch the three inputs of the key derivation as the player builds them, and write them down.

`0x1FD60` is the `std::string = MD5_hex(input)` wrapper the key path calls last (see
`docs/KEY-DERIVATION.md`). Its second argument is the *whole* concatenation — `tk + filename +
extra` — as a NUL-terminated C string, so hooking one instruction yields the third input that no
capture contains, for every segment the player decrypts while attached.

Two corrections this script carries from its first run, both of which made it silent rather than
wrong: the function's first byte is a redundant REX prefix (`40 53`, not `53`), and a failed
prologue check must be *reported* — a hook that never arms and a hook that arms and sees nothing
look identical from the outside.

    python probe_kdf.py --pid 1234 --seconds 120 --out capture_kdf.jsonl
"""

import argparse
import hashlib
import json
import os
import sys
import time

import frida

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var HOOK_RVA = 0x1FD60;
// push rbx; sub rsp,0x20; mov rbx,rcx  (the 0x40 is a redundant REX prefix on push rbx)
var PROLOGUE = '40 53 48 83 ec 20 48 8b d9';

function readCString(ptr, max) {
  var out = '';
  for (var i = 0; i < max; i += 64) {
    var chunk = ptr.add(i).readByteArray(Math.min(64, max - i));
    if (!chunk) return out;
    var u = new Uint8Array(chunk);
    for (var j = 0; j < u.length; j++) {
      if (u[j] === 0) return out;
      out += String.fromCharCode(u[j]);
    }
  }
  return out;
}

var target = dll.base.add(HOOK_RVA);
var bytes = new Uint8Array(target.readByteArray(PROLOGUE.split(' ').length));
var hex = '';
for (var i = 0; i < bytes.length; i++) hex += ('0' + bytes[i].toString(16)).slice(-2) + (i + 1 < bytes.length ? ' ' : '');
if (hex !== PROLOGUE) {
  send({ t: 'wrong-target', at: target.toString(), expected: PROLOGUE, actual: hex });
} else {
  Interceptor.attach(target, {
    onEnter: function (args) {
      // rcx = std::string* out, rdx = const char* input (tk + filename + extra)
      var text = readCString(args[1], 512);
      if (text && text.length >= 32) send({ t: 'input', text: text });
    }
  });
  send({ t: 'ready', base: dll.base.toString(), at: target.toString() });
}
"""


def parse(text):
    """Split `<tk(32 hex)><filename ending .ts><extra>` the way the code builds it."""
    if len(text) < 33:
        return None
    tk, rest = text[:32], text[32:]
    if not all(c in '0123456789abcdef' for c in tk):
        return None
    end = rest.find('.ts')
    if end < 0:
        return None
    return tk, rest[:end + 3], rest[end + 3:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "captured", "kdf_inputs.jsonl"))
    args = ap.parse_args()

    pid = args.pid
    if pid is None:
        cands = [p.pid for p in frida.get_local_device().enumerate_processes()
                 if p.name.lower() == 'evplayer2.exe']
        pid = cands[0] if cands else None
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print(f"attaching pid={pid}", flush=True)
    session = frida.attach(pid)

    seen = set()
    hits = []
    state = {'armed': False}

    def on_message(message, data):
        if message.get('type') != 'send':
            print(f"[frida] {json.dumps(message)[:300]}", flush=True)
            return
        payload = message['payload']
        kind = payload.get('t')
        if kind == 'ready':
            state['armed'] = True
            print(f"armed at {payload['at']} (module base {payload['base']})", flush=True)
            return
        if kind == 'wrong-target':
            print(f"NOT ARMED: bytes at {payload['at']} are {payload['actual']}, "
                  f"expected {payload['expected']}", flush=True)
            return
        if kind != 'input':
            return
        text = payload['text']
        if text in seen:
            return
        seen.add(text)
        digest = hashlib.md5(text.encode()).hexdigest()
        parsed = parse(text)
        record = {"md5_input": text, "md5": digest}
        if parsed:
            record.update({"tk": parsed[0], "file": parsed[1], "extra": parsed[2]})
        hits.append(record)
        print(f"\nmd5_input = {text}", flush=True)
        print(f"  md5     = {digest}", flush=True)
        if parsed:
            print(f"  tk      = {parsed[0]}", flush=True)
            print(f"  file    = {parsed[1]}", flush=True)
            print(f"  EXTRA   = {parsed[2]!r}   <-- the input no capture contains", flush=True)
        else:
            print("  (does not parse as tk + filename + extra)", flush=True)
        # Write through, not at the end: a long watch is exactly when someone wants to read what has
        # been caught so far, and a run that is killed at its window would otherwise leave nothing.
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    print(f"watching for {args.seconds:.0f}s", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    print(f"\n{len(hits)} derivation(s) caught, {len(hits) and args.out or 'nothing written'}")
    extras = sorted({record.get('extra') for record in hits if 'extra' in record})
    for extra in extras:
        print(f"  distinct extra: {extra!r}")
    return 0 if state['armed'] else 2


if __name__ == "__main__":
    sys.exit(main())
