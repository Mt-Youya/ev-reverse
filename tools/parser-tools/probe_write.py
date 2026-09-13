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
  * Nothing else may be reading the same process. An `evmedia grab` against the same pid reads
    `ctx+0x120` itself on every poll, and a cross-process read can consume a PAGE_GUARD without ever
    calling back here. Run the two instruments in separate phases, never together.

What the output means:
  "hb #N reports=..."             -- the run is alive. This is the line that decides whether the run
                                     means anything: heartbeats that keep coming are a measurement,
                                     heartbeats that stop make the run VOID
  "access ... <<< lands on a key field" -- an access landed inside a watched key field; if op=write,
                                     that is the answer to ticket 07 and the instruction is named
  "the writer, with context"      -- that instruction ran again; registers, stack and backtrace follow
  "no heartbeat was ever seen"    -- the instrument observed nothing. NOT a negative about the player.
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
  9. **A reporting cap must not short-circuit the catch.** The second version stopped processing
     accesses once it had printed 600 of them, which on the player's pages happened within seconds
     -- so it sat armed, re-arming and permanently deaf. The cap now suppresses only the printing.
 10. **Re-arm by deferring a turn, not on a timer and never synchronously.** Against a target doing
     24 stores with reads in between: not re-arming caught 0; a 100 ms timer caught 23 and missed
     one; `setTimeout(..., 0)` from inside the callback caught **24 of 24** with the target healthy.
     A page the player reads continuously is mostly blind under a timer, which is what the player's
     own run showed: 600 accesses seen and not one of them a store.
 11. **That 24-of-24 does not transfer to many hot pages, and the page count is the binding limit.**
     The measurement above used ONE page of a sleeping target. Re-measured against a storm target --
     24 pages read in tight loops by four threads, then 24 stores at page+0x120, in
     `tools/parser-tools/storm/`, whose driver extracts this file's own JS and runs it unmodified --
     at 24, 8 and 4 guarded pages the heartbeat fired once, no store was caught, and the target did
     not even finish its own twelve-second storm. At 2 pages the full result came back in four runs
     of five, and at 1 page in every run. **MAX_PAGES is 2**; four is already too many.
 12. **The page count is reproducible and the rate limit is not, so do not state either as clean.**
     At 2 pages, removing `REARM_FLOOR_MS` reproduced the failure in both attempts (one heartbeat, no
     catch, target wedged) -- but at 1 page one unthrottled run passed, and one run of the shipped
     2-page setting failed while its four repeats passed. Prefer the fewest pages that cover the
     question; do not read a single passing or failing run as the boundary; repeat before quoting a
     number. The direction is solid, the edge is noisy.
     What that means for the heartbeat: it runs on the same event loop as the access callbacks and CAN
     be starved by them, which is what the player run of 2026-09-13 looks like. Liveness is therefore
     judged on the Python side from ANY message, not from the heartbeat alone, and a run with a silent
     channel is reported as VOID rather than as a negative about the player.

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
import threading
import time

JS = r"""
'use strict';
var DLL = 'PlayerLibRender56_vs.dll';
var dll = Process.getModuleByName(DLL);
var EMPTY = 0xBAADF00D;      // what sits at +0x120 until a key is derived
var KEY_FIELD = 0x120;
var MAX_PAGES = 2;           // measured: see below. 4 is already too many on a contended process.
var HEARTBEAT_MS = 5000;     // proves the run is alive; carries the totals that say whether it worked
var REARM_FLOOR_MS = 2;      // rate limit on disable/enable -- see scheduleRearm
var INDIVIDUAL_CAP = 40;     // individual access lines stop after this many; hits always print

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
var writerDumps = 0;
var ticks = 0;
var rearms = 0;       // how many disable/enable pairs have actually run
var lastRearmAt = 0;  // when the last one ran, for the rate limit
var announcedCap = false;
var focus = null;     // the lowest empty index's watched entry; printed uncapped

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
  send({t: 'log', msg: keyed.length + ' of ' + objs.length + ' context(s) hold a key; the highest ' +
        'decrypted index is ' + frontier});
  if (keyed.length <= 2) {
    send({t: 'log', msg: '  that is very few. The writer only runs while the player decrypts a ' +
          'segment it has not decrypted on this machine before -- if this lesson has already been ' +
          'played here, nothing will be written and this run cannot catch anything. Open a lesson ' +
          'never played on this machine and keep it playing.'});
  }
  var byIndex = function (a, b) { return a.index - b.index; };
  var ahead = empty.filter(function (o) { return o.index > frontier; }).sort(byIndex);
  var chosen = (ahead.length ? ahead : empty.sort(byIndex)).slice(0, MAX_PAGES);
  send({t: 'log', msg: 'aiming at ' + chosen.length + ' context(s), index ' +
        chosen[0].index + '..' + chosen[chosen.length - 1].index});

  var pages = {};
  chosen.forEach(function (o) {
    var field = o.obj.add(KEY_FIELD);
    var pg = field.and(ptr('0xfffffffffffff000')).toString();
    pages[pg] = { page: pg, fieldOff: parseInt(field.and(ptr('0xfff')).toString(), 16),
                  file: o.file, index: o.index, ctx: o.obj.toString() };
  });
  watched = Object.keys(pages).map(function (k) { return pages[k]; });
  ranges = Object.keys(pages).map(function (k) { return { base: ptr(k), size: 0x1000 }; });
  // The map is keyed by page, so two chosen contexts sharing a page collapse into one entry and only
  // one of the pair is guarded. Say it rather than let the aiming line imply full coverage.
  if (watched.length < chosen.length) {
    send({t: 'log', msg: '  ' + chosen.length + ' context(s) collapse to ' + watched.length +
          ' page(s): ' + (chosen.length - watched.length) + ' share a page with another and are not ' +
          'individually watched'});
  }
  focus = watched.slice().sort(function (a, b) { return a.index - b.index; })[0] || null;
  if (focus) {
    send({t: 'log', msg: '  focus page: index ' + focus.index + ' (' + focus.file +
          '), its writes print uncapped'});
  }
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
  // The reporting cap must NOT short-circuit the catch below. The first version returned here once
  // the cap was reached, which on a busy page happened within seconds and left the watch armed,
  // re-arming and permanently deaf -- the same silent failure this script exists to avoid.
  if (found) return;
  reports++;
  var pg = d.address.and(ptr('0xfffffffffffff000')).toString();
  // parseInt, not .toNumber(): `.and()` yields a NativePointer, and NativePointer has no
  // toNumber() -- only UInt64 does. Calling it raises inside the callback, once per access, and the
  // watch then reports nothing at all while looking perfectly armed.
  var off = parseInt(d.address.and(ptr('0xfff')).toString(), 16);
  var hit = fieldAt(pg, off);

  // A focus page is printed uncapped on WRITES only. Printing every read uncapped on a page the
  // player reads thousands of times a second floods the message channel and starves the very
  // heartbeat that is supposed to prove the run is alive -- measured on the storm stand-in, where it
  // held the heartbeat to a single beat. Writes there are rare, which is the point of watching them.
  var focusWrite = !!(focus && focus.page === pg && d.operation === 'write');
  if (hit || focusWrite || reports <= INDIVIDUAL_CAP) {
    send({t: 'access', op: d.operation, off: '0x' + off.toString(16), page: pg,
          from: modOff(d.from), field: hit ? hit.file : null, focus: focusWrite});
  } else if (!announcedCap) {
    // The stopping at INDIVIDUAL_CAP lines is cosmetic. Say so once, or the operator reads the
    // sudden silence as "the accesses ended" -- which is the misreading this script exists to stop.
    announcedCap = true;
    send({t: 'log', msg: 'individual access lines suppressed after ' + INDIVIDUAL_CAP +
          '; the heartbeat carries the running total, and writes to the focus page still print'});
  }

  if (d.operation === 'write' && hit) {
    found = true;
    if (timer) { clearInterval(timer); timer = null; }
    // Flush the totals before going quiet -- the last numbers are what say how much of the window
    // was actually watched, and they are the one thing a caught store does not otherwise report.
    send({t: 'totals', ticks: ticks, reports: reports, rearms: rearms, pages: ranges.length,
          reason: 'key field written'});
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
    // store by the same instruction -- which is why it is guarded.
    if (!instrHooked) {
      instrHooked = true;
      try {
        Interceptor.attach(d.from, {
          onEnter: function () {
            // The instruction keeps running for every other context, so dump it a few times and
            // stop; the first one is the answer and the rest is noise.
            if (writerDumps++ >= 3) return;
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
    return;
  }

  // Re-arm with a deferred turn, rate-limited. Not synchronously here and not only on a timer.
  //
  // Synchronously re-enabling inside the callback WEDGES the target -- measured: 200 reports and
  // zero target operations for 8 seconds. Deferring one turn caught 24 of 24 stores on a stand-in,
  // so it is the primary mechanism and the timer below is the safety net.
  //
  // That 24-of-24 was measured on ONE page of a single-threaded target that slept between stores.
  // Against the real player -- 24 hot pages, Qt and ntdll reading them, and a second instrument
  // scanning the same memory -- a re-arm per access is one disable/enable pair per access, and the
  // run of 2026-09-13 went silent after 40 accesses with its heartbeat never once firing. The
  // heartbeat runs on this same event loop, so churn here can starve the very line that would
  // report the run is alive. The floor bounds it rather than removing it.
  scheduleRearm();
}

var rearmPending = false;
function scheduleRearm() {
  if (rearmPending || found) return;
  rearmPending = true;
  // The floor is what keeps the event loop turning. Do not set it to 0 without re-measuring the
  // heartbeat against a many-page storm target: at 0 this is one disable/enable per access again,
  // and the heartbeat stops -- which is the failure that produced this line.
  var wait = Math.max(0, REARM_FLOOR_MS - (Date.now() - lastRearmAt));
  setTimeout(function () {
    rearmPending = false;
    if (found) return;
    lastRearmAt = Date.now();
    rearms++;
    try { MemoryAccessMonitor.disable(); } catch (e) {}
    try { MemoryAccessMonitor.enable(ranges, { onAccess: onAccess }); }
    catch (e) { send({t: 'err', where: 're-arm', e: String(e)}); }
  }, wait);
}

function beat() {
  if (found) return;
  send({t: 'hb', ticks: ticks, reports: reports, rearms: rearms, pages: ranges.length,
        pending: rearmPending, at: Date.now()});
  ticks++;
  // The safety-net re-arm. On a healthy run the deferred path above gets there first.
  try { MemoryAccessMonitor.disable(); } catch (e) {}
  try { MemoryAccessMonitor.enable(ranges, { onAccess: onAccess }); }
  catch (e) { send({t: 'err', where: 're-arm', e: String(e)}); }
}

function arm() {
  try {
    MemoryAccessMonitor.enable(ranges, { onAccess: onAccess });
    // One beat NOW, before the interval's first period elapses. A run that is killed, wedged or
    // starved within its first few seconds is exactly the run whose silence is ambiguous, and a
    // heartbeat that only starts at t=5s cannot speak for that window.
    beat();
    // This interval is both the safety-net re-arm and the only proof the run is alive. It fires on
    // its FIRST period and carries the running totals, so "capped but alive" is distinguishable
    // from "the agent stopped": a heartbeat with reports climbing and no key-field hit is a real
    // negative, while a heartbeat that never arrives makes the run void rather than a statement
    // about the player. Nothing here may be gated on reports or on a write.
    timer = setInterval(beat, HEARTBEAT_MS);
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

    # Liveness is judged on ANY message, not only the heartbeat. The heartbeat runs on the agent's
    # event loop, and a storm of access callbacks can hold it off for the whole run -- measured on the
    # storm stand-in, where access traffic starved the interval to a single beat while the agent was
    # demonstrably alive and catching stores. A silent channel means the run is void; a busy one means
    # it is alive even when no heartbeat arrives.
    live = {'last': 0.0, 'hb_seen': 0, 'msgs': 0, 'stop': False}
    # The JS heartbeat is every 5 s (`var HEARTBEAT_MS = 5000`); this is the ceiling for ANY message.
    WATCHDOG_S = 18

    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(msg, data):
        live['last'] = time.monotonic()
        live['msgs'] += 1
        p = msg.get('payload') or {}
        t = p.get('t')
        if t == 'log':
            print("[*] " + p['msg'], flush=True)
        elif t == 'hb':
            live['hb_seen'] += 1
            print("    [hb #%-4s] reports=%-7s rearms=%-7s pages=%s%s" % (
                p['ticks'], p['reports'], p['rearms'], p['pages'],
                '  re-arm pending' if p.get('pending') else ''), flush=True)
        elif t == 'totals':
            print("    [totals] reports=%s rearms=%s pages=%s (%s)" % (
                p['reports'], p['rearms'], p['pages'], p['reason']), flush=True)
        elif t == 'access':
            marks = []
            if p.get('field'):
                marks.append('<<< lands on a key field')
            if p.get('focus'):
                marks.append('(focus page)')
            print("[access] %-5s page+%-6s from %s%s" % (
                p['op'], p['off'], p['from'], ('  ' + ' '.join(marks)) if marks else ''),
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

    def watchdog():
        while not live['stop']:
            time.sleep(1)
            if live['last'] and time.monotonic() - live['last'] > WATCHDOG_S:
                print("\n[!] nothing from the agent for %.0fs -- it is no longer reporting. " %
                      (time.monotonic() - live['last']), flush=True)
                print("    This run is VOID: it says nothing about whether the player wrote a key. "
                      "Do not read it as a negative.", flush=True)
                live['last'] = time.monotonic()  # one warning per silence, not one per second

    script.on('message', on_message)
    script.on('destroyed', lambda *_: print(
        "\n[!] the agent was destroyed -- script unloaded or the target exited. Run is VOID.",
        flush=True))
    session.on('detached', lambda *_: print(
        "\n[!] the frida session detached -- the agent is gone. Run is VOID.", flush=True))
    script.load()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        pass
    live['stop'] = True
    try:
        session.detach()
    except Exception:
        pass
    if live['msgs'] == 0:
        print("[!] nothing was ever received from the agent: this run observed nothing rather than "
              "observing a player that did not write. VOID, not a negative.", flush=True)
    else:
        print("    %d message(s) received, %d heartbeat(s): the agent was reporting."
              % (live['msgs'], live['hb_seen']), flush=True)
    print("done.", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
