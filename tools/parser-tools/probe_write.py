#!/usr/bin/env python3
"""Catch the instruction that stores a freshly derived segment key into a playback context.

Ticket 07. The 32 bytes at `ctx+0x120` are the key once the player has decrypted that segment.
Whatever writes them is on the path to the derivation, so catching the writer's instruction
pointer and its backtrace is the point.

`watch_key.py` asks the same question, but it watches up to 96 pages, re-arms the monitor every
1.2 s, and attaches an Interceptor from inside the access callback. On a live player that is
enough to hang it, which is why it is marked "do not run this one directly". This watches ONE
page -- the page holding the key field of a single context the player has not decrypted yet --
and stops as soon as the write lands.

Frida 17.18.0 has no hardware watchpoints: `typeof Thread.setHardwareWatchpoint` is undefined
(checked against a live process). So `MemoryAccessMonitor`, which works with page guards, is the
only mechanism available, and the ticket's fallback -- a per-thread breakpoint that may miss the
writer -- is not a route that exists here. If the writer is missed, that is a result to name, not
an avenue to try.

Usage:  <python> -u probe_write.py [seconds]      (default 240)

Preconditions, all of them load-bearing:
  * EVPlayer2 running, logged in, a lesson open.
  * The lesson must still have segments the player has NOT decrypted. The writer only runs while
    the player is decrypting something for the first time, so a lesson already played to the end
    on this machine will produce nothing. Fastest way to satisfy this: open a lesson that has not
    been opened before, or jump the playhead somewhere it has not been.
  * Keep it playing. Do not pause.

What the output means:
  "empty context"     -- the slot was still the heap fill, so this is a real target
  "armed ..."         -- the page guard is on
  "WRITE to ctx+0x120 by <addr>"  -- the answer to ticket 07; read the module offset
  "the writer, with context"      -- the instruction ran again and its registers and stack follow

What is verified and what is not, so the next person does not have to guess:

  Verified. The script's JavaScript parses and loads (checked by loading it into a throwaway
  process; the only error is the expected missing-DLL one). A write from the agent's own thread
  into a guarded page raises `guard page was hit` rather than invoking the access callback --
  checked against a live process -- which is why the field is read only after the monitor is
  disabled.

  Not verified. Whether a write from the *player's own* threads reaches this callback at all.
  It could not be tested without the player: frida's JavaScript runs on its own thread inside the
  target, so every write a test can perform is an agent-thread write, which is the case that
  raises instead. If the callback never fires while a lesson is playing, that is the first thing
  to suspect, and the fallback is a guard on a narrower window or an `Interceptor` on the
  suspected store, not a bigger monitor.
"""
import frida
import sys
import time

JS = r"""
'use strict';
var DLL = 'PlayerLibRender56_vs.dll';
var dll = Process.getModuleByName(DLL);

function modOff(a) {
  try {
    var mo = Process.findModuleByAddress(a);
    if (!mo) return a.toString() + ' <heap-code>';
    return mo.name + '+0x' + a.sub(mo.base).toString(16);
  } catch (e) { return a.toString(); }
}
function bytesToStr(buf) {
  var u = new Uint8Array(buf); var s = '';
  for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
  return s;
}
function stringsIn(ptrAddr, len) {
  var out = [];
  try {
    var u = new Uint8Array(ptrAddr.readByteArray(len));
    var cur = '';
    for (var i = 0; i < u.length; i++) {
      var c = u[i];
      if (c >= 32 && c < 127) cur += String.fromCharCode(c);
      else { if (cur.length >= 5) out.push(cur); cur = ''; }
    }
    if (cur.length >= 5) out.push(cur);
  } catch (e) {}
  return out;
}
function readStr(obj, off) {
  try {
    var f = obj.add(off);
    var n = f.add(16).readU64().toNumber();
    var c = f.add(24).readU64().toNumber();
    if (n < 0 || n > 8192 || c < n || c > 1048576) return null;
    if (n === 0) return '';
    var data = (c < 16) ? f.readByteArray(n) : f.readPointer().readByteArray(n);
    return data ? bytesToStr(data) : null;
  } catch (e) { return null; }
}

// The playback contexts: objects whose vtable sits in the DLL's vtable range, carrying a
// 32-character mask at +0x268 and a .ts name at +0x18. This scan is the expensive part and it
// runs exactly once, before any hook is armed.
var EMPTY = 0xBAADF00D;   // the heap fill that sits at +0x120 until a key is derived
function findContexts() {
  var lo = dll.base.add(0x802000), hi = dll.base.add(0x804000);
  var hex = lo.toString().slice(2); while (hex.length < 16) hex = '0' + hex;
  var b = []; for (var i = 0; i < 8; i++) b.push(hex.slice(14 - i * 2, 16 - i * 2));
  var pat = ['??', '??', b[2], b[3], b[4], b[5], b[6], b[7]].join(' ');
  var objs = [];
  Process.enumerateRanges('rw-').forEach(function (r) {
    if (r.size > 0x10000000) return;
    try {
      Memory.scanSync(r.base, r.size, pat).forEach(function (m) {
        var obj = m.address;
        var mask = readStr(obj, 0x268);
        if (!mask || mask.length !== 32) return;
        var file = readStr(obj, 0x18);
        if (!file || file.slice(-3) !== '.ts') return;
        var ready = obj.add(0x264).readU8();
        var fill = obj.add(0x120).readU32();
        objs.push({ obj: obj, file: file, ready: ready, empty: fill === EMPTY });
      });
    } catch (e) {}
  });
  return objs;
}

var target = null;
var armed = false;
var reported = false;
var instrHooked = false;

function arm() {
  var objs = findContexts();
  send({t: 'log', msg: 'found ' + objs.length + ' context(s), ' +
        objs.filter(function (o) { return o.empty; }).length + ' with an empty key field'});

  // One context that has not been decrypted. That is the only kind whose key field is still
  // fill, and therefore the only kind a writer can still be caught on.
  var candidates = objs.filter(function (o) { return o.empty && !o.ready; });
  if (!candidates.length) candidates = objs.filter(function (o) { return o.empty; });
  if (!candidates.length) {
    send({t: 'nodice',
          msg: 'no context with an empty key field. The writer only runs for a segment the ' +
               'player has not decrypted yet -- open a lesson that has not been played on this ' +
               'machine, or jump the playhead past where it has been.'});
    return false;
  }
  target = candidates[0];
  send({t: 'log', msg: 'empty context: ' + target.file + ' at ' + target.obj +
        ' (ready=' + target.ready + ')'});

  var field = target.obj.add(0x120);
  var page = field.and(ptr('0xfffffffffffff000'));
  try {
    MemoryAccessMonitor.enable([{ base: page, size: 0x1000 }], {
      onAccess: function (d) {
        if (reported) return;
        if (d.operation !== 'write') return;
        if (d.address.compare(field) < 0 || d.address.compare(field.add(32)) >= 0) return;
        reported = true;
        // Release the guard BEFORE touching the page. A read from the agent's own thread into a
        // guarded page raises `guard page was hit` instead of calling back here -- measured, not
        // assumed -- so the value has to be read with the monitor off. Releasing here also gives
        // the player back its page: one page was guarded, the answer is in hand, and leaving it
        // faulting on memory it reads constantly is what makes the heavy version hang it.
        try { MemoryAccessMonitor.disable(); } catch (e) {}
        var value = null;
        try { value = bytesToStr(field.readByteArray(32)); } catch (e) { value = '<unreadable: ' + e + '>'; }
        send({t: 'write', by: modOff(d.from), at: modOff(field), from: d.from.toString(),
              value: value});
        // The access callback has the address but no thread context, so the registers and the
        // backtrace have to come from the instruction itself. Attaching once, after the fact,
        // catches the next store by the same instruction -- which is why it is guarded.
        if (!instrHooked) {
          instrHooked = true;
          try {
            Interceptor.attach(d.from, {
              onEnter: function () {
                send({t: 'writer', at: modOff(d.from),
                      regs: ['rcx','rdx','r8','r9'].reduce(function (acc, rn) {
                        try { acc[rn] = this.context[rn].toString(); } catch (e) {}
                        return acc;
                      }, {}),
                      stack: stringsIn(this.context.rsp, 0x600),
                      bt: Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOff)});
              }
            });
            send({t: 'log', msg: 'the writer instruction is now hooked; it will dump its ' +
                  'registers on its next run. Keep it playing.'});
          } catch (e) { send({t: 'err', where: 'hook-writer', e: String(e)}); }
        }
      }
    });
    armed = true;
    send({t: 'log', msg: 'armed on ' + page + ' (' + target.file + ', field ' + modOff(field) + ')'});
    return true;
  } catch (e) {
    send({t: 'err', where: 'arm', e: String(e)});
    return false;
  }
}

var tries = 0;
var poll = setInterval(function () {
  tries++;
  if (armed && reported) { clearInterval(poll); return; }
  if (!armed) {
    try { arm(); } catch (e) { send({t: 'err', where: 'arm-outer', e: String(e)}); }
  }
  if (tries > 240) clearInterval(poll);
}, 1500);

send({t: 'ready'});
"""


def find_pid():
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == 'evplayer2.exe':
            return p.pid
    return None


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    pid = find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print("attaching to EVPlayer2.exe pid=%d for %ds" % (pid, secs), flush=True)

    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(msg, data):
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'log':
            print("[*] " + p['msg'], flush=True)
        elif t == 'write':
            print("\n=== WRITE to the key field of a context ===", flush=True)
            print("   instruction : %s" % p['by'], flush=True)
            print("   field       : %s" % p['at'], flush=True)
            print("   value       : %s" % repr(p['value']), flush=True)
        elif t == 'writer':
            print("\n=== the writer, with context ===", flush=True)
            print("   at    : %s" % p['at'], flush=True)
            for rn, v in (p.get('regs') or {}).items():
                print("   %s  : %s" % (rn, v), flush=True)
            if p.get('stack'):
                print("   stack : %s" % p['stack'][:25], flush=True)
            for i, fr in enumerate(p.get('bt') or []):
                print("   #%d %s" % (i, fr), flush=True)
        elif t == 'nodice':
            print("\n[!] %s" % p['msg'], flush=True)
        elif t == 'err':
            print("[err] %s: %s" % (p.get('where'), p.get('e')), flush=True)
        elif t == 'ready':
            print("[*] looking for a playback context with an empty key field.", flush=True)
            print("[*] 请打开一个没播过的课时并保持播放。", flush=True)

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
