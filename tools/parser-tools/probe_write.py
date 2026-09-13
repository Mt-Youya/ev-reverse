#!/usr/bin/env python3
"""Catch the instruction that stores a freshly derived segment key into a playback context.

Ticket 07. The 32 bytes at `ctx+0x120` are the key once the player has decrypted that segment.
Whatever writes them is on the path to the derivation, so catching the writer's instruction pointer
and its backtrace is the point.

Usage:  <python> -u probe_write.py [seconds]      (default 240)

Preconditions, all of them load-bearing:
  * EVPlayer2 running, logged in, a lesson open.
  * That lesson must still have segments the player has NOT decrypted. The writer only runs while
    the player is decrypting something for the first time, so a lesson already played to the end on
    this machine produces nothing. Open a lesson never opened here.
  * Keep it playing. Do not pause.

What the output means:
  "watching N page(s)"            -- the guards are on and being re-armed
  "access ... field=<file>"       -- an access landed inside a watched key field; if op=write, that
                                     is the answer to ticket 07 and the instruction is named
  "the writer, with context"      -- that instruction ran again; registers, stack and backtrace follow
  "no context with an empty key field"  -- nothing left to catch on this machine

--------------------------------------------------------------------------------------------------
How this was built, and what was measured rather than assumed

The first version of this script armed a page guard and caught nothing in two 240-second runs. The
mechanism was then tested against target processes written for the purpose, and the results are why
this version looks the way it does:

  1. **A page guard fires once.** It is consumed by the first access, read or write, and frida does
     NOT restore it. Measured: 27 target memory operations, 1 report. The first version never
     re-armed, so the player reading the page -- which it does constantly, it reads these context
     objects -- silenced the watch on the first read and the run reported nothing for the remaining
     240 seconds. That was the bug, and it was not "the player never wrote".
  2. **Re-arming from inside the access callback wedges the target.** Measured: 200 reports and the
     target performed zero operations for 8 seconds. Re-arming must happen from a timer.
  3. **Re-arming from a timer is free.** A target hammering guarded pages in a tight loop kept
     100.4% of its unguarded throughput at 64 and 128 pages with a 100 ms re-arm. Page count is not
     the hazard it was assumed to be, so this watches up to `MAX_PAGES` contexts rather than one.
  4. **The reported address is the faulting byte, not the start of the write.** Measured: a 32-byte
     store at page+0x120 reported as page+0x130. A filter that requires the address to equal the
     field's first byte throws the answer away, so this reports every access to a watched page and
     says which field, if any, the offset lands in.
  5. **There are no hardware watchpoints to fall back on.** `Thread` on frida 17.18.0 offers only
     `backtrace`; `Thread.setHardwareWatchpoint` and `Thread.setHardwareBreakpoint` are both
     `undefined`. The ticket's own fallback, "a per-thread breakpoint across sixteen threads", is a
     route that does not exist on this build.
  6. **A guard faults BEFORE the store executes.** The callback runs, and only when it returns does
     the instruction retry and complete, so reading the field inside the callback always returns the
     old contents. The value is therefore read on a later turn, and the first version's attempt to
     report it inline would have reported zeros.
  7. **`Thread.backtrace(..., Backtracer.ACCURATE)` raises `invalid operation`** where unwind data is
     missing or unusable -- it did on a plain Rust target, and this player is UPX-packed Qt, so
     expect it there too. FUZZY returned an empty list on the same target. A scan of the stack for
     words that point into a loaded module produced a real call chain where both failed, so that is
     what is printed when the unwind-based backtrace comes back empty.
  8. **`NativePointer` has no `toNumber()`** -- only `UInt64` does. The first version called it on
     the result of `.and()`, which raised inside the callback once per access: armed, apparently
     healthy, reporting nothing. `parseInt(....toString(), 16)` is the fix.

`watch_key.py`, the instrument this replaces, is not slow because of its 96 pages -- 128 guarded
pages at a 100 ms re-arm measured free. It hangs the player because it also hooks the AES site and
takes an ACCURATE backtrace on every hit, and that site fires once per AES round. This script does
not hook the AES site at all.

This version was run end to end against a stand-in target process: six reads of the guarded page
(the sequence that silenced the first version), then a 32-byte store into the watched field. It
survived the reads, caught the store, named the instruction, dumped the registers, read the value
`3df51fd02753d5605cc3139e66a3561e` back correctly, and produced a call chain from the stack after
both backtracers came back empty. It has still not been run against EVPlayer2 itself.
"""
import frida
import sys
import time

JS = r"""
'use strict';
var DLL = 'PlayerLibRender56_vs.dll';
var dll = Process.getModuleByName(DLL);
var EMPTY = 0xBAADF00D;      // what sits at +0x120 until a key is derived
var KEY_FIELD = 0x120;
var MAX_PAGES = 24;          // measured free up to 128; 24 keeps the log readable
var TICK_MS = 100;           // re-arm interval; the guard is consumed by the first access
var REPORT_CAP = 600;
var INDIVIDUAL_CAP = 40;     // after this many, only accesses that land on a field are printed

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

// A backtrace that does not need unwind data: walk the stack for words that point into a loaded
// module. `Thread.backtrace(..., Backtracer.ACCURATE)` raises `invalid operation` where the unwind
// tables are missing or unusable -- measured on a plain Rust target, and this player is UPX-packed
// Qt, so expect it there too. This always answers, at the cost of false positives.
function stackReturnAddrs(sp, len) {
  var out = [];
  for (var off = 0; off < len; off += 8) {
    try {
      var p = sp.add(off).readPointer();
      if (p.isNull()) continue;
      var mo = Process.findModuleByAddress(p);
      if (mo) out.push('[rsp+' + off.toString(16) + '] ' + mo.name + '+0x' + p.sub(mo.base).toString(16));
    } catch (e) {}
  }
  return out;
}

// The playback contexts: objects whose vtable sits in the DLL's vtable range, carrying a
// 32-character mask at +0x268 and a .ts name at +0x18. One-off, done before any guard is armed --
// the scan reads these pages, and reading a guarded page raises.
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
        var index = obj.add(8).readU32();
        if (index > 20_000_000) return;
        objs.push({ obj: obj, file: file, index: index,
                    empty: obj.add(KEY_FIELD).readU32() === EMPTY });
      });
    } catch (e) {}
  });
  return objs;
}

var watched = [];     // { page, fieldOff, file, index, ctx }
var ranges = [];
var reports = 0;
var found = false;
var timer = null;
var instrHooked = false;
var ticks = 0;

function chooseTargets() {
  var objs = findContexts();
  var empty = objs.filter(function (o) { return o.empty; });
  send({t: 'log', msg: 'found ' + objs.length + ' context(s), ' + empty.length +
        ' with an empty key field'});
  if (!empty.length) {
    send({t: 'nodice', msg: 'no context with an empty key field. The writer only runs for a segment ' +
          'the player has not decrypted yet -- open a lesson that has not been played on this machine.'});
    return null;
  }

  // Aim just past what has already been decrypted: the player decrypts roughly in playhead order, so
  // the lowest indexes still empty are the ones about to be filled. An arbitrary context, which is
  // what the first version guarded, is most likely one the player will never touch again.
  var keyed = objs.filter(function (o) { return !o.empty; })
                  .map(function (o) { return o.index; });
  var frontier = keyed.length ? Math.max.apply(null, keyed) : -1;
  var byIndex = function (a, b) { return a.index - b.index; };
  var ahead = empty.filter(function (o) { return o.index > frontier; }).sort(byIndex);
  var chosen = (ahead.length ? ahead : empty.sort(byIndex)).slice(0, MAX_PAGES);
  send({t: 'log', msg: 'highest decrypted index is ' + frontier + '; aiming at ' +
        chosen.length + ' context(s) from index ' + chosen[0].index + ' upward'});

  var pages = {};
  chosen.forEach(function (o) {
    var field = o.obj.add(KEY_FIELD);
    var pg = field.and(ptr('0xfffffffffffff000')).toString();
    pages[pg] = { page: pg, fieldOff: parseInt(field.and(ptr('0xfff')).toString(), 16),
                  file: o.file, index: o.index, ctx: o.obj.toString() };
    send({t: 'log', msg: '  watching index ' + o.index + '  ' + o.file});
  });
  watched = Object.keys(pages).map(function (k) { return pages[k]; });
  ranges = Object.keys(pages).map(function (k) { return { base: ptr(k), size: 0x1000 }; });
  return ranges;
}

function fieldAt(pageStr, off) {
  for (var i = 0; i < watched.length; i++) {
    var w = watched[i];
    if (w.page === pageStr && off >= w.fieldOff && off < w.fieldOff + 32) return w;
  }
  return null;
}

function onAccess(d) {
  if (found || reports >= REPORT_CAP) return;
  reports++;
  var pg = d.address.and(ptr('0xfffffffffffff000')).toString();
  // parseInt, not .toNumber(): `.and()` yields a NativePointer, and NativePointer has no
  // toNumber() -- only UInt64 does. Calling it raises inside the callback, once per access, and the
  // watch then reports nothing at all while looking perfectly armed.
  var off = parseInt(d.address.and(ptr('0xfff')).toString(), 16);
  var hit = fieldAt(pg, off);

  if (hit || reports <= INDIVIDUAL_CAP) {
    send({t: 'access', op: d.operation, off: '0x' + off.toString(16), page: pg,
          from: modOff(d.from), field: hit ? hit.file : null});
  }

  if (d.operation === 'write' && hit) {
    found = true;
    if (timer) { clearInterval(timer); timer = null; }
    // Release every guard before reading: a read from the agent's own thread into a guarded page
    // raises `guard page was hit` instead of calling back. Measured, not assumed.
    try { MemoryAccessMonitor.disable(); } catch (e) {}
    send({t: 'write', by: modOff(d.from), from: d.from.toString(), file: hit.file,
          index: hit.index});
    // The value has to be read on a later turn of the loop, not here. A page guard faults BEFORE
    // the store executes -- the callback runs, and only when it returns does the instruction retry
    // and complete -- so reading the field inside the callback always returns the old contents.
    setTimeout(function () {
      var value = '<unreadable>';
      try { value = bytesToStr(ptr(hit.ctx).add(KEY_FIELD).readByteArray(32)); }
      catch (e) { value = '<' + e + '>'; }
      send({t: 'value', file: hit.file, index: hit.index, value: value});
    }, 0);
    // The access callback has the address but no thread context, so the registers and the backtrace
    // have to come from the instruction itself. Attaching once, after the fact, catches the next
    // store by the same instruction -- which is why it is guarded, and why it is done OUTSIDE the
    // callback's own bookkeeping rather than as a re-arm.
    if (!instrHooked) {
      instrHooked = true;
      try {
        Interceptor.attach(d.from, {
          onEnter: function () {
            var ctx = this.context;
            var regs = {};
            ['rax','rbx','rcx','rdx','rsi','rdi','r8','r9','r10','r11'].forEach(function (rn) {
              try { regs[rn] = ctx[rn].toString(); } catch (e) {}
            });
            var stack = [];
            try { stack = stringsIn(ctx.rsp, 0x800); } catch (e) {}
            // ACCURATE needs unwind data and raises `invalid operation` where it is missing.
            var bt = [];
            try { bt = Thread.backtrace(ctx, Backtracer.ACCURATE).map(modOff); }
            catch (e) {
              try { bt = Thread.backtrace(ctx, Backtracer.FUZZY).map(modOff); } catch (e2) {}
            }
            var addrs = [];
            try { addrs = stackReturnAddrs(ctx.rsp, 0x400); } catch (e) {}
            send({t: 'writer', at: modOff(ctx.pc), regs: regs, stack: stack, bt: bt, addrs: addrs});
          }
        });
        send({t: 'log', msg: 'the writer instruction is now hooked; keep it playing and its ' +
              'registers will follow on the next run.'});
      } catch (e) { send({t: 'err', where: 'hook-writer', e: String(e)}); }
    }
  }
}

function arm() {
  try {
    MemoryAccessMonitor.enable(ranges, { onAccess: onAccess });
    // Re-armed from here, on a timer -- never from inside onAccess, which wedges the target.
    timer = setInterval(function () {
      if (found) { if (timer) { clearInterval(timer); timer = null; } return; }
      ticks++;
      try { MemoryAccessMonitor.disable(); } catch (e) {}
      try { MemoryAccessMonitor.enable(ranges, { onAccess: onAccess }); }
      catch (e) { send({t: 'err', where: 're-arm', e: String(e)}); }
      if (ticks % 50 === 0) {
        send({t: 'log', msg: 'still watching ' + ranges.length + ' page(s); ' + reports +
              ' access(es) seen so far'});
      }
    }, TICK_MS);
  } catch (e) {
    send({t: 'err', where: 'arm', e: String(e)});
  }
}

try {
  if (chooseTargets()) arm();
} catch (e) {
  send({t: 'err', where: 'setup', e: String(e)});
}
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
        elif t == 'access':
            mark = '  <<< lands on a key field' if p.get('field') else ''
            print("[access] %-5s page+%-6s from %s%s" % (p['op'], p['off'], p['from'], mark),
                  flush=True)
        elif t == 'write':
            print("\n=== WRITE into a key field ===", flush=True)
            print("   instruction : %s" % p['by'], flush=True)
            print("   file        : %s  (index %s)" % (p['file'], p['index']), flush=True)
        elif t == 'value':
            print("   value now   : %s" % repr(p['value']), flush=True)
            print("   (read after the store completed, not during -- a guard faults before the "
                  "instruction runs)", flush=True)
        elif t == 'writer':
            print("\n=== the writer, with context ===", flush=True)
            print("   at    : %s" % p['at'], flush=True)
            for rn, v in (p.get('regs') or {}).items():
                print("   %s  : %s" % (rn, v), flush=True)
            if p.get('stack'):
                print("   stack strings: %s" % p['stack'][:30], flush=True)
            for i, fr in enumerate(p.get('bt') or []):
                print("   bt #%d %s" % (i, fr), flush=True)
            if not p.get('bt') and p.get('addrs'):
                print("   (no unwind-based backtrace; module pointers found on the stack instead)",
                      flush=True)
                for a in p['addrs'][:24]:
                    print("   %s" % a, flush=True)
        elif t == 'nodice':
            print("\n[!] %s" % p['msg'], flush=True)
        elif t == 'err':
            print("[err] %s: %s" % (p.get('where'), p.get('e')), flush=True)
        elif t == 'ready':
            print("[*] guards armed. 请保持播放。", flush=True)

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
