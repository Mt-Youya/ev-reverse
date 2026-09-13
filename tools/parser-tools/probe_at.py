#!/usr/bin/env python3
"""Hook one or more RVAs in a live module and dump the context at each hit.

Light by design: a few Interceptor hooks, a hit cap, and one stack read per hit.
No memory scanning and no page monitoring.

Usage:
  <python> -u probe_at.py <module> <rva_hex> [max_hits] [stack_bytes] [more_rvas...]

Example:
  python -u probe_at.py PlayerLibRender56_vs.dll 0xb2ef68 30 0x2000 0x792c78

For each hit it reports registers that point at printable strings, the strings found on
the stack (looking for ".ts" filenames and the long tk+filename+salt concatenation), and
a 32-hex scan.
"""
import frida
import sys
import time

JS = r"""
'use strict';
var MOD = '__MOD__';
var RVAS = __RVAS__;
var MAX_HITS = __MAXHITS__;
var STACK = __STACK__;
var m = Process.getModuleByName(MOD);
var counts = {};

var REGS = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','r8','r9','r10','r11','r12','r13','r14','r15','rsp'];

function strAt(p) {
  try {
    var s = p.readCString(256);
    if (!s || s.length < 4) return null;
    var ok = 0;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 32 && c < 127) ok++; }
    return (ok / s.length > 0.85) ? s : null;
  } catch (e) { return null; }
}
function scanStrings(sp, len) {
  var out = [], hexOut = [], tsOut = [];
  try {
    var u = new Uint8Array(sp.readByteArray(len));
    var cur = '', start = 0;
    for (var i = 0; i <= u.length; i++) {
      var c = (i < u.length) ? u[i] : 0;
      if (c >= 32 && c < 127) { if (!cur) start = i; cur += String.fromCharCode(c); }
      else {
        if (cur.length >= 8) {
          out.push(cur);
          if (cur.indexOf('.ts') !== -1) tsOut.push(cur);
          if (/^[0-9a-fA-F]{32}$/.test(cur)) hexOut.push(cur);
        }
        cur = '';
      }
    }
  } catch (e) {}
  return { all: out, ts: tsOut, hex: hexOut };
}

RVAS.forEach(function (rva) {
  counts[rva] = 0;
  var a = m.base.add(rva);
  try {
    Interceptor.attach(a, {
      onEnter: function () {
        if (counts[rva] >= MAX_HITS) return;
        counts[rva]++;
        var ctx = this.context;
        var strs = {};
        REGS.forEach(function (rn) { var s = strAt(ctx[rn]); if (s) strs[rn] = s; });
        var sc = scanStrings(ctx.rsp, STACK);
        send({t: 'hit', rva: rva.toString(), n: counts[rva],
              regs: strs, ts: sc.ts, hex: sc.hex, all: sc.all.slice(0, 40)});
      }
    });
    send({t: 'armed', rva: rva.toString()});
  } catch (e) { send({t: 'err', rva: rva.toString(), e: String(e)}); }
});
send({t: 'ready'});
"""


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    mod = sys.argv[1]
    max_hits = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    stack = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x1000
    rvas = [int(sys.argv[2], 16) if sys.argv[2].startswith('0x') else int(sys.argv[2])]
    for extra in sys.argv[5:]:
        rvas.append(int(extra, 16) if extra.startswith('0x') else int(extra))

    pid = None
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            pid = p.pid
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching pid=%d module=%s rvas=%s max_hits=%d" %
          (pid, mod, [hex(r) for r in rvas], max_hits), flush=True)

    js = (JS.replace('__MOD__', mod).replace('__RVAS__', '[' + ', '.join(hex(r) for r in rvas) + ']')
            .replace('__MAXHITS__', str(max_hits)).replace('__STACK__', str(stack)))
    session = frida.attach(pid)
    script = session.create_script(js)
    seen = set()

    def on_message(msg, data):
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'armed':
            print("[*] armed at +%s" % p['rva'], flush=True)
        elif t == 'hit':
            sig = (p['rva'], tuple(p.get('ts') or []), tuple(p.get('hex') or []), tuple(sorted((p.get('regs') or {}).items())))
            if sig in seen:
                return
            seen.add(sig)
            print("\n### hit +%s #%s" % (p['rva'], p['n']), flush=True)
            for rn, sv in (p.get('regs') or {}).items():
                print("   %s -> %r" % (rn, sv), flush=True)
            if p.get('hex'):
                print("   32-hex on stack: %s" % p['hex'], flush=True)
            if p.get('ts'):
                print("   .ts on stack: %s" % p['ts'][:6], flush=True)
            print("   stack strings: %s" % (p.get('all') or [])[:25], flush=True)
        elif t == 'err':
            print("[err] +%s: %s" % (p.get('rva'), p.get('e')), flush=True)
        elif t == 'ready':
            print("[*] ready. 请播放视频。", flush=True)

    script.on('message', on_message)
    script.load()
    secs = 150
    try:
        time.sleep(secs)
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
