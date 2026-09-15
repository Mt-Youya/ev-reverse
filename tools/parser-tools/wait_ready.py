#!/usr/bin/env python3
"""Poll playback contexts properly (each poll re-scans) and hunt a full key triple.

For 40+ segments we hold the manifest entry (tk + sf with t/sign/bid/sid) AND a playback
context. The context's schedule field holds the AES key once the player has decrypted that
segment (before that it is the heap fill 0xBAADF00D). A full triple (tk + filename + key +
t/sign) is what every derivation hypothesis needs, so this waits for one to appear.

Read-only: memory scans only, no hooks.
"""
import frida
import os
import sys
import json
import time
import hashlib

here = os.path.dirname(os.path.abspath(__file__))

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var SCAN_PAT = (function () {
  var hp = dll.base.add(0x802000).toString().slice(2); while (hp.length < 16) hp = '0' + hp;
  var b = []; for (var i = 0; i < 8; i++) b.push(hp.slice(14 - i * 2, 16 - i * 2));
  return ['??', '??', b[2], b[3], b[4], b[5], b[6], b[7]].join(' ');
})();

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

rpc.exports = {
  scan: function () {
    var out = [];
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x10000000) return;
      try {
        Memory.scanSync(r.base, r.size, SCAN_PAT).forEach(function (m) {
          var obj = m.address;
          var file = readStr(obj, 0x18);
          if (!file || file.slice(-3) !== '.ts') return;
          var sch = '';
          try { var u = new Uint8Array(obj.add(0x120).readByteArray(32)); for (var i = 0; i < u.length; i++) sch += ('0' + u[i].toString(16)).slice(-2); } catch (e) {}
          out.push({ file: file, token: readStr(obj, 0x288), ready: obj.add(0x264).readU8(), schedule: sch });
        });
      } catch (e) {}
    });
    return out;
  }
};
"""


def XT(x): return ((x << 1) ^ (0x1b if x & 0x80 else 0)) & 0xff
def Mix(b):
    o = bytearray(16)
    for i in range(0, 16, 4):
        t = b[i] ^ b[i+1] ^ b[i+2] ^ b[i+3]
        for j in range(4):
            o[i+j] = b[i+j] ^ t ^ XT(b[i+j] ^ b[i + ((j+1) % 4)])
    return bytes(o)
def key_from_schedule(h):
    if not h or len(h) != 64:
        return None
    s = bytes.fromhex(h)
    if s == b'\x00' * 32 or s[:8] == bytes.fromhex('0df0adba0df0adba'):
        return None
    k = bytearray(32); k[0:16] = s[0:16]; k[16:32] = Mix(s[16:32])
    return bytes(k)


def load_pairs():
    raw = open(os.path.join(here, 'captured', 'zlib.bin'), 'rb').read()
    parts = raw.split(b'\n===== ')
    buf = bytearray()
    for part in parts[1:]:
        nl = part.find(b' =====\n')
        if nl != -1 and part[:nl].startswith(b'Z-INFLATE'):
            buf += part[nl + len(b' =====\n'):]
    txt = buf.decode('utf-8', 'replace')
    out, i, n = [], 0, len(txt)
    while i < n:
        if txt[i] == '{':
            d = 0; j = i; s = False; e = False
            while j < n:
                ch = txt[j]
                if s:
                    if e: e = False
                    elif ch == chr(92): e = True
                    elif ch == '"': s = False
                else:
                    if ch == '"': s = True
                    elif ch in '{[': d += 1
                    elif ch in '}]':
                        d -= 1
                        if d == 0: break
                j += 1
            if d == 0 and j - i > 20:
                try:
                    o = json.loads(txt[i:j+1])
                    if isinstance(o, dict) and isinstance(o.get('k_l'), list) and o.get('d_p'):
                        out.append(o)
                except Exception:
                    pass
                i = j + 1
                continue
        i += 1
    pairs = {}
    for m in out:
        for e in m['k_l']:
            fn = e['sf'].split('?')[0].lstrip('/')
            pairs[fn] = (m['d_p'].rstrip('/'), e['sf'], e['tk'])
    return pairs


def main():
    pairs = load_pairs()
    man_tokens = set(v[2] for v in pairs.values())
    print("manifest pairs: %d (unique tk: %d)" % (len(pairs), len(man_tokens)), flush=True)

    pid = None
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            pid = p.pid
    if pid is None:
        print("EVPlayer2.exe is not running."); return 1
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on('message', lambda m, d: None)
    script.load()
    ex = script.exports_sync

    reported = set()
    for it in range(60):
        try:
            pairs = load_pairs()          # re-read: the capture may still be growing
            man_tokens = set(v[2] for v in pairs.values())
        except Exception:
            pass
        ctxs = ex.scan()
        ready = [c for c in ctxs if key_from_schedule(c.get('schedule'))]
        ready_in_man = [c for c in ready if c['file'] in pairs]
        ready_tk_in_man = [c for c in ready if c['token'] in man_tokens]
        ctx_in_man = [c for c in ctxs if c['file'] in pairs]
        print("[%02d] ctx=%d  ctx∩manifest=%d  ready=%d  ready∩manifest(file)=%d  ready∩manifest(tk)=%d"
              % (it, len(ctxs), len(ctx_in_man), len(ready), len(ready_in_man), len(ready_tk_in_man)), flush=True)
        if ready and it % 3 == 0:
            print("      sample ready files: %s" % [c['file'][:26] for c in ready[:3]], flush=True)
        if ready_in_man or ready_tk_in_man:
            for c in (ready_in_man + ready_tk_in_man):
                if c['file'] in reported:
                    continue
                reported.add(c['file'])
                key = key_from_schedule(c['schedule']).decode('ascii')
                ent = pairs.get(c['file'])
                print("\n*** FULL TRIPLE: %s" % c['file'], flush=True)
                print("   context tk=%s key=%s" % (c['token'], key), flush=True)
                if ent:
                    print("   manifest sf=%s" % ent[1], flush=True)
                    print("   manifest tk=%s" % ent[2], flush=True)
                json.dump({"file": c['file'], "tk": c['token'], "key": key,
                           "sf": ent[1] if ent else None}, open(os.path.join(here, 'triple.json'), 'w'))
        time.sleep(10)
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
