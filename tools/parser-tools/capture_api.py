"""One session that records both halves of the API conversation: what goes out, what came back.

Two hooks, because the two facts are in different places:

* `nghttp2_submit_request` carries the outgoing header block, which is the only place the bearer
  token exists in a readable form — it is issued by the server and expires, so it cannot be derived
  or replayed from an old capture.
* `0x1EA10` is the single response decryption in the DLL. Its key argument names the endpoint family
  and its output is the plaintext, so one hook attributes every response *and* hands over what it
  said. That is also how the two call sites that have never fired will identify themselves.

Both in one Frida session on purpose: a fresh instance tolerates one injection, and repeated
attaches to the same player have been refused outright (`VirtualAllocEx` -> ACCESS_DENIED), which is
a dead instrument rather than a negative about the player.

    python capture_api.py --pid 4348 --seconds 900
"""

import argparse
import json
import os
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "api")
TOKENS = os.path.join(HERE, "captured", "tokens.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var nghttp2 = Process.getModuleByName('nghttp2.dll');

function toAscii(ptr, max) {
  try {
    var size = ptr.add(0x10).readU64().toNumber();
    var cap = ptr.add(0x18).readU64().toNumber();
    if (size <= 0 || size > max || cap < size || cap > 8 * 1024 * 1024) return null;
    var data = (cap < 16) ? ptr.readByteArray(size) : ptr.readPointer().readByteArray(size);
    if (!data) return null;
    var u = new Uint8Array(data), out = '';
    for (var i = 0; i < u.length; i++) out += String.fromCharCode(u[i]);
    return out;
  } catch (e) { return null; }
}

// --- outgoing headers: the token lives here and nowhere else -------------------------------
// Both entry points, because which one this build calls is not something to assume: hooking only
// `nghttp2_submit_request` armed cleanly and never fired, while every request was still going out.
var submitSyms = ['nghttp2_submit_request', 'nghttp2_submit_request2'];
var armed = 0;
submitSyms.forEach(function (sym) {
  var submit = nghttp2.findExportByName(sym);
  if (!submit) return;
  armed += 1;
  Interceptor.attach(submit, {
    onEnter: function (args) {
      var nva = args[2], nvlen = args[3].toInt32();
      if (!nva || nvlen <= 0 || nvlen > 64) return;
      var headers = [];
      for (var i = 0; i < nvlen; i++) {
        try {
          // nghttp2_nv is 40 bytes on x64: name, value, namelen, valuelen, flags. A 16-byte stride
          // reads three headers out of one and returns null strings, which is a hook that arms and
          // reports nothing.
          var record = nva.add(i * 40);
          var namePtr = record.readPointer();
          var valuePtr = record.add(8).readPointer();
          var nameLen = record.add(16).readU64().toNumber();
          var valueLen = record.add(24).readU64().toNumber();
          if (namePtr.isNull() || valuePtr.isNull()) continue;
          if (nameLen <= 0 || nameLen > 256 || valueLen < 0 || valueLen > 8192) continue;
          headers.push([namePtr.readUtf8String(nameLen), valuePtr.readUtf8String(valueLen)]);
        } catch (e) {}
      }
      if (headers.length) send({ t: 'request', sym: sym, headers: headers });
    }
  });
});
send({ t: 'ready', part: 'headers', armed: armed });

// --- response decryption: key + plaintext --------------------------------------------------
Interceptor.attach(dll.base.add(0x1EA10), {
  onEnter: function (args) {
    this.out = args[0];
    this.key = toAscii(args[2], 4096);
    this.caller = this.returnAddress.sub(dll.base).toInt32();
  },
  onLeave: function () {
    var size = 0;
    try {
      size = this.out.add(0x10).readU64().toNumber();
      if (size <= 0 || size > 4 * 1024 * 1024) return;
      var cap = this.out.add(0x18).readU64().toNumber();
      var data = (cap < 16) ? this.out.readByteArray(size) : this.out.readPointer().readByteArray(size);
      if (!data) return;
      send({ t: 'payload', caller: this.caller, key: this.key, size: size }, data);
    } catch (e) {}
  }
});
send({ t: 'ready', part: 'decrypt', at: dll.base.add(0x1EA10).toString() });
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--follow", action="store_true",
                    help="wait for a fresh EVPlayer2 to appear and attach to that one; a player "
                         "that has already been injected into several times refuses further "
                         "injection (VirtualAllocEx -> ACCESS_DENIED)")
    args = ap.parse_args()

    if args.follow and not args.pid:
        import subprocess
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
                print(f"found pid {args.pid}", flush=True)
                break
            known |= pids
            time.sleep(3)

    if not args.pid:
        print("pass --pid, or --follow to wait for one")
        return 1

    print(f"attaching pid={args.pid}", flush=True)
    session = frida.attach(args.pid)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(OUT, stamp)
    os.makedirs(out, exist_ok=True)

    seen = set()
    payloads = 0
    tokens = set()

    def on_message(message, data):
        nonlocal payloads
        if message.get('type') != 'send':
            print(json.dumps(message)[:200].encode("ascii", "replace").decode(), flush=True)
            return
        payload = message['payload']
        kind = payload.get('t')
        if kind == 'ready':
            print(f"armed: {payload}", flush=True)
            return
        if kind == 'error':
            print(f"ERROR {payload}", flush=True)
            return
        if kind == 'request':
            headers = {}
            for name, value in payload['headers']:
                if isinstance(name, str) and isinstance(value, str):
                    headers[name.lower()] = value
            path = headers.get(':path', '')
            token = headers.get('authorization', '')
            if token and token not in tokens:
                tokens.add(token)
                with open(TOKENS, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"at": time.time(), "path": path,
                                             "authorization": token,
                                             "headers": payload['headers']}) + "\n")
                names = ", ".join(name for name, _ in payload['headers'])
                print(f"TOKEN for {path}: {token[:60]}… ({len(token)} chars) -> {TOKENS}",
                      flush=True)
                print(f"   headers: {names}", flush=True)
            return
        if kind != 'payload' or data is None:
            return
        blob = bytes(data)
        digest = hash(blob)
        if digest in seen:
            return
        seen.add(digest)
        payloads += 1
        name = f"{payloads:03d}-{payload['caller']:#08x}.bin"
        with open(os.path.join(out, name), "wb") as handle:
            handle.write(blob)
        head = blob[:24].hex(" ")
        gzip_like = blob[:2] == b"\x1f\x8b"
        print(f"[{payloads:03d}] caller={payload['caller']:#08x} key={payload['key']!r} "
              f"{payload['size']:7d} B gzip={gzip_like} {head}", flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s — open a lesson or press download", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass
    print(f"\n{payloads} payload(s) in {out}; {len(tokens)} token(s) seen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
