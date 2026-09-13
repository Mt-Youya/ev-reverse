#!/usr/bin/env python3
"""ev2_grab.py - harvest per-segment keys from the running player while it plays, download
the segments, decrypt them, and merge into one MPEG-TS (optionally remuxed to MP4).

Why it works this way
---------------------
The per-segment key is a 32-char lowercase-hex string that exists in the player's memory
only while it decrypts that segment for playback (the playback-context object's schedule
field). Downloading does NOT compute keys -- verified: all 1745 distinct 32-hex strings in
memory failed to decrypt a freshly downloaded segment, and player downloads are still
encrypted. So the lesson has to play once while this tool watches.

The segment manifests are also delivered progressively (30-segment pages as playback
advances), so this tool captures them live too: it hooks zlib's inflate (the response path is
AES-decrypt then inflate) and reads the plaintext pages as they arrive.

Verified decryption scheme (see EVPlayer2_解密工具与参数/EVPlayer2_分析结果.md):
    mask    = MD5(segment_filename)[:16]      -- 16 ASCII bytes, XORed cyclically
    key     = the 32-char hex string used directly as 32 ASCII bytes -> AES-256
    plain   = AES-256-ECB_decrypt(ciphertext XOR mask), strip '#' padding -> 188-byte packets

Usage
-----
  python ev2_grab.py --out ev2_out --mp4
  python ev2_grab.py --video <d_p-uuid> --out ev2_out --jobs 8
  python ev2_grab.py --from-capture captured/zlib.bin --out ev2_out

Start it, then play the lesson in EVPlayer2. It picks the lesson automatically once it sees a
harvested key that belongs to a manifest page. Create <out>/STOP (or Ctrl+C) to stop early;
the run then merges whatever is complete and exits non-zero if segments are missing.
"""
import argparse
import base64
import concurrent.futures as cf
import hashlib
import json
import os
import re
import signal
import sys
import time
import urllib.request

from Crypto.Cipher import AES
from Crypto.Util.strxor import strxor

HERE = os.path.dirname(os.path.abspath(__file__))
HEAP_FILL = bytes.fromhex('0df0adba')     # 0xBAADF00D: schedule slot before decryption

# --------------------------------------------------------------------------- player link
PLAYER_JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
var PAT = (function () {
  var hp = dll.base.add(0x802000).toString().slice(2); while (hp.length < 16) hp = '0' + hp;
  var b = []; for (var i = 0; i < 8; i++) b.push(hp.slice(14 - i * 2, 16 - i * 2));
  return ['??', '??', b[2], b[3], b[4], b[5], b[6], b[7]].join(' ');
})();

// Player string field: {inline[16] | ptr, u64 size@+16, u64 cap@+24}; cap < 16 => inline.
function readStr(obj, off) {
  try {
    var f = obj.add(off);
    var n = f.add(16).readU64().toNumber();
    var c = f.add(24).readU64().toNumber();
    if (n < 0 || n > 4096 || c < n || c > 1048576) return null;
    if (n === 0) return '';
    var d = (c < 16) ? f.readByteArray(n) : f.readPointer().readByteArray(n);
    if (!d) return null;
    var u = new Uint8Array(d), s = '';
    for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
    return s;
  } catch (e) { return null; }
}

function findExport(mod, name) {
  try { var m = Process.getModuleByName(mod); return m.findExportByName(name); } catch (e) { return null; }
}

// Manifest pages are AES-decrypted then inflated; hooking zlib yields the plaintext.
function hookZlib() {
  function one(sym, isDec) {
    var a = findExport('zlib.dll', sym);
    if (!a) return;
    Interceptor.attach(a, isDec ? {
      onEnter: function (args) { this.dest = args[0]; this.pdl = args[1]; },
      onLeave: function (rv) {
        try { if (rv.toInt32() === 0) { var n = this.pdl.readU32(); if (n > 0 && n <= 8388608) send({ ev: 'z', len: n }, this.dest.readByteArray(n)); } } catch (e) {}
      }
    } : {
      onEnter: function (args) { try { var n = args[3].toInt32(); if (n > 0 && n <= 8388608 && !args[2].isNull()) send({ ev: 'z', len: n }, args[2].readByteArray(n)); } catch (e) {} }
    });
  }
  one('uncompress', true);
  one('uncompress2', true);
  one('compress', false);
  one('compress2', false);
  var inf = findExport('zlib.dll', 'inflate');
  if (inf) Interceptor.attach(inf, {
    onEnter: function (args) { this.s = args[0]; try { this.out0 = this.s.add(16).readPointer(); this.av0 = this.s.add(24).readU32(); } catch (e) { this.out0 = null; } },
    onLeave: function () { try { if (!this.out0) return; var n = this.av0 - this.s.add(24).readU32(); if (n > 0 && n <= 8388608) send({ ev: 'z', len: n }, this.out0.readByteArray(n)); } catch (e) {} }
  });
}

rpc.exports = {
  contexts: function () {
    var out = [];
    Process.enumerateRanges('rw-').forEach(function (r) {
      if (r.size > 0x10000000) return;
      try {
        Memory.scanSync(r.base, r.size, PAT).forEach(function (m) {
          var obj = m.address;
          var file = readStr(obj, 0x18);
          if (!file || file.slice(-3) !== '.ts') return;
          var sch = '';
          try { var u = new Uint8Array(obj.add(0x120).readByteArray(32)); for (var i = 0; i < u.length; i++) sch += ('0' + u[i].toString(16)).slice(-2); } catch (e) {}
          out.push({ file: file, schedule: sch });
        });
      } catch (e) {}
    });
    return out;
  }
};

hookZlib();
send({ ev: 'ready' });
"""


def pick_pid():
    """Return the pid that actually has the player DLL loaded, or None."""
    import frida
    for p in frida.get_local_device().enumerate_processes():
        if p.name.lower() != 'evplayer2.exe':
            continue
        try:
            s = frida.attach(p.pid)
            probe = s.create_script(
                "send(!!(function(){try{Process.getModuleByName('PlayerLibRender56_vs.dll');"
                "return 1}catch(e){return 0}})());")
            ok = {'v': None}
            probe.on('message', lambda m, d: ok.update(v=m.get('payload')))
            probe.load()
            time.sleep(0.7)
            try:
                s.detach()
            except Exception:
                pass
            if ok['v']:
                return p.pid
        except Exception:
            continue
    return None


def aes_key_from_schedule(sched_hex):
    """Context stores key[0:16] verbatim; key[16:32] appears MixColumns'd."""
    if not sched_hex or len(sched_hex) != 64:
        return None
    s = bytes.fromhex(sched_hex)
    if s == b'\x00' * 32 or s[:4] == HEAP_FILL:
        return None                         # not decrypted yet

    def xt(x):
        return ((x << 1) ^ (0x1b if x & 0x80 else 0)) & 0xff

    def mix(b):
        o = bytearray(16)
        for i in range(0, 16, 4):
            t = b[i] ^ b[i + 1] ^ b[i + 2] ^ b[i + 3]
            for j in range(4):
                o[i + j] = b[i + j] ^ t ^ xt(b[i + j] ^ b[i + ((j + 1) % 4)])
        return bytes(o)

    k = bytearray(32)
    k[0:16] = s[0:16]
    k[16:32] = mix(s[16:32])
    try:
        text = bytes(k).decode('ascii')
    except UnicodeDecodeError:
        return None
    return text if re.fullmatch(r'[0-9a-f]{32}', text) else None


# --------------------------------------------------------------------------- crypto / net
def decrypt_segment(data, key_text, filename):
    if not data or len(data) % 16:
        raise ValueError('not AES-block aligned')
    mask = hashlib.md5(filename.encode()).hexdigest()[:16].encode()
    pt = AES.new(key_text.encode(), AES.MODE_ECB).decrypt(
        strxor(data, mask * (len(data) // 16)))
    pad = len(pt) % 188
    if pad and pad <= 16 and pt[-pad:] == b'#' * pad:
        pt = pt[:-pad]
    if not pt or len(pt) % 188 or any(b != 0x47 for b in pt[::188]):
        raise ValueError('not aligned MPEG-TS (wrong key or truncated data)')
    return pt


def fetch(url, attempts=3, timeout=60):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'restclient-cpp'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as ex:
            last = ex
            time.sleep(1.0 + i)
    raise last


def looks_like_full_segment(blob):
    """Cheap sanity check for a cached enc file: aligned and not obviously truncated."""
    return bool(blob) and len(blob) % 16 == 0 and len(blob) >= 188 * 2


def valid_dec_file(path):
    try:
        if os.path.getsize(path) < 188 * 2 or os.path.getsize(path) % 188:
            return False
        with open(path, 'rb') as f:
            head = f.read(188 * 3)
        return len(head) >= 188 and head[0] == 0x47 and head[188] == 0x47
    except Exception:
        return False


# --------------------------------------------------------------------------- manifests
def extract_manifests(path):
    """Pull every {d_p, k_l} JSON object out of a capture file (plaintext zlib output)."""
    if not os.path.isfile(path):
        return []
    raw = open(path, 'rb').read()
    parts = raw.split(b'\n===== ')
    buf = bytearray()
    for part in parts[1:]:
        nl = part.find(b' =====\n')
        if nl != -1 and part[:nl].startswith(b'Z-INFLATE'):
            buf += part[nl + len(b' =====\n'):]
    txt = buf.decode('utf-8', 'replace')
    out, i, n = [], 0, len(txt)
    while i < n:
        if txt[i] == '{':
            d = 0; j = i; s = False; e = False
            while j < n:
                ch = txt[j]
                if s:
                    if e: e = False
                    elif ch == chr(92): e = True
                    elif ch == '"': s = False
                else:
                    if ch == '"': s = True
                    elif ch in '{[': d += 1
                    elif ch in '}]':
                        d -= 1
                        if d == 0: break
                j += 1
            if d == 0 and j - i > 20:
                try:
                    o = json.loads(txt[i:j + 1])
                    if isinstance(o, dict) and isinstance(o.get('k_l'), list) and o.get('d_p'):
                        out.append(o)
                except Exception:
                    pass
                i = j + 1
                continue
        i += 1
    return out


def merge_manifests(manifests):
    """{d_p: {segment_filename: entry}}, preserving arrival order.

    Not keyed by idx: for a lesson being downloaded/streamed the player fetches manifests one
    segment at a time and every such response reports idx 0, so an idx key would collapse
    distinct segments into one. Arrival order is recorded so the merge can still be ordered
    when idx is not usable.
    """
    vids = {}
    seq = 0
    for m in manifests:
        d_p = m['d_p'].rstrip('/')
        if not d_p:
            continue
        slot = vids.setdefault(d_p, {})
        for e in m['k_l']:
            if not e.get('sf'):
                continue
            fn = seg_name(e)
            if not fn:
                continue
            seq += 1
            prev = slot.get(fn)
            if prev is None or len(e.get('sf', '')) > len(prev.get('sf', '')):
                merged = dict(e)
                merged['_seq'] = prev.get('_seq', seq) if prev else seq
                slot[fn] = merged
    return vids


def order_entries(slot):
    """Segment list in playback order: by idx when idx is informative, else by arrival."""
    ents = list(slot.values())
    idxs = [e.get('idx') for e in ents]
    if idxs and len(set(idxs)) == len(idxs) and all(isinstance(i, int) for i in idxs):
        ents = sorted(ents, key=lambda e: e['idx'])
    else:
        ents = sorted(ents, key=lambda e: e.get('_seq', 0))
    for i, e in enumerate(ents):
        e['_pos'] = i                      # stable output position for this segment
    return ents


def seg_name(entry):
    return entry['sf'].split('?')[0].lstrip('/')


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', action='append', default=[], help='static manifest json (repeatable)')
    ap.add_argument('--from-capture', action='append', default=[], help='captured/zlib.bin (repeatable)')
    ap.add_argument('--no-auto-capture', action='store_true',
                    help='do not auto-load ./captured/zlib.bin from a previous capture_all run')
    ap.add_argument('--video', help='d_p uuid (default: auto-detect from played lesson)')
    ap.add_argument('--out', default='ev2_out')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--poll', type=float, default=8.0, help='seconds between polls')
    ap.add_argument('--idle-limit', type=int, default=45, help='stop after this many idle polls')
    ap.add_argument('--max-attempts', type=int, default=3, help='download+decrypt tries per segment')
    ap.add_argument('--mp4', action='store_true', help='remux to mp4 with ffmpeg when complete')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    capture_path = os.path.join(args.out, 'manifests.capture')   # live zlib plaintext
    stop_file = os.path.join(args.out, 'STOP')
    keys_file = os.path.join(args.out, 'keys.json')
    if os.path.exists(stop_file):
        os.remove(stop_file)

    # static manifest sources (explicit ones, plus any earlier capture in ./captured)
    static = []
    for p in args.manifest:
        static.append(json.loads(open(p, encoding='utf-8-sig').read()))
    for p in args.from_capture:
        static += extract_manifests(p)
    if not args.no_auto_capture:
        cand = os.path.join(HERE, 'captured', 'zlib.bin')
        if os.path.isfile(cand):
            got = extract_manifests(cand)
            if got:
                print('also loaded %d manifest page(s) from %s' % (len(got), cand))
                static += got

    keys = {}
    if os.path.isfile(keys_file):
        try:
            keys = {k: v for k, v in json.load(open(keys_file)).items() if re.fullmatch(r'[0-9a-f]{32}', v)}
            print('resumed %d harvested keys from %s' % (len(keys), keys_file))
        except Exception:
            keys = {}

    import frida
    pid = pick_pid()
    if pid is None:
        print('EVPlayer2.exe with PlayerLibRender56_vs.dll is not running.')
        return 1
    session = frida.attach(pid)
    script = session.create_script(PLAYER_JS)

    state = {'zchunks': 0, 'detached': False}

    def on_message(msg, data):
        p = msg.get('payload')
        if p is None:
            return
        if p.get('ev') == 'ready':
            print('attached to EVPlayer2 pid=%d; hooks installed' % pid, flush=True)
        elif p.get('ev') == 'z' and data:
            state['zchunks'] += 1
            with open(capture_path, 'ab') as f:
                f.write(('\n===== Z-INFLATE %d =====\n' % p.get('len', 0)).encode())
                f.write(bytes(data))

    script.on('message', on_message)
    session.on('detached', lambda *a: state.update(detached=True))
    script.load()
    ex = script.exports_sync

    enc_dir = os.path.join(args.out, 'enc')
    dec_dir = os.path.join(args.out, 'dec')
    os.makedirs(enc_dir, exist_ok=True)
    os.makedirs(dec_dir, exist_ok=True)

    vids = {}
    chosen = None
    entries = []
    started = time.time()
    last_report = 0.0
    failed = {}          # fn -> (attempts, message)
    ok = set()           # fn decrypted this run
    inflight = {}
    idle = 0
    stopping = {'go': False}
    signal.signal(signal.SIGINT, lambda *a: stopping.update(go=True))

    def dec_path(e):
        return os.path.join(dec_dir, '%06d.ts' % e.get('_pos', e.get('idx', 0)))

    def refresh_manifests():
        nonlocal vids
        man = static + extract_manifests(capture_path)
        merged = merge_manifests(man)
        for d_p, slot in merged.items():
            vids.setdefault(d_p, {}).update(slot)
        return vids

    def decide_video():
        """Pick the lesson we are actually playing: the one owning a harvested key."""
        if args.video:
            for d in vids:
                if args.video in d:
                    return d
            return None
        for fn in keys:
            for d, slot in vids.items():
                if any(seg_name(e) == fn for e in slot.values()):
                    return d
        return None

    def work(e, key):
        fn = seg_name(e)
        enc_local = os.path.join(enc_dir, fn)
        blob = b''
        if os.path.isfile(enc_local):
            blob = open(enc_local, 'rb').read()
        if not looks_like_full_segment(blob):
            blob = fetch(chosen + e['sf'])
            if not looks_like_full_segment(blob):
                raise ValueError('suspicious response (%d bytes)' % len(blob))
            with open(enc_local, 'wb') as f:
                f.write(blob)
        try:
            pt = decrypt_segment(blob, key, fn)
        except ValueError:
            # cached ciphertext may be stale/corrupt: refetch once with fresh bytes
            if os.path.isfile(enc_local):
                os.remove(enc_local)
            blob = fetch(chosen + e['sf'])
            with open(enc_local, 'wb') as f:
                f.write(blob)
            pt = decrypt_segment(blob, key, fn)
        tmp = dec_path(e) + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(pt)
        os.replace(tmp, dec_path(e))
        return fn

    pool = cf.ThreadPoolExecutor(max_workers=args.jobs)

    def reap():
        progressed = False
        for fn in list(inflight):
            fut = inflight[fn]
            if not fut.done():
                continue
            del inflight[fn]
            try:
                fut.result()
                ok.add(fn)
                failed.pop(fn, None)
                progressed = True
            except Exception as ex:
                n = failed.get(fn, (0, ''))[0] + 1
                failed[fn] = (n, str(ex))
        return progressed

    try:
        while not stopping['go'] and not os.path.exists(stop_file):
            # 1) harvest keys
            if not state['detached']:
                try:
                    for c in ex.contexts():
                        k = aes_key_from_schedule(c.get('schedule'))
                        if k:
                            keys[c['file']] = k
                except Exception as ex:
                    print('player poll failed (%s); continuing with harvested keys' % ex, flush=True)
                    state['detached'] = True
            else:
                try:
                    session.detach()
                except Exception:
                    pass

            # 2) manifests (live + static)
            refresh_manifests()
            if chosen is None:
                chosen = decide_video()
                if chosen:
                    entries = order_entries(vids[chosen])
                    print('lesson %s: %d segments known so far' % (chosen.split('/')[-1], len(entries)))

            # 3) submit work for segments whose key we hold
            new_submits = 0
            if chosen:
                want = {seg_name(e): e for e in entries}
                for fn, k in keys.items():
                    e = want.get(fn)
                    if e is None or fn in inflight or fn in ok:
                        continue
                    if fn in failed and failed[fn][0] >= args.max_attempts:
                        continue
                    if valid_dec_file(dec_path(e)):
                        ok.add(fn)
                        continue
                    inflight[fn] = pool.submit(work, e, k)
                    new_submits += 1

            progressed = reap()

            # 4) bookkeeping
            if new_submits == 0 and not progressed:
                idle += 1
            else:
                idle = 0
            json.dump(keys, open(keys_file, 'w'), indent=0)

            if time.time() - last_report > 10:
                last_report = time.time()
                total = len(entries) if chosen else 0
                print('  keys=%-5d done=%-5d inflight=%-3d failed=%-4d lesson=%-6d  %.0fs' %
                      (len(keys), len(ok), len(inflight), len(failed), total, time.time() - started),
                      flush=True)
                if chosen is None:
                    print('      waiting for the lesson: open it from the course list so its '
                          'segment-list pages get fetched (%d manifest pages captured so far)'
                          % len(extract_manifests(capture_path)), flush=True)

            if chosen and total_of(entries, ok, dec_path) >= len(entries):
                print('all %d segments decrypted' % len(entries))
                break
            if idle >= args.idle_limit:
                print('idle for %d polls; stopping' % idle)
                break
            time.sleep(args.poll)
    finally:
        pool.shutdown(wait=True)
        # drain anything that finished during shutdown, so results are not lost
        for fn in list(inflight):
            fut = inflight[fn]
            try:
                fut.result()
                ok.add(fn)
            except Exception as ex:
                n = failed.get(fn, (0, ''))[0] + 1
                failed[fn] = (n, str(ex))
        json.dump(keys, open(keys_file, 'w'), indent=0)
        try:
            session.detach()
        except Exception:
            pass

    if not chosen:
        print('never identified a lesson (no harvested key matched a manifest page).')
        return 1

    entries = order_entries(vids[chosen])
    have = [e for e in entries if os.path.isfile(dec_path(e))]
    missing = [e for e in entries if not os.path.isfile(dec_path(e))]
    print('\nsegments: %d total, %d present on disk, %d missing' %
          (len(entries), len(have), len(missing)))
    for e in missing[:10]:
        print('   missing #%-4d %s' % (e.get('_pos', -1), seg_name(e)))
    if failed:
        print('permanent failures (after %d attempts):' % args.max_attempts)
        for fn, (n, msg) in list(failed.items())[:8]:
            print('   %s x%d: %s' % (fn, n, msg))
    if not have:
        print('nothing to merge')
        return 1

    complete = not missing
    name = (chosen.split('/')[-1] or 'video')[:12]
    merged = os.path.join(args.out, name + ('.ts' if complete else '.partial.ts'))
    with open(merged, 'wb') as out:
        for e in entries:
            p = dec_path(e)
            if os.path.isfile(p):
                out.write(open(p, 'rb').read())
    print('merged -> %s (%d bytes)%s' %
          (merged, os.path.getsize(merged), '' if complete else '  [INCOMPLETE]'))

    if args.mp4:
        if not complete:
            print('refusing to remux an incomplete merge; keep playing and rerun '
                  '(decrypted segments are cached, so it resumes)')
            return 1
        mp4 = os.path.join(args.out, name + '.mp4')
        rc = os.system('ffmpeg -hide_banner -nostdin -y -i "%s" -map 0:v:0 -map 0:a:0? '
                       '-c copy -movflags +faststart "%s"' % (merged, mp4))
        if rc != 0 or not os.path.isfile(mp4):
            print('ffmpeg failed (rc=%d); the merged .ts is still usable' % rc)
            return 1
        print('mp4 -> %s' % mp4)

    return 0 if complete else 1


def total_of(entries, ok, dec_path):
    return sum(1 for e in entries if seg_name(e) in ok or os.path.isfile(dec_path(e)))


if __name__ == '__main__':
    sys.exit(main())
