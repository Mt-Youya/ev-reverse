#!/usr/bin/env python3
"""Fish decrypted API plaintext out of the AES routine's buffers.

The responses we can read (zip:1) are only ever the catalog and the segment manifests.
The zip:0 ones -- notably getPlayTimeKeySign* (the name says "Key") -- we never see, and
they are the prime suspect for where per-segment keys come from.

So: sample the AES T-table routine (one Interceptor, heavily throttled -- this site runs
once per AES round, so it is extremely hot), and on sampled hits read a window at every
register that points at readable memory, looking for JSON text.

Read-only apart from the single hook.
"""
import frida
import sys
import time

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var SITE = dll.base.add(0x792c78);
var hits = 0, dumps = 0;
var SAMPLE = 400, MAXDUMPS = 30, CAP = 200000;
var REGS = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','r8','r9','r10','r11','r12','r13','r14','r15'];

Interceptor.attach(SITE, {
  onEnter: function () {
    hits++;
    if (hits > CAP || dumps >= MAXDUMPS) return;
    if (hits % SAMPLE !== 0) return;
    dumps++;
    var ctx = this.context;
    var sent = 0;
    for (var i = 0; i < REGS.length && sent < 6; i++) {
      var rn = REGS[i];
      var p = ctx[rn];
      try {
        var b = p.readByteArray(768);
        if (!b) continue;
        var u = new Uint8Array(b), printable = 0;
        for (var k = 0; k < u.length; k++) if (u[k] >= 32 && u[k] < 127) printable++;
        if (printable < 40) continue;          // only bother with mostly-text buffers
        send({ t: 'buf', reg: rn, addr: p.toString() }, b);
        sent++;
      } catch (e) {}
    }
  }
});
send({ t: 'ready' });
"""


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
    seen = set()

    def on_message(msg, data):
        p = msg.get('payload') or {}
        if p.get('t') == 'ready':
            print("[*] fishing for decrypted buffers. 请播放视频。", flush=True)
            return
        if p.get('t') != 'buf' or not data:
            return
        b = bytes(data)
        txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in b)
        # keep only buffers that look like JSON or hold 32-hex runs
        import re
        interesting = ('{' in txt and '"' in txt) or re.search(r'[0-9a-f]{32}', txt)
        if not interesting:
            return
        key = (p['reg'], txt[:60])
        if key in seen:
            return
        seen.add(key)
        print("\n--- %s @%s ---\n%s" % (p['reg'], p['addr'], txt[:700]), flush=True)

    script.on('message', on_message)
    script.load()
    try:
        time.sleep(120)
    except KeyboardInterrupt:
        pass
    try:
        session.detach()
    except Exception:
        pass
    print("done. buffers seen: %d" % len(seen), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
