"""Scan the live player for segment keys and test them against every downloaded segment.

`harvest_keys.py` collected every 32-hex string in memory but only tested the six newest files,
which is a very small target: a key is only useful if its own segment is on disk, and the six
newest are an arbitrary slice. This tests each candidate against *all* downloaded segments.

The filter is cheap because a matching key must put an MPEG-TS sync byte (0x47) at offset 0 and
188; candidates that survive that are then verified across 200 packets, which no wrong key can
pass. Run it while the player is downloading or playing.

Usage:
    python scan_keys_all.py [pid]
"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import sys
import time

import frida
from Crypto.Cipher import AES

DOWNLOADS = r"D:\Downloads\EVPlayer2Downloads"
N_VERIFY = 188 * 200

JS = r"""
'use strict';
function isHex(c) {
  return (c >= 0x30 && c <= 0x39) || (c >= 0x61 && c <= 0x66) || (c >= 0x41 && c <= 0x46);
}
rpc.exports = {
  hexstrings: function () {
    var found = {};
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x10000000) return;
      var CH = 0x200000;
      for (var off = 0; off < r.size; off += CH) {
        var len = Math.min(CH, r.size - off);
        var buf;
        try { buf = r.base.add(off).readByteArray(len); } catch (e) { continue; }
        if (!buf) continue;
        var u = new Uint8Array(buf);
        var start = -1;
        for (var i = 0; i <= u.length; i++) {
          var c = (i < u.length) ? u[i] : 0;
          if (isHex(c)) { if (start < 0) start = i; }
          else {
            if (start >= 0 && (i - start) === 32) {
              var s = '';
              for (var k = start; k < i; k++) s += String.fromCharCode(u[k]);
              found[s.toLowerCase()] = 1;
            }
            start = -1;
          }
        }
      }
    });
    return Object.keys(found);
  }
};
send({ t: 'ready' });
"""


def find_pid():
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EVPlayer2.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == "evplayer2.exe":
            return int(parts[1])
    return None


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running")
        return 1

    segs = [n for n in os.listdir(DOWNLOADS) if n.endswith(".ts")]
    print("segments on disk: %d" % len(segs))
    heads, masks = {}, {}
    for n in segs:
        try:
            with open(os.path.join(DOWNLOADS, n), "rb") as fh:
                data = fh.read(16)
            if len(data) < 16:
                continue
            m = hashlib.md5(n.encode()).hexdigest()[:16].encode()
            masks[n] = m
            heads[n] = bytes(b ^ m[i % 16] for i, b in enumerate(data))
        except OSError:
            continue
    print("readable: %d" % len(heads))

    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", lambda m, d: None)
    script.load()
    t0 = time.time()
    cands = [c.lower() for c in script.exports_sync.hexstrings()]
    print("32-hex strings in memory: %d  (%.1fs)" % (len(cands), time.time() - t0))

    survivors = []
    for c in cands:
        aes = AES.new(c.encode(), AES.MODE_ECB)
        for n, blk in heads.items():
            if aes.decrypt(blk)[0] == 0x47:
                survivors.append((c, n))
    print("survived the offset-0 filter: %d" % len(survivors))

    hits = 0
    for c, n in survivors:
        data = open(os.path.join(DOWNLOADS, n), "rb").read(N_VERIFY)
        m = masks[n]
        buf = bytes(b ^ m[i % 16] for i, b in enumerate(data))
        p = AES.new(c.encode(), AES.MODE_ECB).decrypt(buf)
        good = sum(1 for i in range(0, len(p), 188) if p[i] == 0x47)
        if good == len(p) // 188:
            hits += 1
            print("\n*** KEY FOUND ***")
            print("    segment : %s" % n)
            print("    key     : %s" % c)
    print("\nverified hits: %d" % hits)
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
