#!/usr/bin/env python3
"""Snatch the plaintext of zip:0 responses (getDownEVSKey etc.) from memory.

Why: every response we can read (zip:1) is only the catalog or the segment manifests; the
zip:0 ones -- getDownEVSKey ("download EVS key"), getEvsSignUrl, getPlayAuthorityEVS,
getPlaySubtitle -- are the only un-read responses and the prime suspect for where the
per-segment keys come from.

How: hook nghttp2's response-body callback (the same channel capture_all.py uses). The
instant a zip:0 envelope arrives we hold its base64 "result". Its decoded ciphertext and
the AES plaintext it turns into are allocated by the same code path at the same moment, so
we scan process memory for the base64 text and dump the neighbourhood, then look for JSON
that is NOT the envelope we already know.

Light: hooks mirror capture_all.py; memory scanning happens only when a zip:0 body lands.
"""
import frida
import os
import sys
import json
import re
import time

here = os.path.dirname(os.path.abspath(__file__))

JS = r"""
'use strict';
function mo(n) { try { return Process.getModuleByName(n); } catch (e) { return null; } }
function fe(mod, name) { var m = mo(mod); if (!m) return null; try { return m.findExportByName(name); } catch (e) { return null; } }
function bytesToStr(b) { var u = new Uint8Array(b), s = ''; for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return s; }

var hooked = {};
function hookSetter(sym, kind) {
  var a = fe('nghttp2.dll', sym);
  if (!a) { send({ t: 'setup', m: 'setter missing ' + sym }); return; }
  Interceptor.attach(a, {
    onEnter: function (args) {
      var cb = args[1];
      if (!cb || cb.isNull() || hooked[kind]) return;
      hooked[kind] = true;
      if (kind === 'hdr') {
        Interceptor.attach(cb, {
          onEnter: function (a2) {
            try {
              var nl = a2[3].toInt32(), vl = a2[5].toInt32();
              send({ t: 'hdr', name: a2[2].readUtf8String(nl), value: a2[4].readUtf8String(vl) });
            } catch (e) {}
          }
        });
      } else {
        Interceptor.attach(cb, {
          onEnter: function (a2) {
            try {
              var n = a2[4].toInt32();
              if (n > 0 && n <= 262144) send({ t: 'body', n: n }, a2[3].readByteArray(n));
            } catch (e) {}
          }
        });
      }
      send({ t: 'setup', m: 'hooked ' + kind + ' callback' });
    }
  });
}
hookSetter('nghttp2_session_callbacks_set_on_header_callback', 'hdr');
hookSetter('nghttp2_session_callbacks_set_on_data_chunk_recv_callback', 'body');

rpc.exports = {
  /** search memory for an ASCII needle; return printable context around each hit */
  find: function (needle) {
    var pat = needle.split('').map(function (c) { return ('0' + c.charCodeAt(0).toString(16)).slice(-2); }).join(' ');
    var out = [];
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x20000000) return;
      try {
        Memory.scanSync(r.base, r.size, pat).forEach(function (m) {
          if (out.length >= 8) return;
          var start = m.address.sub(0x4000);
          var buf;
          try { buf = start.readByteArray(0x8000); } catch (e) { return; }
          var u = new Uint8Array(buf), s = '', runs = [], cur = '';
          for (var i = 0; i < u.length; i++) {
            var c = u[i];
            if (c >= 32 && c < 127) cur += String.fromCharCode(c);
            else { if (cur.length >= 24) runs.push(cur); cur = ''; }
          }
          if (cur.length >= 24) runs.push(cur);
          out.push({ addr: m.address.toString(), runs: runs.slice(0, 10) });
        });
      } catch (e) {}
    });
    return out;
  }
};
send({ t: 'ready' });
"""


def pick_pid():
    """There can be more than one EVPlayer2.exe (helper/stub); pick the one with the player DLL."""
    cands = [p.pid for p in frida.get_local_device().enumerate_processes()
             if p.name.lower() == 'evplayer2.exe']
    for pid in cands:
        try:
            s = frida.attach(pid)
            ok = s.create_script(
                "send(!!(function(){try{Process.getModuleByName('PlayerLibRender56_vs.dll');return 1}catch(e){return 0}})());"
            )
            res = {'v': None}
            ok.on('message', lambda m, d: res.update(v=m.get('payload')))
            ok.load()
            time.sleep(0.7)
            try:
                s.detach()
            except Exception:
                pass
            if res['v']:
                return pid
        except Exception:
            continue
    return cands[0] if cands else None


def main():
    pid = pick_pid()
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching pid=%d" % pid, flush=True)
    session = frida.attach(pid)
    script = session.create_script(JS)

    state = {'result': None, 'path': None}
    handled = set()

    def on_message(msg, data):
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'setup':
            print("[setup] %s" % p['m'], flush=True)
            return
        if t != 'body' or not data:
            return
        raw = bytes(data)
        try:
            o = json.loads(raw.decode('utf-8'))
        except Exception:
            return
        if not isinstance(o, dict):
            return
        res = o.get('result')
        if not isinstance(res, str) or len(res) < 40:
            return
        sig = (o.get('zip'), o.get('encrypt'), len(res))
        print("[body] zip=%s encrypt=%s result_len=%d uuid=%s" %
              (o.get('zip'), o.get('encrypt'), len(res), o.get('uuid')), flush=True)
        if o.get('zip') == 1:
            return                      # we can read those via zlib already
        needle = res[:48]
        if needle in handled:
            return
        handled.add(needle)
        print("    scanning memory for the base64 of a zip=0 result ...", flush=True)
        try:
            hits = script.exports_sync.find(needle)
        except Exception as ex:
            print("    scan failed: %s" % ex, flush=True)
            return
        print("    hits: %d" % len(hits), flush=True)
        for h in hits:
            print("    @%s" % h['addr'], flush=True)
            for run in h['runs']:
                # skip runs that are just the envelope/base64 we already have
                if run.startswith(res[:24]) or 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' in run:
                    continue
                print("        %r" % run[:400], flush=True)

    script.on('message', on_message)
    script.load()
    print("[*] ready. 请在播放器里点「下载」并打开几个课程，触发 zip:0 接口。", flush=True)
    try:
        time.sleep(240)
    except KeyboardInterrupt:
        pass
    try:
        session.detach()
    except Exception:
        pass
    print("done.", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
