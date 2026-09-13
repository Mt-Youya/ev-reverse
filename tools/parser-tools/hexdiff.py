#!/usr/bin/env python3
"""Differential scan: which 32-hex values appear in memory when a key endpoint is hit?

getDownEVSKey / getEvsSignUrl / getPlayAuthorityEVS are the only responses we cannot read
(zip:0, AES-encrypted, no inflate). If any of them hands out per-segment keys, the values
must materialise in memory right after the response lands.

So: take a baseline of every '"<32 hex>"' value in memory, trigger the action, sample again,
and report what is NEW. Much lighter than scanning for a whole base64 body: the search is a
fixed 3-byte prefix (22 3a 22) plus 32 wildcards, so Memory.scanSync does the work natively.
"""
import frida
import sys
import time

JS = r"""
'use strict';
var HEX = '0123456789abcdefABCDEF';
rpc.exports = {
  hexvals: function () {
    // '":"' followed by any 32 bytes; keep the ones that are hex
    var pat = '22 3a 22 ' + (new Array(33).join('?? ').trim());
    var out = {};
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x40000000) return;
      try {
        Memory.scanSync(r.base, r.size, pat).forEach(function (m) {
          try {
            var b = m.address.add(3).readByteArray(32);
            var u = new Uint8Array(b), s = '';
            for (var i = 0; i < 32; i++) {
              var c = String.fromCharCode(u[i]);
              if (HEX.indexOf(c) === -1) return;
              s += c;
            }
            out[s.toLowerCase()] = 1;
          } catch (e) {}
        });
      } catch (e) {}
    });
    return Object.keys(out);
  },
  // also grab any 32-hex that appears as a bare token, for the diff
  barenhex: function () {
    var pat = new Array(33).join('?? ').trim();   // 32 wildcards: too broad, so skip
    return [];
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


def main():
    pid = pick_pid()
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching pid=%d" % pid, flush=True)
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on('message', lambda m, d: None)
    script.load()
    ex = script.exports_sync

    print("taking baseline ...", flush=True)
    t0 = time.time()
    base = set(ex.hexvals())
    print("baseline: %d distinct 32-hex values (%.1fs)" % (len(base), time.time() - t0), flush=True)
    print("\n>>> 现在请在播放器里点「下载」按钮 / 再打开一个课时。30 秒后我会采样。", flush=True)
    for i in range(30, 0, -10):
        time.sleep(10)
        print("    ...%ds" % i, flush=True)
    print("sampling after action ...", flush=True)
    t1 = time.time()
    after = set(ex.hexvals())
    print("after: %d distinct (%.1fs)" % (len(after), time.time() - t1), flush=True)
    new = sorted(after - base)
    gone = sorted(base - after)
    print("\nNEW 32-hex values: %d" % len(new), flush=True)
    for v in new[:40]:
        print("   %s" % v, flush=True)
    print("\ndisappeared: %d" % len(gone), flush=True)
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
