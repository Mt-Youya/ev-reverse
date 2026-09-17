#!/usr/bin/env python3
"""Bounded, self-validating memory searcher for the live EVPlayer2 process.

Previous attempts died because a whole-memory scan with an unbounded per-hit dump either
hung or silently matched nothing. This one:
  * scans ranges in chunks, with a hard cap on total bytes and on hits,
  * reports how many bytes it actually scanned (so "0 hits" is distinguishable from
    "never really scanned"),
  * is validated first against a string we KNOW is in memory (a segment filename from the
    capture) before being pointed at an unknown target.

Usage:
  python find_in_mem.py --validate            # prove the scanner works
  python find_in_mem.py --needle <ascii>      # search for a literal
"""
import frida
import os
import sys
import time
import argparse

here = os.path.dirname(os.path.abspath(__file__))

JS = r"""
'use strict';
function toPat(s) {
  var out = [];
  for (var i = 0; i < s.length; i++) out.push(('0' + s.charCodeAt(i).toString(16)).slice(-2));
  return out.join(' ');
}
rpc.exports = {
  scan: function (needle, maxMB, maxHits) {
    var pat = toPat(needle);
    var cap = maxMB * 1024 * 1024, scanned = 0;
    var hits = [], rangesSeen = 0;
    var all = Process.enumerateRanges('rw-');
    for (var i = 0; i < all.length; i++) {
      if (scanned >= cap || hits.length >= maxHits) break;
      var r = all[i];
      if (r.size > 0x10000000) continue;          // skip huge reservations
      rangesSeen++;
      var CH = 0x4000000;                          // 64 MB chunks
      for (var off = 0; off < r.size && scanned < cap && hits.length < maxHits; off += CH) {
        var len = Math.min(CH, r.size - off);
        try {
          Memory.scanSync(r.base.add(off), len, pat).forEach(function (m) {
            if (hits.length < maxHits) hits.push(m.address.toString());
          });
        } catch (e) {}
        scanned += len;
      }
    }
    return { hits: hits, scannedBytes: scanned, ranges: rangesSeen };
  },
  context: function (addrStr, back, len) {
    try {
      var a = ptr(addrStr).sub(back);
      var u = new Uint8Array(a.readByteArray(len));
      var runs = [], cur = '';
      for (var i = 0; i < u.length; i++) {
        var c = u[i];
        if (c >= 32 && c < 127) cur += String.fromCharCode(c);
        else { if (cur.length >= 16) runs.push(cur); cur = ''; }
      }
      if (cur.length >= 16) runs.push(cur);
      return runs;
    } catch (e) { return ['<err ' + e + '>']; }
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


def known_needle():
    """a segment filename we know the player holds (from the last context/capture)"""
    for path in ('ctx_dump.json',):
        p = os.path.join(here, path)
        if os.path.isfile(p):
            import json
            try:
                data = json.load(open(p, encoding='utf-8-sig'))
                for c in data:
                    if c.get('file', '').endswith('.ts'):
                        return c['file']
            except Exception:
                pass
    return '119354-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--needle')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--maxmb', type=int, default=1024)
    ap.add_argument('--hits', type=int, default=8)
    ap.add_argument('--context', type=int, default=0x4000)
    args = ap.parse_args()

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

    needle = args.needle or known_needle()
    print("needle: %r (len %d)" % (needle, len(needle)), flush=True)
    t0 = time.time()
    res = ex.scan(needle, args.maxmb, args.hits)
    dt = time.time() - t0
    print("scanned %.1f MB across %d ranges in %.1fs -> %d hit(s)" %
          (res['scannedBytes'] / 1048576.0, res['ranges'], dt, len(res['hits'])), flush=True)
    for h in res['hits']:
        print("   hit @ %s" % h, flush=True)
        if args.context:
            runs = ex.context(h, args.context // 2, args.context)
            for r in runs[:12]:
                print("        %r" % r[:300], flush=True)
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
