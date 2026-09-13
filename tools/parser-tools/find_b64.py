#!/usr/bin/env python3
"""Locate the base64 decoder by watching the alphabet tables.

Every response's "result" is base64 (we already hold the ciphertext). The decoded buffer is
very likely where the AES then writes its plaintext in place, so finding the decoder and its
output buffer is the most direct route to the decrypted zip:0 responses
(getDownEVSKey / getEvsSignUrl / getPlayAuthorityEVS / getPlaySubtitle), which are the only
un-read responses and the prime suspect for where per-segment keys come from.

Technique: MemoryAccessMonitor on the page holding each base64 alphabet table; the first
reader instruction inside the module is (part of) the decoder. Light: a few pages, one shot
each, no hooks beyond the monitor.
"""
import frida
import struct
import sys
import time

DLL = r"D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll"
ALPHA = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


def file_off_to_rva(path, off):
    d = open(path, 'rb').read()
    e = struct.unpack_from('<I', d, 0x3c)[0]
    coff = e + 4
    nsects = struct.unpack_from('<H', d, coff + 2)[0]
    optsz = struct.unpack_from('<H', d, coff + 16)[0]
    opt = coff + 20
    sec = opt + optsz
    for i in range(nsects):
        o = sec + i * 40
        va = struct.unpack_from('<I', d, o + 12)[0]
        rsz = struct.unpack_from('<I', d, o + 16)[0]
        raw = struct.unpack_from('<I', d, o + 20)[0]
        if raw <= off < raw + rsz:
            return va + (off - raw)
    return None


d = open(DLL, 'rb').read()
offs, i = [], d.find(ALPHA)
while i != -1:
    offs.append(i)
    i = d.find(ALPHA, i + 1)
rvas = [file_off_to_rva(DLL, o) for o in offs]
print("base64 alphabet tables: file offsets %s -> RVAs %s" %
      ([hex(o) for o in offs], [hex(r) if r else None for r in rvas]), flush=True)
pages = sorted(set(r & ~0xfff for r in rvas if r))
print("pages to watch: %s" % [hex(p) for p in pages], flush=True)

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var RVAS = __RVAS__;
function modOff(a) {
  try { var m = Process.findModuleByAddress(a); return m ? (m.name + '+0x' + a.sub(m.base).toString(16)) : a.toString(); }
  catch (e) { return a.toString(); }
}
var seen = {};
RVAS.forEach(function (rva) {
  var va = dll.base.add(rva);
  var page = va.and(ptr('0xfffffffffffff000'));
  try {
    MemoryAccessMonitor.enable([{ base: page, size: 0x1000 }], {
      onAccess: function (d) {
        var k = d.from.toString() + d.operation;
        if (seen[k]) return;
        seen[k] = 1;
        send({ t: 'acc', rva: rva.toString(), op: d.operation, from: modOff(d.from),
               addr: d.address.toString() });
      }
    });
    send({ t: 'armed', rva: rva.toString(), page: page.toString() });
  } catch (e) { send({ t: 'err', e: String(e) }); }
});
send({ t: 'ready' });
""".replace('__RVAS__', '[' + ', '.join(hex(r) for r in rvas if r) + ']')


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

    def on_message(msg, data):
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'armed':
            print("[*] watching page of rva %s" % p['rva'], flush=True)
        elif t == 'acc':
            print("[acc] rva=%s op=%s from=%s addr=%s" % (p['rva'], p['op'], p['from'], p['addr']), flush=True)
        elif t == 'err':
            print("[err] %s" % p['e'], flush=True)
        elif t == 'ready':
            print("[*] ready. 请在播放器里触发一次接口请求（打开课程/视频）。", flush=True)

    script.on('message', on_message)
    script.load()
    try:
        time.sleep(150)
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
