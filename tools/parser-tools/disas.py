#!/usr/bin/env python3
"""Disassemble a range of a loaded module in the live EVPlayer2 process.

Usage:  <python> -u disas.py <module> <rva_hex> [count] [back_bytes]

Example: python -u disas.py PlayerLibRender56_vs.dll 0xb2ef68 60 0x120
Prints the instructions so a CALL site and its enclosing function can be identified.
Read-only: uses Instruction.parse, installs no hooks.
"""
import frida
import sys
import time

JS = r"""
'use strict';
var modName = '__MOD__';
var rva = __RVA__;
var count = __COUNT__;
var back = __BACK__;
var m = Process.getModuleByName(modName);
var start = m.base.add(rva).sub(back);
var p = start;
var base = m.base;
for (var i = 0; i < count; i++) {
  var ins;
  try { ins = Instruction.parse(p); } catch (e) { send({t:'line', s: p.sub(base).toString() + '  <stop>'}); break; }
  var bytes = '';
  try { var u = new Uint8Array(p.readByteArray(ins.size)); for (var k=0;k<u.length;k++) bytes += ('0'+u[k].toString(16)).slice(-2); } catch (e) {}
  var mark = (p.sub(base).toString() === m.base.add(rva).sub(base).toString()) ? '  <== HERE' : '';
  send({t:'line', s: p.sub(base).toString() + '  ' + bytes + '  ' + ins.mnemonic + ' ' + ins.opStr + mark});
  p = p.add(ins.size);
}
send({t:'done'});
"""


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    mod = sys.argv[1]
    rva = int(sys.argv[2], 16) if sys.argv[2].startswith('0x') else int(sys.argv[2])
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    back = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0xC0

    pid = None
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            pid = p.pid
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1

    js = (JS.replace('__MOD__', mod).replace('__RVA__', hex(rva))
            .replace('__COUNT__', str(count)).replace('__BACK__', str(back)))
    session = frida.attach(pid)
    script = session.create_script(js)

    def on_message(msg, data):
        p = msg.get('payload') or {}
        if p.get('t') == 'line':
            print(p['s'], flush=True)
        elif p.get('t') == 'done':
            session.detach()

    script.on('message', on_message)
    script.load()
    time.sleep(4)
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
