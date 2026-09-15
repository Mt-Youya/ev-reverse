"""Print the headers the player sends, so a request of our own can be authenticated the same way.

Writing a request is one thing; being *accepted* is another, and the bearer token is the part of
that which cannot be derived — it is issued by the server and expires. This hooks
`nghttp2_submit_request`, which is where the player's outgoing header block exists as a C array
before it goes on the wire, and prints the headers of any request whose path matches.

    python sniff_headers.py --seconds 300 --match getPlayTimeKeySign
"""

import argparse
import json
import sys
import time

import frida

JS = r"""
'use strict';
var nghttp2 = Process.getModuleByName('nghttp2.dll');

function headerName(sym) {
  var submit = nghttp2.findExportByName(sym);
  if (!submit) { send({ t: 'missing', sym: sym }); return; }
  Interceptor.attach(submit, {
    onEnter: function (args) {
      // nghttp2_submit_request(session, pri_spec, nva, nvlen, data_provider, stream_user_data)
      var nva = args[2], nvlen = args[3].toInt32();
      if (!nva || nvlen <= 0 || nvlen > 64) return;
      var headers = [];
      for (var i = 0; i < nvlen; i++) {
        try {
          var name = nva.add(i * 16).readPointer().readUtf8String();
          var value = nva.add(i * 16 + 8).readPointer().readUtf8String();
          headers.push([name, value]);
        } catch (e) {}
      }
      if (headers.length) send({ t: 'headers', headers: headers });
    }
  });
  send({ t: 'hooked', sym: sym });
}

headerName('nghttp2_submit_request');
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--match", default="")
    args = ap.parse_args()

    pid = args.pid or (frida.get_local_device().enumerate_processes() and None)
    if not pid:
        cands = [p.pid for p in frida.get_local_device().enumerate_processes()
                 if p.name.lower() == 'evplayer2.exe']
        pid = cands[0] if cands else None
    if not pid:
        print("no pid; pass --pid")
        return 1

    session = frida.attach(pid)
    seen = set()

    def on_message(message, data):
        if message.get('type') != 'send':
            return
        payload = message['payload']
        if payload.get('t') in ('hooked', 'missing'):
            print(f"[{payload['t']}] {payload.get('sym')}", flush=True)
            return
        if payload.get('t') != 'headers':
            return
        headers = payload['headers']
        path = next((v for k, v in headers if k == ':path'), '')
        if args.match and args.match not in path:
            return
        token = next((v for k, v in headers if k.lower() == 'authorization'), '')
        key = (path, token)
        if key in seen:
            return
        seen.add(key)
        print(f"\n== {path}", flush=True)
        for name, value in headers:
            shown = value if len(value) < 80 else value[:60] + f"…({len(value)})"
            print(f"   {name}: {shown}", flush=True)
        if token:
            with open("captured/live_token.txt", "w", encoding="utf-8") as handle:
                handle.write(token)
            print("   -> token written to captured/live_token.txt", flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
