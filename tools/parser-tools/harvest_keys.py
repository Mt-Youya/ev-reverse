#!/usr/bin/env python3
"""Harvest every 32-hex string in memory and test each one as a segment key.

The segment key is a 32-char lowercase-hex string (verified: the context's schedule decodes
to exactly that, and it decrypts real segments). So if the key for a given file is anywhere
in memory as text, this finds it: collect all 32-hex runs, then AES-try each as the key for
a file the player itself downloaded this session (first block only -> microseconds each).

Read-only. The player's RW memory we scan is only tens of MB, so a regex pass is cheap.
"""
import frida
import os
import re
import sys
import time
import hashlib

from Crypto.Cipher import AES
from Crypto.Util.strxor import strxor

here = os.path.dirname(os.path.abspath(__file__))
DL = r"D:\Downloads\EVPlayer2Downloads"

JS = r"""
'use strict';
function isHex(c) {
  return (c >= 0x30 && c <= 0x39) || (c >= 0x61 && c <= 0x66) || (c >= 0x41 && c <= 0x46);
}
rpc.exports = {
  hexstrings: function () {
    var found = {};
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x10000000) return;
      var CH = 0x200000;                       // 2 MB chunks
      for (var off = 0; off < r.size; off += CH) {
        var len = Math.min(CH, r.size - off);
        var buf;
        try { buf = r.base.add(off).readByteArray(len); } catch (e) { continue; }
        if (!buf) continue;
        var u = new Uint8Array(buf);
        var start = -1;
        for (var i = 0; i <= u.length; i++) {
          var c = (i < u.length) ? u[i] : 0;
          if (isHex(c)) { if (start < 0) start = i; }
          else {
            var n = i - start;
            if (start >= 0 && n === 32) {
              var s = '';
              for (var k = start; k < i; k++) s += String.fromCharCode(u[k]);
              found[s.toLowerCase()] = 1;
            }
            start = -1;
          }
        }
      }
    });
    return Object.keys(found);
  }
};
send({ t: 'ready' });
"""


def pick_pid():
    cands = [p.pid for p in frida.get_local_device().enumerate_processes()
             if p.name.lower() == 'evplayer2.exe']
    for pid in cands:
        try:
            s = frida.attach(pid)
            probe = s.create_script(
                "send(!!(function(){try{Process.getModuleByName('PlayerLibRender56_vs.dll');return 1}catch(e){return 0}})());")
            res = {'v': None}
            probe.on('message', lambda m, d: res.update(v=m.get('payload')))
            probe.load()
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


def newest_files(n=6):
    out = []
    for name in os.listdir(DL):
        if not name.endswith('.ts'):
            continue
        p = os.path.join(DL, name)
        try:
            out.append((os.path.getmtime(p), name, os.path.getsize(p)))
        except Exception:
            pass
    out.sort(reverse=True)
    return out[:n]


def main():
    pid = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        pid = int(sys.argv[1])
        print("using pid=%d (from argv)" % pid, flush=True)
    else:
        pid = pick_pid()
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    files = newest_files(6)
    print("newest downloaded segments:")
    for mt, n, sz in files:
        print("   %s (%d bytes)" % (n, sz))

    print("\nattaching pid=%d ..." % pid, flush=True)
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on('message', lambda m, d: None)
    script.load()
    t0 = time.time()
    cands = script.exports_sync.hexstrings()
    print("32-hex strings in memory: %d  (%.1fs)" % (len(cands), time.time() - t0), flush=True)

    hits = 0
    for _, fn, _ in files:
        path = os.path.join(DL, fn)
        data = open(path, 'rb').read(4096)
        if len(data) < 188 * 2:
            continue
        mask = hashlib.md5(fn.encode()).hexdigest()[:16].encode()
        ct0 = strxor(data[:16], mask)
        ct11 = strxor(data[176:192], mask)
        for cand in cands:
            key = cand.encode()
            try:
                a = AES.new(key, AES.MODE_ECB)
                if a.decrypt(ct0)[0] == 0x47 and a.decrypt(ct11)[0] == 0x47:
                    print("\n*** KEY FOUND for %s: %s" % (fn, cand), flush=True)
                    hits += 1
                    # full verify
                    blob = open(path, 'rb').read()
                    pt = AES.new(key, AES.MODE_ECB).decrypt(strxor(blob, mask * (len(blob) // 16)))
                    pad = len(pt) % 188
                    if pad and pad <= 16 and pt[-pad:] == b'#' * pad:
                        pt = pt[:-pad]
                    ok = bool(pt) and len(pt) % 188 == 0 and all(b == 0x47 for b in pt[::188])
                    print("    full-file verify: %s (%d bytes)" % ('VALID MPEG-TS' if ok else 'failed', len(pt)), flush=True)
            except Exception:
                pass
    print("\ntested %d files x %d candidate keys; hits=%d" % (len(files), len(cands), hits), flush=True)
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
