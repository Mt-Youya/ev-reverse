#!/usr/bin/env python3
"""Compare the manifest's tk with the playback-context's tk for the SAME segment.

If they differ, the "tk" in the manifest is not what the player stores/uses, and every
derivation hypothesis built on it was tested against the wrong input.

Read-only: one memory scan via frida, plus parsing the capture. No hooks.
"""
import frida
import os
import sys
import json
import time

here = os.path.dirname(os.path.abspath(__file__))

JS = r"""
'use strict';
setImmediate(function () {
  var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
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
  var hexpt = dll.base.add(0x802000).toString().slice(2); while (hexpt.length < 16) hexpt = '0' + hexpt;
  var b = []; for (var i = 0; i < 8; i++) b.push(hexpt.slice(14 - i * 2, 16 - i * 2));
  var pat = ['??', '??', b[2], b[3], b[4], b[5], b[6], b[7]].join(' ');
  var out = [];
  Process.enumerateRanges('rw-').forEach(function (r) {
    if (r.size > 0x10000000) return;
    try {
      Memory.scanSync(r.base, r.size, pat).forEach(function (m) {
        var obj = m.address;
        var file = readStr(obj, 0x18);
        if (!file || file.slice(-3) !== '.ts') return;
        var sch = '';
        try { var u = new Uint8Array(obj.add(0x120).readByteArray(32)); for (var i = 0; i < u.length; i++) sch += ('0' + u[i].toString(16)).slice(-2); } catch (e) {}
        out.push({ file: file, token: readStr(obj, 0x288), mask: readStr(obj, 0x268),
                   ready: obj.add(0x264).readU8(), schedule: sch });
      });
    } catch (e) {}
  });
  send({ t: 'ctx', list: out });
  send({ t: 'done' });
});
"""


def extract_manifests(path):
    raw = open(path, 'rb').read()
    parts = raw.split(b'\n===== ')
    buf = bytearray()
    for part in parts[1:]:
        nl = part.find(b' =====\n')
        if nl != -1 and part[:nl].startswith(b'Z-INFLATE'):
            buf += part[nl + len(b' =====\n'):]
    txt = buf.decode('utf-8', 'replace')
    out, i, n = [], 0, len(txt)
    while i < n:
        if txt[i] in '{[':
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
    return out


def main():
    pid = None
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            pid = p.pid
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching pid=%d" % pid, flush=True)
    session = frida.attach(pid)
    script = session.create_script(JS)
    ctxs = []
    done = {"v": False}

    def on_message(msg, data):
        p = msg.get('payload') or {}
        if p.get('t') == 'ctx':
            ctxs.extend(p['list'])
        elif p.get('t') == 'done':
            done['v'] = True

    script.on('message', on_message)
    script.load()
    for _ in range(60):
        if done['v']:
            break
        time.sleep(0.5)
    try:
        session.detach()
    except Exception:
        pass
    print("contexts: %d (ready: %d)" % (len(ctxs), sum(1 for c in ctxs if c['ready'])), flush=True)

    mans = extract_manifests(os.path.join(here, 'captured', 'zlib.bin'))
    print("manifests in capture: %d" % len(mans), flush=True)

    ctx_by_file = {c['file']: c for c in ctxs}
    man_by_file = {}
    for m in mans:
        for e in m['k_l']:
            fn = e['sf'].split('?')[0].lstrip('/')
            man_by_file[fn] = (e.get('tk'), e.get('sf'), m['d_p'])

    common = [f for f in man_by_file if f in ctx_by_file]
    print("segments present in BOTH capture and contexts: %d" % len(common), flush=True)
    same = diff = 0
    for f in common[:20]:
        mt = man_by_file[f][0]
        ct = ctx_by_file[f]['token']
        eq = (mt == ct)
        same += eq; diff += (not eq)
        print("  %s\n     manifest tk=%s\n     context  tk=%s   ready=%s  %s" %
              (f, mt, ct, ctx_by_file[f]['ready'], 'SAME' if eq else '*** DIFFERENT ***'), flush=True)
    if common:
        print("\nsummary over %d: same=%d different=%d" % (len(common), same, diff), flush=True)
    else:
        print("\nno overlap: the capture has no manifest for the currently loaded video.", flush=True)
        print("captured files (first 5): %s" % list(man_by_file)[:5], flush=True)
        print("context files (first 5): %s" % [c['file'] for c in ctxs[:5]], flush=True)
    json.dump(ctxs, open(os.path.join(here, 'ctx_dump.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
