#!/usr/bin/env python3
"""EVPlayer2 API capture v6.

v6 findings that shape this script:
  * EVPlayer2 uses Qt 5.9.9. There is NO OpenSSL loaded (System32 has only a stray
    libcrypto.dll), so Qt's HTTPS path is dead. The live TLS stack is Schannel
    (Secur32/SSPICLI: DecryptMessage/EncryptMessage), and the app links
    nghttp2.dll -> HTTP/2 over Schannel. So the app's traffic is h2-over-TLS.
  * Therefore the highest-signal hooks are nghttp2's, which expose fully decoded
    headers and body chunks (no HPACK decoding needed), plus Qt's QJsonDocument
    (parsed API payloads) as a bonus.

Channels (all optional, whatever resolves):
  h2  : nghttp2_submit_request  -> outgoing request headers (URL/method) + stream id
        nghttp2_session_callbacks_set_on_header_callback / _2
        nghttp2_session_callbacks_set_on_data_chunk_recv_callback
                                  -> hook the app's own callbacks: response headers + bodies
  qt  : QNetworkAccessManager createRequest/get/post/... -> URLs ; QIODevice::read -> bodies
        QJsonDocument::fromJson/toJson -> parsed JSON payloads
  raw : Schannel DecryptMessage/EncryptMessage -> decrypted TLS plaintext (fallback)

Stop with captured/STOP or Ctrl+C. Everything is appended to captured/events.jsonl.
"""
import os
import sys
import json
import time
import signal

import frida

print("frida version:", frida.__version__)

JS = r"""
'use strict';

// ---------- module helpers (frida 17: instance methods) ----------
function modByName(name) { try { return Process.getModuleByName(name); } catch (e) { return null; } }
function findExport(mod, name) {
  var mo = modByName(mod);
  if (!mo) return null;
  try { if (typeof mo.findExportByName === 'function') { var a = mo.findExportByName(name); if (a) return a; } } catch (e) {}
  try { if (typeof mo.getExportByName === 'function') return mo.getExportByName(name); } catch (e) {}
  return null;
}
function listExports(mod) {
  var mo = modByName(mod);
  if (!mo) { send({event: 'diag', item: 'module-missing', symbol: mod}); return []; }
  try { var r = mo.enumerateExports(); if (r && r.length !== undefined) return r; } catch (e) {}
  return [];
}
function filter(list, cls, sub) {
  var out = [];
  list.forEach(function (e) {
    if (!e || !e.name) return;
    if (e.name.indexOf(cls) === -1 || e.name.indexOf(sub) === -1) return;
    if (e.name.indexOf('??_E') !== -1) return;
    out.push(e.name);
  });
  return out;
}
function firstMatch(list, cls, sub) { var f = filter(list, cls, sub); return f.length ? f[0] : null; }

// ---------- nghttp2 (HTTP/2) ----------
var streamUrls = {};        // stream_id -> request path
var hookedCallbacks = {};   // addr -> true (dedup)

// nghttp2_nv { uint8_t* name; uint8_t* value; size_t namelen; size_t valuelen; uint8_t flags; } -> 40 bytes
function readNvArray(nva, nvlen) {
  var out = [];
  for (var i = 0; i < nvlen && i < 48; i++) {
    var nv = nva.add(i * 40);
    try {
      var np = nv.readPointer(); var vp = nv.add(8).readPointer();
      var nl = nv.add(16).readU64().toNumber(); var vl = nv.add(24).readU64().toNumber();
      if (nl > 8192 || vl > 65536) continue;
      out.push([np.readUtf8String(nl), vp.readUtf8String(vl)]);
    } catch (e) {}
  }
  return out;
}

function attachDataCb(addr) {
  if (!addr || addr.isNull() || hookedCallbacks[addr.toString()]) return;
  hookedCallbacks[addr.toString()] = true;
  try {
    Interceptor.attach(addr, {
      onEnter: function (args) { this.skey = args[0].toString() + ':' + args[1].toInt32(); this.buf = args[2]; },
      onLeave: function (retval) {
        var n = retval.toInt32();
        if (n > 0 && n <= 4194304) send({event: 'h2reqbody', skey: this.skey, len: n}, this.buf.readByteArray(n));
      }
    });
    send({event: 'setup', item: 'h2 request-body callback hooked', symbol: addr.toString()});
  } catch (e) { send({event: 'diag', item: 'reqbody-cb-failed', error: String(e)}); }
}

function hookH2Submit() {
  ['nghttp2_submit_request', 'nghttp2_submit_request2'].forEach(function (sym) {
    var a = findExport('nghttp2.dll', sym);
    if (!a) return;
    try {
      Interceptor.attach(a, {
        onEnter: function (args) {
          this.sess = args[0];
          this.hdrs = readNvArray(args[2], args[3].toInt32());
          this.sym = sym;
          // nghttp2_data_provider { nghttp2_data_source source; read_callback; } -> read_callback at +8
          try { var dp = args[4]; if (dp && !dp.isNull()) attachDataCb(dp.add(8).readPointer()); } catch (e) {}
        },
        onLeave: function (retval) {
          var sid = retval.toInt32();
          if (sid > 0 && this.hdrs && this.hdrs.length) {
            var path = '/', method = '?';
            this.hdrs.forEach(function (h) { if (h[0] === ':path') path = h[1]; if (h[0] === ':method') method = h[1]; });
            var skey = this.sess.toString() + ':' + sid;   // session:sid is unique (sid restarts per connection)
            streamUrls[skey] = method + ' ' + path;
            send({event: 'h2req', skey: skey, sid: sid, hdrs: this.hdrs});
          }
        }
      });
      send({event: 'setup', item: 'nghttp2 submit', symbol: sym});
    } catch (e) { send({event: 'diag', item: 'h2submit-failed', symbol: sym, error: String(e)}); }
  });
}

function attachAppHeaderCb(addr, rcbuf) {
  if (hookedCallbacks[addr.toString()]) return;
  hookedCallbacks[addr.toString()] = true;
  try {
    Interceptor.attach(addr, {
      onEnter: function (args) {
        var sid = -1;
        try { sid = args[1].add(8).readS32(); } catch (e) {}
        var skey = args[0].toString() + ':' + sid;
        try {
          if (rcbuf) {
            var nb = args[2].readPointer(), vb = args[3].readPointer();  // nghttp2_rcbuf* -> buf {base, len}
            var nlen = nb.add(8).readU64().toNumber(), vlen = vb.add(8).readU64().toNumber();
            send({event: 'h2hdr', skey: skey, sid: sid, name: nb.readPointer().readUtf8String(nlen), value: vb.readPointer().readUtf8String(vlen)});
          } else {
            var nl = args[3].toInt32(), vl = args[5].toInt32();
            send({event: 'h2hdr', skey: skey, sid: sid, name: args[2].readUtf8String(nl), value: args[4].readUtf8String(vl)});
          }
        } catch (e) {}
      }
    });
    send({event: 'setup', item: 'h2 header callback hooked', symbol: addr.toString() + (rcbuf ? ' (rcbuf)' : '')});
  } catch (e) { send({event: 'diag', item: 'h2hdr-cb-failed', error: String(e)}); }
}

function attachAppDataCb(addr) {
  if (hookedCallbacks[addr.toString()]) return;
  hookedCallbacks[addr.toString()] = true;
  try {
    Interceptor.attach(addr, {
      onEnter: function (args) {
        try {
          var sid = args[2].toInt32();
          var len = args[4].toInt32();
          if (len > 0 && len <= 4194304) {
            send({event: 'h2data', skey: args[0].toString() + ':' + sid, sid: sid, len: len}, args[3].readByteArray(len));
          }
        } catch (e) {}
      }
    });
    send({event: 'setup', item: 'h2 data callback hooked', symbol: addr.toString()});
  } catch (e) { send({event: 'diag', item: 'h2data-cb-failed', error: String(e)}); }
}

function hookH2CallbackSetters() {
  var s = findExport('nghttp2.dll', 'nghttp2_session_callbacks_set_on_header_callback');
  if (s) Interceptor.attach(s, { onEnter: function (args) { send({event: 'setup', item: 'on_header_callback setter FIRED'}); attachAppHeaderCb(args[1], false); } });
  var s2 = findExport('nghttp2.dll', 'nghttp2_session_callbacks_set_on_header_callback2');
  if (s2) Interceptor.attach(s2, { onEnter: function (args) { send({event: 'setup', item: 'on_header_callback2 setter FIRED'}); attachAppHeaderCb(args[1], true); } });
  var sd = findExport('nghttp2.dll', 'nghttp2_session_callbacks_set_on_data_chunk_recv_callback');
  if (sd) Interceptor.attach(sd, { onEnter: function (args) { send({event: 'setup', item: 'on_data_chunk_recv setter FIRED'}); attachAppDataCb(args[1]); } });
  send({event: 'setup', item: 'h2 callback setters armed', symbol: 'hdr=' + !!s + ' hdr2=' + !!s2 + ' data=' + !!sd});
}

// ---------- Qt ----------
var reqUrlFn = null, urlToStringFn = null, qbaToStdFn = null;
var scratch1 = Memory.alloc(64), scratch2 = Memory.alloc(64), scratch3 = Memory.alloc(64);
var replyUrls = {};

function utf16(ptr) {
  try {
    var d = ptr.readPointer(); if (d.isNull()) return null;
    var len = d.add(4).readU32(); if (len > 16384) return null;
    return len === 0 ? '' : d.add(16).readUtf16String(len);
  } catch (e) { return null; }
}
function readUrl(reqPtr) {
  try {
    if (!reqUrlFn || !reqPtr || reqPtr.isNull()) return '(no url)';
    scratch1.writeU64(0); scratch1.add(8).writeU64(0);
    var qurl = reqUrlFn(scratch1, reqPtr);
    if (qurl.isNull() || qurl.readPointer().isNull()) return '(empty)';
    if (!urlToStringFn) return '(no toString)';
    scratch2.writeU64(0); scratch2.add(8).writeU64(0);
    var qstr = urlToStringFn(scratch2, qurl, 0, 0);
    return utf16(qstr) || '(unreadable)';
  } catch (e) { return '(err ' + e + ')'; }
}
function readQByteArrayPtr(qba) {
  // qba: pointer to QByteArray { Data *d }; Data { ref(4) size(4) alloc(4) pad offset(8) } data at d+offset
  try {
    var d = qba.readPointer(); if (d.isNull()) return null;
    var size = d.add(4).readS32(); if (size < 0 || size > 8388608) return null;
    var offset = d.add(16).readS64().toNumber();
    if (offset < 0 || offset > 4096) offset = 16;
    return d.add(offset).readByteArray(size);
  } catch (e) { return null; }
}
function hookQtJson() {
  var fj = filter(listExports('Qt5Core.dll'), 'QJsonDocument', 'fromJson@')[0];
  if (fj) {
    var a = findExport('Qt5Core.dll', fj);
    if (a) Interceptor.attach(a, { onEnter: function (args) { var b = readQByteArrayPtr(args[1]); if (b) send({event: 'json-in'}, b); } });
    send({event: 'setup', item: 'QJsonDocument::fromJson', symbol: fj});
  }
  var tj = filter(listExports('Qt5Core.dll'), 'QJsonDocument', 'toJson@')[0];
  if (tj) {
    var a2 = findExport('Qt5Core.dll', tj);
    if (a2) Interceptor.attach(a2, { onLeave: function (retval) { var b = readQByteArrayPtr(retval); if (b) send({event: 'json-out'}, b); } });
    send({event: 'setup', item: 'QJsonDocument::toJson', symbol: tj});
  }
}
function hookQtNet() {
  var net = listExports('Qt5Network.dll');
  ['get@', 'post@', 'put@', 'head@', 'deleteResource@', 'sendCustomRequest@'].forEach(function (sub) {
    filter(net, 'QNetworkAccessManager', sub).forEach(function (sym) {
      var a = findExport('Qt5Network.dll', sym); if (!a) return;
      try {
        Interceptor.attach(a, {
          onEnter: function (args) { this.url = readUrl(args[1]); send({event: 'qturl', src: sub, url: this.url}); },
          onLeave: function (retval) { if (retval && !retval.isNull() && this.url) replyUrls[retval.toString()] = this.url; }
        });
      } catch (e) {}
    });
  });
  var cr = filter(net, 'QNetworkAccessManager', 'createRequest@')[0];
  if (cr) {
    var a = findExport('Qt5Network.dll', cr); var OPS = ['HEAD', 'GET', 'PUT', 'POST', 'DELETE', 'CUSTOM'];
    if (a) Interceptor.attach(a, {
      onEnter: function (args) { var op = args[1].toInt32(); this.url = readUrl(args[2]); send({event: 'qturl', src: 'createRequest:' + (OPS[op] || op), url: this.url}); },
      onLeave: function (retval) { if (retval && !retval.isNull() && this.url) replyUrls[retval.toString()] = this.url; }
    });
  }
  var rd = filter(net.concat(listExports('Qt5Core.dll')), 'QIODevice', 'read@')[0];
  var coreRd = filter(listExports('Qt5Core.dll'), 'QIODevice', 'read@')[0];
  if (coreRd) {
    var a2 = findExport('Qt5Core.dll', coreRd), nm = modByName('Qt5Network.dll');
    var lo = nm ? nm.base : ptr(0), hi = nm ? nm.base.add(nm.size) : ptr(0);
    if (a2) Interceptor.attach(a2, {
      onEnter: function (args) { this.keep = false; try { var vt = args[0].readPointer(); if (!vt.isNull() && vt.compare(lo) >= 0 && vt.compare(hi) < 0) { this.keep = true; this.url = replyUrls[args[0].toString()] || '(unknown)'; } } catch (e) {} this.buf = args[1]; },
      onLeave: function (retval) { if (!this.keep) return; var n = retval.toInt32(); if (n <= 0) return; if (this.url.indexOf('.ts') !== -1) return; var cap = Math.min(n, 262144); try { send({event: 'qtresp', url: this.url, len: n}, this.buf.readByteArray(cap)); } catch (e) {} }
    });
  }
  send({event: 'setup', item: 'Qt net hooks installed'});
}
function hookSchannel() {
  var seen = {}, installed = 0;
  [['sspicli.dll', 'DecryptMessage', true], ['sspicli.dll', 'EncryptMessage', false],
   ['secur32.dll', 'DecryptMessage', true], ['secur32.dll', 'EncryptMessage', false]].forEach(function (t) {
    var addr = findExport(t[0], t[1]); if (!addr) return;
    var key = addr.toString(); if (seen[key]) return; seen[key] = true;   // secur32 forwards to sspicli
    var isDecrypt = t[2];
    function dump(ctx, phase) {
      try {
        var count = ctx.desc.add(4).readU32(), arr = ctx.desc.add(8).readPointer();
        for (var i = 0; i < count && i < 8; i++) {
          var b = arr.add(i * 16), cb = b.readU32(), type = b.add(4).readU32(), pv = b.add(8).readPointer();
          if (cb > 0 && cb <= 1048576 && type === 1) send({event: 'raw', dir: ctx.dir, len: cb, phase: phase}, pv.readByteArray(cb));
        }
      } catch (e) {}
    }
    Interceptor.attach(addr, {
      onEnter: function (args) { this.desc = isDecrypt ? args[1] : args[2]; this.dir = isDecrypt ? 'in' : 'out'; if (!isDecrypt) dump(this, 'enter'); },
      onLeave: function (r) { if (isDecrypt) dump(this, 'leave'); }
    });
    installed++;
  });
  send({event: 'setup', item: 'schannel hooks installed', symbol: String(installed)});
}

// Windows CNG (bcrypt.dll): capture symmetric keys and AES plaintext/ciphertext.
// The API payload crypto (base64 "params"/"result", encrypt:1/zip:1) uses CNG.
function hookBcrypt() {
  var B = 'bcrypt.dll';
  function hookSym(sym, secretArg, secretLenArg) {
    var a = findExport(B, sym); if (!a) { send({event: 'diag', item: 'bcrypt-missing', symbol: sym}); return; }
    Interceptor.attach(a, {
      onEnter: function (args) {
        try {
          var cb = args[secretLenArg].toInt32(), p = args[secretArg];
          if (cb > 0 && cb <= 128 && p && !p.isNull()) send({event: 'bcrypt-key', src: sym, len: cb}, p.readByteArray(cb));
        } catch (e) {}
      }
    });
    send({event: 'setup', item: 'bcrypt key hook', symbol: sym});
  }
  hookSym('BCryptGenerateSymmetricKey', 4, 5);   // (hAlg, phKey, obj, objLen, pbSecret, cbSecret, flags)
  hookSym('BCryptImportKey', 6, 7);              // (hAlg, hImp, type, phKey, obj, objLen, pbSecret, cbSecret, flags)

  var dd = findExport(B, 'BCryptDecrypt');
  if (dd) Interceptor.attach(dd, {
    onEnter: function (args) { this.out = args[6]; this.pres = args[8]; },
    onLeave: function (rv) {
      try {
        if (rv.toInt32() >= 0) {
          var n = this.pres.readU32();
          if (n > 0 && n <= 524288 && !this.out.isNull()) send({event: 'bcrypt-dec', len: n}, this.out.readByteArray(n));
        }
      } catch (e) {}
    }
  });
  var de = findExport(B, 'BCryptEncrypt');
  if (de) Interceptor.attach(de, {
    onEnter: function (args) {   // plaintext is pbInput before encryption
      try { var n = args[2].toInt32(); if (n > 0 && n <= 524288 && !args[1].isNull()) send({event: 'bcrypt-enc', len: n}, args[1].readByteArray(n)); } catch (e) {}
    }
  });
  send({event: 'setup', item: 'bcrypt hooks', symbol: 'dec=' + !!dd + ' enc=' + !!de});
}

// zlib: the API payload is AES-encrypted with a statically-linked cipher (no CNG/CryptoAPI),
// so we can't hook the cipher. But encrypted responses are zlib-compressed (zip:1), and the
// requests are compressed too. Hooking zlib gives the plaintext JSON after decryption /
// before encryption -> no need to break the AES.
function hookZlib() {
  var Z = 'zlib.dll';
  function ex(sym) { var a = findExport(Z, sym); if (!a) send({event: 'diag', item: 'zlib-missing', symbol: sym}); return a; }
  function oneShot(sym, isDec) {
    var a = ex(sym); if (!a) return;
    Interceptor.attach(a, isDec ? {
      onEnter: function (args) { this.dest = args[0]; this.pdl = args[1]; },
      onLeave: function (rv) { try { if (rv.toInt32() === 0) { var n = this.pdl.readU32(); if (n > 0 && n <= 8388608) send({ event: 'z-uncompress', len: n }, this.dest.readByteArray(n)); } } catch (e) {} }
    } : {
      onEnter: function (args) { try { var n = args[3].toInt32(); if (n > 0 && n <= 8388608 && !args[2].isNull()) send({ event: 'z-compress', len: n }, args[2].readByteArray(n)); } catch (e) {} }
    });
  }
  oneShot('uncompress', true);
  oneShot('uncompress2', true);
  oneShot('compress', false);
  oneShot('compress2', false);
  // streaming inflate: produced bytes = avail_out_before - avail_out_after, at next_out
  var inf = ex('inflate');
  if (inf) Interceptor.attach(inf, {
    onEnter: function (args) { this.s = args[0]; try { this.out0 = this.s.add(16).readPointer(); this.av0 = this.s.add(24).readU32(); } catch (e) { this.out0 = null; } },
    onLeave: function (rv) { try { if (!this.out0) return; var n = this.av0 - this.s.add(24).readU32(); if (n > 0 && n <= 8388608) send({ event: 'z-inflate', len: n }, this.out0.readByteArray(n)); } catch (e) {} }
  });
  var def = ex('deflate');
  if (def) Interceptor.attach(def, {
    onEnter: function (args) { try { var s = args[0]; var ni = s.add(0).readPointer(); var ai = s.add(8).readU32(); if (ai > 0 && ai <= 8388608) send({ event: 'z-deflate', len: ai }, ni.readByteArray(ai)); } catch (e) {} }
  });
  send({event: 'setup', item: 'zlib hooks', symbol: 'uncompress=' + !!findExport(Z, 'uncompress') + ' inflate=' + !!inf + ' deflate=' + !!def});
}

function main() {
  Process.enumerateModules().forEach(function (m) {
    var n = m.name.toLowerCase();
    if (n.indexOf('nghttp') !== -1 || n.indexOf('sspi') !== -1 || n.indexOf('secur32') !== -1) {
      send({event: 'setup', item: 'loaded module', symbol: m.name + ' base=' + m.base});
    }
  });
  var net = listExports('Qt5Network.dll'), core = listExports('Qt5Core.dll');
  send({event: 'setup', item: 'Qt5Network.dll exports', symbol: String(net.length)});
  send({event: 'setup', item: 'Qt5Core.dll exports', symbol: String(core.length)});

  var reqCands = filter(net, 'QNetworkRequest', 'url@');
  if (reqCands.length) { var a = findExport('Qt5Network.dll', reqCands[0]); if (a) reqUrlFn = new NativeFunction(a, 'pointer', ['pointer', 'pointer']); }
  var urlCands = filter(core, 'QUrl', 'toString@');
  if (urlCands.length) { var ua = findExport('Qt5Core.dll', urlCands[0]); if (ua) urlToStringFn = new NativeFunction(ua, 'pointer', ['pointer', 'pointer', 'uint', 'uint']); }
  send({event: 'setup', item: 'url helpers', symbol: 'req=' + !!reqUrlFn + ' toString=' + !!urlToStringFn});

  hookH2Submit();
  hookH2CallbackSetters();
  hookQtJson();
  hookQtNet();
  hookSchannel();
  hookBcrypt();
  hookZlib();
  send({event: 'ready'});
}
try { main(); } catch (e) { send({event: 'setup', item: 'fatal', error: String(e), stack: e.stack}); }
"""


def find_pid():
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() == "evplayer2.exe":
            return p.pid
    return None


def main():
    pid = find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running. Start it first (tools/device_launcher/run-evplayer.cmd).")
        return 1
    print("attaching to EVPlayer2.exe pid=%d" % pid)

    here = os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.join(here, "captured")
    os.makedirs(outdir, exist_ok=True)
    stop_file = os.path.join(outdir, "STOP")
    if os.path.exists(stop_file):
        os.remove(stop_file)

    urllog = open(os.path.join(outdir, "urls.log"), "a", encoding="utf-8")
    events = open(os.path.join(outdir, "events.jsonl"), "a", encoding="utf-8")
    raw = open(os.path.join(outdir, "plaintext.bin"), "ab")
    bodies = open(os.path.join(outdir, "bodies.bin"), "ab")
    bcryptf = open(os.path.join(outdir, "bcrypt.bin"), "ab")
    zlibf = open(os.path.join(outdir, "zlib.bin"), "ab")
    seq = [0]
    py_urls = {}   # stream_id -> "METHOD path" (mirror of the JS map)

    def mark(s):
        bodies.write(("\n===== %s =====\n" % s).encode("utf-8"))

    def on_message(msg, data):
      try:
        p = msg.get("payload")
        if p is None:
            print("[frida-error]", msg, flush=True); return
        ev = p.get("event")
        if ev in ("setup", "diag"):
            print("[setup]", p.get("item"), p.get("symbol", ""), p.get("error", ""), flush=True)
            return
        n = len(data) if data else 0
        events.write(json.dumps({**p, "n": n}, ensure_ascii=False) + "\n"); events.flush()
        if ev == "h2req":
            hdrs = p.get("hdrs") or []
            hmap = dict(hdrs)
            py_urls[p.get("skey")] = "%s %s%s" % (hmap.get(":method", "?"), hmap.get(":authority", ""), hmap.get(":path", "?"))
            line = "H2 REQ %s  (%s)" % (py_urls[p.get("skey")],
                                        " ".join("%s=%s" % (k, v) for k, v in hdrs))
            print(line, flush=True); urllog.write(line + "\n"); urllog.flush()
        elif ev == "h2hdr":
            path = py_urls.get(p.get("skey"), "")
            line = "H2 HDR %s: %s   [%s]" % (p.get("name"), p.get("value"), path)
            if p.get("name") in (":status", ":path", ":method", "content-type", "content-length", "location"):
                print(line, flush=True)
            urllog.write(line + "\n"); urllog.flush()
        elif ev == "h2data":
            seq[0] += 1
            mark("H2DATA %s %d  %s" % (p.get("skey"), n, py_urls.get(p.get("skey"), ""))); bodies.write(bytes(data)); bodies.flush()
            print("[h2data %d bytes] %s" % (n, py_urls.get(p.get("skey"), "")), flush=True)
        elif ev == "h2reqbody":
            seq[0] += 1
            mark("H2REQBODY %s %d  %s" % (p.get("skey"), n, py_urls.get(p.get("skey"), ""))); bodies.write(bytes(data)); bodies.flush()
            print("[h2reqbody %d bytes] %s" % (n, py_urls.get(p.get("skey"), "")), flush=True)
        elif ev == "bcrypt-key":
            bcryptf.write(("\n===== KEY %s len=%d =====\n" % (p.get("src"), n)).encode()); bcryptf.write(bytes(data)); bcryptf.flush()
            print("[bcrypt-key %s %d]" % (p.get("src"), n), flush=True)
        elif ev == "bcrypt-dec":
            bcryptf.write(("\n===== DEC %d =====\n" % n).encode()); bcryptf.write(bytes(data)); bcryptf.flush()
            print("[bcrypt-dec %d]" % n, flush=True)
        elif ev == "bcrypt-enc":
            bcryptf.write(("\n===== ENC %d =====\n" % n).encode()); bcryptf.write(bytes(data)); bcryptf.flush()
            print("[bcrypt-enc %d]" % n, flush=True)
        elif ev and (ev.startswith("z-")):
            zlibf.write(("\n===== %s %d =====\n" % (ev.upper(), n)).encode()); zlibf.write(bytes(data)); zlibf.flush()
            print("[%s %d]" % (ev, n), flush=True)
        elif ev == "json-in":
            seq[0] += 1
            mark("JSON-IN %d" % n); bodies.write(bytes(data)); bodies.flush()
            print("[json-in %d bytes]" % n, flush=True)
        elif ev == "json-out":
            seq[0] += 1
            mark("JSON-OUT %d" % n); bodies.write(bytes(data)); bodies.flush()
            print("[json-out %d bytes]" % n, flush=True)
        elif ev == "qturl":
            line = "QT  %s %s" % (p.get("src"), p.get("url")); print(line, flush=True); urllog.write(line + "\n"); urllog.flush()
        elif ev == "qtresp":
            print("[qtresp %d] %s" % (n, p.get("url")), flush=True)
            mark("QTRESP %s" % p.get("url")); bodies.write(bytes(data)); bodies.flush()
        elif ev == "raw":
            raw.write(("\n===== RAW %s %d =====\n" % (p.get("dir"), n)).encode("utf-8")); raw.write(bytes(data)); raw.flush()
            print("[raw %s %d]" % (p.get("dir"), n), flush=True)
        elif ev == "ready":
            print("capture active. Log in + browse the catalog + open one video. "
                  "Create captured/STOP (or Ctrl+C) to finish.", flush=True)
      except Exception as e:
        print("[handler-error]", repr(e), flush=True)

    session = frida.attach(pid)
    session.on("detached", lambda *a: print("[detached]", a, flush=True))
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()

    stopping = {"go": False}
    signal.signal(signal.SIGINT, lambda *a: stopping.update(go=True))
    signal.signal(signal.SIGTERM, lambda *a: stopping.update(go=True))
    while not stopping["go"] and not os.path.exists(stop_file):
        time.sleep(0.5)

    for f in (urllog, events, raw, bodies, bcryptf, zlibf):
        f.close()
    try:
        session.detach()
    except Exception:
        pass
    print("done. see captured/urls.log + events.jsonl + bodies.bin + plaintext.bin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
