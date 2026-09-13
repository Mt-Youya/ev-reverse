#!/usr/bin/env python3
"""Hunt for the key-derivation input buffer.

Idea: if the app builds "tk + filename + salt" in memory before MD5-ing it, that buffer is
still (or was) in the process. So: find a playback context with a valid derived key, then
search process memory for the ASCII of "tk+filename" (and a few other orderings). Whatever
follows the prefix in that buffer is the salt -- or at least the rest of the input.

Read-only: scans memory, installs no hooks.
"""
import frida
import os
import sys
import hashlib
import json

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var lo = dll.base, hi = dll.base.add(0x20000000);

function readStr(obj, off) {
  try {
    var f = obj.add(off);
    var n = f.add(16).readU64().toNumber();
    var c = f.add(24).readU64().toNumber();
    if (n < 0 || n > 4096 || c < n || c > 1048576) return null;
    if (n === 0) return '';
    var d = (c < 16) ? f.readByteArray(n) : f.readPointer().readByteArray(n);
    if (!d) return null;
    var u = new Uint8Array(d), s = '';
    for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
    return s;
  } catch (e) { return null; }
}
function hex(s) { return s && /^[0-9a-fA-F]{32}$/.test(s); }

// 1) find a ready context -> (tk, file)
var found = null;
(function () {
  var hexpt = lo.add(0x802000).toString().slice(2); while (hexpt.length < 16) hexpt = '0' + hexpt;
  var b = []; for (var i = 0; i < 8; i++) b.push(hexpt.slice(14 - i * 2, 16 - i * 2));
  var pat = ['??', '??', b[2], b[3], b[4], b[5], b[6], b[7]].join(' ');
  var cands = [];
  Process.enumerateRanges('rw-').forEach(function (r) {
    if (r.size > 0x10000000) return;
    try {
      Memory.scanSync(r.base, r.size, pat).forEach(function (m) {
        var obj = m.address;
        var tok = readStr(obj, 0x288), file = readStr(obj, 0x18);
        if (!hex(tok) || !file || file.slice(-3) !== '.ts') return;
        var sch = '';
        try { var u = new Uint8Array(obj.add(0x120).readByteArray(32)); for (var i = 0; i < u.length; i++) sch += ('0' + u[i].toString(16)).slice(-2); } catch (e) {}
        cands.push({ obj: obj.toString(), token: tok, file: file, ready: obj.add(0x264).readU8(), schedule: sch });
      });
    } catch (e) {}
  });
  send({ t: 'ctx', n: cands.length, sample: cands.filter(function (c) { return c.ready; }).slice(0, 2) });
  found = cands.filter(function (c) { return c.ready; })[0] || null;
})();

if (!found) { send({ t: 'noctx' }); }
else {
  send({ t: 'use', token: found.token, file: found.file, schedule: found.schedule });
  var pats = [];
  function toPat(s) { return s.split('').map(function (c) { return ('0' + c.charCodeAt(0).toString(16)).slice(-2); }).join(' '); }
  pats.push(['tk+file', toPat(found.token + found.file)]);
  pats.push(['file+tk', toPat(found.file + found.token)]);
  pats.push(['tk', toPat(found.token)]);
  pats.forEach(function (p) {
    var hits = [];
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x10000000) return;
      try { Memory.scanSync(r.base, r.size, p[1]).forEach(function (m) { hits.push(m.address.toString()); }); } catch (e) {}
    });
    send({ t: 'scan', what: p[0], n: hits.length, addrs: hits.slice(0, 6) });
    hits.slice(0, 4).forEach(function (h) {
      try {
        var a = ptr(h);
        var after = a.add(found.token.length + (p[0] === 'file+tk' ? found.file.length : 0));
        var buf = after.readByteArray(0x200);
        send({ t: 'after', what: p[0], addr: h }, buf);
      } catch (e) {}
    });
  });
}
send({ t: 'done' });
"""


def find_pid():
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            return p.pid
    return None


def main():
    pid = find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching pid=%d" % pid, flush=True)
    session = frida.attach(pid)
    script = session.create_script(JS)
    result = {}

    def on_message(msg, data):
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'ctx':
            print("contexts: %d  ready sample: %s" % (p['n'], json.dumps(p['sample'], ensure_ascii=False)[:300]), flush=True)
        elif t == 'noctx':
            print("no ready context found (open/play a video first)", flush=True)
        elif t == 'use':
            print("using tk=%s file=%s" % (p['token'], p['file']), flush=True)
            result['tk'] = p['token']
            result['file'] = p['file']
            result['schedule'] = p['schedule']
        elif t == 'scan':
            print("search %-8s -> %d hit(s) %s" % (p['what'], p['n'], p['addrs']), flush=True)
        elif t == 'after':
            buf = bytes(data)
            txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in buf)
            print("   after %s @%s:\n      %r" % (p['what'], p['addr'], txt), flush=True)
            # also try the bytes right after the prefix as a salt candidate
            result.setdefault('after', []).append((p['what'], buf))
        elif t == 'done':
            session.detach()

    script.on('message', on_message)
    script.load()
    import time
    time.sleep(25)
    try:
        session.detach()
    except Exception:
        pass

    # verify: does any captured "after" buffer start with a salt that reproduces the key?
    if 'tk' in result and result.get('after'):
        tk, fn = result['tk'], result['file']
        # derive expected key from schedule
        sch = bytes.fromhex(result.get('schedule') or '')
        if len(sch) == 32:
            def XT(x): return ((x << 1) ^ (0x1b if x & 0x80 else 0)) & 0xff
            def Mix(b):
                o = bytearray(16)
                for i in range(0, 16, 4):
                    t = b[i] ^ b[i+1] ^ b[i+2] ^ b[i+3]
                    for j in range(4):
                        o[i+j] = b[i+j] ^ t ^ XT(b[i+j] ^ b[i + ((j+1) % 4)])
                return bytes(o)
            key = bytearray(32); key[0:16] = sch[0:16]; key[16:32] = Mix(sch[16:32])
            key = bytes(key).decode('ascii', 'replace')
            print("\nexpected key: %s" % key)
            for what, buf in result['after']:
                for ln in range(1, 80):
                    cand = buf[:ln]
                    try:
                        cs = cand.decode('ascii')
                    except Exception:
                        continue
                    if hashlib.md5((tk + fn + cs).encode()).hexdigest() == key:
                        print("*** SALT FOUND (%s, len=%d): %r" % (what, ln, cs))
            print("(no salt matched among dumped buffers)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
