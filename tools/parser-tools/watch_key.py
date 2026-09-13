#!/usr/bin/env python3
"""Chase EVPlayer2's segment-key derivation.

Finding so far: the AES code that decrypts segments lives in PlayerLibRender56_vs.dll
(RVA 0x792c78 reads the inverse S-box), but its *callers* sit in dynamically allocated
executable memory (heap addresses that belong to no module) -- which is why static
analysis of the DLL never finds the MD5/derivation the key = MD5(tk+file+salt) implies.

This script therefore walks the live call chain and watches for the key store:

  A) breakpoint at dll+0x792c78 (AES decrypt): collect the unique caller addresses, then
     attach to each and dump registers, pointed-to strings, the stack, and the first
     bytes of the caller's code.

  B) write watch on ctx+0x120 (the 32-byte key field) of playback-context objects that
     are not yet "ready", so we catch the instruction that stores a freshly derived key.

Usage:  <python> -u watch_key.py [seconds]     (default 240)
Play a video while it runs and keep it playing.
"""
import frida
import os
import sys
import time

JS = r"""
'use strict';
var DLL = 'PlayerLibRender56_vs.dll';
var dll = Process.getModuleByName(DLL);
var AES_DECRYPT_RVA = 0x792c78;

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
function codeAt(a, n) {
  try { return bytesToStr(a.readByteArray(n)).replace(/[^\x20-\x7e]/g, '.'); } catch (e) { return null; }
}

// ---------- A) callers of the AES decrypt loop ----------
var callers = {}, callerCount = 0;
function inspectCaller(addr) {
  var p = ptr(addr);
  var id = p.toString();
  if (callers[id]) return;
  callers[id] = 1;
  callerCount++;
  send({t: 'log', msg: 'A) new caller #' + callerCount + ': ' + modOff(p)});
  try {
    Interceptor.attach(p, {
      onEnter: function () {
        var ctx = this.context;
        var regs = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','r8','r9','r10','r11','r12','r13','r14','r15'];
        var strs = {};
        regs.forEach(function (rn) {
          try {
            var s = ctx[rn].readCString(200);
            if (s && s.length >= 4) { var ok = 0; for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); if (c >= 32 && c < 127) ok++; } if (ok / s.length > 0.85) strs[rn] = s; }
          } catch (e) {}
        });
        send({t: 'caller-hit', at: modOff(p), strs: strs,
              stack: stringsIn(ctx.rsp, 0x400),
              bt: Thread.backtrace(ctx, Backtracer.ACCURATE).map(modOff)});
      }
    });
  } catch (e) { send({t: 'err', where: 'attach-caller', e: String(e)}); }
}

var aesHits = 0;
Interceptor.attach(dll.base.add(AES_DECRYPT_RVA), {
  onEnter: function () {
    try {
      var bt = Thread.backtrace(this.context, Backtracer.ACCURATE);
      for (var i = 0; i < bt.length && i < 3; i++) inspectCaller(bt[i]);
      if (aesHits++ === 0) send({t: 'aes-first', bt: bt.map(modOff), stack: stringsIn(this.context.rsp, 0x600)});
    } catch (e) {}
  }
});
send({t: 'log', msg: 'A) armed at ' + DLL + '+0x' + AES_DECRYPT_RVA.toString(16)});

// ---------- B) write watch on pending contexts' key field ----------
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
        objs.push({ obj: obj, file: file, ready: obj.add(0x264).readU8() });
      });
    } catch (e) {}
  });
  return objs;
}

var watch = { ranges: [], fieldOf: {}, armed: false, hits: 0 };
function keyFieldOf(page) { return watch.fieldOf[page.toString()]; }
function armWatch() {
  var objs = findContexts();
  var pending = objs.filter(function (o) { return !o.ready; });
  var target = pending.length ? pending : objs;
  var pages = {};
  target.forEach(function (o) {
    var f = o.obj.add(0x120);
    var pg = f.and(ptr('0xfffffffffffff000'));
    pages[pg.toString()] = f;
  });
  var list = Object.keys(pages).slice(0, 96);
  if (!list.length) return false;
  watch.fieldOf = pages;
  watch.ranges = list.map(function (k) { return { base: ptr(k), size: 0x1000 }; });
  watch.armed = true;
  send({t: 'log', msg: 'B) watching ' + list.length + ' key pages (' + objs.length +
        ' contexts, ' + pending.length + ' pending)'});
  enableWatch();
  return true;
}
function enableWatch() {
  try {
    MemoryAccessMonitor.enable(watch.ranges, {
      onAccess: function (d) {
        if (watch.hits > 24) return;
        if (d.operation !== 'write') return;
        var field = null;
        var pg = d.address.and(ptr('0xfffffffffffff000')).toString();
        if (watch.fieldOf[pg]) field = watch.fieldOf[pg];
        if (!field) return;
        if (d.address.compare(field.sub(16)) < 0 || d.address.compare(field.add(48)) >= 0) return;
        watch.hits++;
        send({t: 'log', msg: 'B) WRITE to key field by ' + modOff(d.from)});
        try {
          Interceptor.attach(d.from, {
            onEnter: function () {
              send({t: 'key-write', at: modOff(d.from),
                    stack: stringsIn(this.context.rsp, 0x600),
                    bt: Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOff)});
            }
          });
        } catch (e) {}
      }
    });
  } catch (e) { send({t: 'err', where: 'watch', e: String(e)}); }
}

var tries = 0;
var poll = setInterval(function () {
  tries++;
  try {
    if (watch.ranges.length === 0) { armWatch(); }
    else if (watch.hits === 0) {
      // one access per page: re-arm so later stores are still caught
      MemoryAccessMonitor.disable();
      watch.ranges = watch.ranges.map(function (r) { return { base: r.base, size: r.size }; });
      enableWatch();
      if (tries % 10 === 0) send({t: 'log', msg: 'B) re-armed watch (try ' + tries + ')'});
    }
  } catch (e) {}
  if (tries > 300) clearInterval(poll);
}, 1200);

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
        elif t == 'aes-first':
            print("\n=== first decrypt call ===", flush=True)
            for i, fr in enumerate(p['bt']):
                print("   #%d %s" % (i, fr), flush=True)
            if p.get('stack'):
                print("   stack: %s" % p['stack'][:20], flush=True)
        elif t == 'caller-hit':
            print("\n--- caller %s ---" % p['at'], flush=True)
            for rn, sv in (p.get('strs') or {}).items():
                print("   %s -> %r" % (rn, sv), flush=True)
            if p.get('stack'):
                print("   stack strings: %s" % p['stack'][:20], flush=True)
            print("   call chain: %s" % (p.get('bt') or [])[:6], flush=True)
        elif t == 'key-write':
            print("\n=== WRITE to ctx+0x120 from %s ===" % p['at'], flush=True)
            for i, fr in enumerate(p['bt']):
                print("   #%d %s" % (i, fr), flush=True)
            if p.get('stack'):
                print("   stack strings: %s" % p['stack'][:25], flush=True)
        elif t == 'err':
            print("[err] %s: %s" % (p.get('where'), p.get('e')), flush=True)
        elif t == 'ready':
            print("[*] hooks installed. 请打开视频并保持播放。", flush=True)

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
