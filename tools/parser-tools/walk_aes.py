#!/usr/bin/env python3
"""Light-weight probe: walk upward from EVPlayer2's AES routine.

Only ONE code breakpoint is installed (PlayerLibRender56_vs.dll + 0x792c78, the T-table
decrypt loop that reads the inverse S-box). That site fires once per AES round, so it is
extremely hot: the hook samples heavily and stops entirely after a hard cap, and no memory
scanning or page monitoring is done at all (that is what froze the player last time).

For each distinct caller of the AES site we attach once and dump, on enter and on leave:
registers, printable strings they point at, the stack, and the call chain. The caller lives
in dynamically allocated executable memory (no module), which is why static analysis of the
DLL never found the key derivation.

Usage:  <python> -u walk_aes.py [seconds]      (default 90)
"""
import frida
import sys
import time

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var SITE = dll.base.add(0x792c78);

var siteHits = 0;
var SAMPLE_EVERY = 200;      // sample instead of inspecting every round
var CAP = 4000;              // stop inspecting entirely after this
var callers = {};            // address -> attached
var callerCount = 0;
var MAX_CALLERS = 12;

function modOff(a) {
  try {
    var mo = Process.findModuleByAddress(a);
    if (!mo) return a.toString() + ' <heap-code>';
    return mo.name + '+0x' + a.sub(mo.base).toString(16);
  } catch (e) { return a.toString(); }
}
function printable(p, len) {
  try {
    var s = p.readCString(len);
    if (!s || s.length < 4) return null;
    var ok = 0;
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 32 && c < 127) ok++; }
    return (ok / s.length > 0.85) ? s : null;
  } catch (e) { return null; }
}
function stackStrings(sp, len) {
  var out = [];
  try {
    var u = new Uint8Array(sp.readByteArray(len));
    var cur = '';
    for (var i = 0; i < u.length; i++) {
      var c = u[i];
      if (c >= 32 && c < 127) cur += String.fromCharCode(c);
      else { if (cur.length >= 6) out.push(cur); cur = ''; }
    }
    if (cur.length >= 6) out.push(cur);
  } catch (e) {}
  return out;
}
var REGS = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','r8','r9','r10','r11','r12','r13','r14','r15'];
function snap(ctx) {
  var strs = {}, peek = {};
  REGS.forEach(function (rn) {
    var v = ctx[rn];
    var s = printable(v, 120);
    if (s) strs[rn] = s;
    try {
      var u = new Uint8Array(v.readByteArray(32));
      var t = '';
      for (var i = 0; i < u.length; i++) t += (u[i] >= 32 && u[i] < 127) ? String.fromCharCode(u[i]) : '.';
      peek[rn] = t;
    } catch (e) {}
  });
  return { strs: strs, peek: peek };
}

function inspectCaller(addr) {
  var id = addr.toString();
  if (callers[id] || callerCount >= MAX_CALLERS) return;
  callers[id] = 1;
  callerCount++;
  send({t: 'log', msg: 'caller #' + callerCount + ' -> ' + modOff(addr)});
  try {
    Interceptor.attach(addr, {
      onEnter: function () {
        this.s = snap(this.context);
        this.stack = stackStrings(this.context.rsp, 0x300);
      },
      onLeave: function () {
        var after = snap(this.context);
        send({t: 'caller', at: modOff(addr),
              enter: this.s, leave: { strs: after.strs },
              stack: this.stack,
              bt: Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOff)});
      }
    });
  } catch (e) { send({t: 'err', where: 'attach', e: String(e)}); }
}

Interceptor.attach(SITE, {
  onEnter: function () {
    siteHits++;
    if (siteHits > CAP) return;
    if (siteHits % SAMPLE_EVERY !== 0) return;
    try {
      var bt = Thread.backtrace(this.context, Backtracer.ACCURATE);
      for (var i = 0; i < bt.length && i < 2; i++) inspectCaller(bt[i]);
      if (siteHits === SAMPLE_EVERY) {
        send({t: 'first', bt: bt.map(modOff), stack: stackStrings(this.context.rsp, 0x500)});
      }
    } catch (e) {}
  }
});
send({t: 'ready', site: SITE.toString()});
"""


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    pid = None
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            pid = p.pid
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching to EVPlayer2.exe pid=%d for %ds (light mode)" % (pid, secs), flush=True)

    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(msg, data):
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'log':
            print("[*] " + p['msg'], flush=True)
        elif t == 'first':
            print("\n=== first AES call seen ===", flush=True)
            for i, fr in enumerate(p['bt']):
                print("   #%d %s" % (i, fr), flush=True)
            if p.get('stack'):
                print("   stack: %s" % p['stack'][:20], flush=True)
        elif t == 'caller':
            print("\n--- caller %s ---" % p['at'], flush=True)
            for rn, sv in (p.get('enter', {}).get('strs') or {}).items():
                print("   ENTER %s str -> %r" % (rn, sv), flush=True)
            for rn, bv in (p.get('enter', {}).get('peek') or {}).items():
                if bv and bv.strip('.') and len(bv.strip('.')) >= 5:
                    print("   ENTER %s mem -> %r" % (rn, bv), flush=True)
            for rn, sv in (p.get('leave', {}).get('strs') or {}).items():
                print("   LEAVE %s str -> %r" % (rn, sv), flush=True)
            if p.get('stack'):
                print("   stack strings: %s" % p['stack'][:20], flush=True)
            print("   chain: %s" % (p.get('bt') or [])[:5], flush=True)
        elif t == 'err':
            print("[err] %s: %s" % (p.get('where'), p.get('e')), flush=True)
        elif t == 'ready':
            print("[*] light hook installed. 请播放视频。", flush=True)

    script.on('message', on_message)
    script.load()
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
