#!/usr/bin/env python3
"""EVPlayer2 batch downloader + decryptor.

Consumes segment manifests captured from the player, i.e. the plaintext JSON the API
returns for a video (one manifest = one page of segments):

    {"d_p": "http://cn28027.evplayer.cn/<uuid>",
     "k_l": [{"idx": 0, "sf": "/119354-<uuid>.ts?...&t=...&sign=...", "tk": "<32 hex>"}, ...]}

Segments are fetched over plain HTTP from d_p + sf, then each is decrypted with the
already-verified scheme (see EVPlayer2_解密工具与参数/EVPlayer2_分析结果.md):

    mask = MD5(segment_filename)[:16]                   (16 ASCII bytes, cyclic XOR)
    key  = MD5(tk + segment_filename + runtime_param)    (32 lowercase-hex ASCII -> AES-256)
    plaintext = AES-256-ECB_decrypt(ciphertext XOR mask), strip '#' padding

`runtime_param` (the salt) is a per-session value the player keeps in memory; recover it
with find_segments.ps1 / the player context. Decryption cannot be verified without it, so
--salt is required.

Get manifests by running capture_all.py while browsing / opening a video, then pass
--from-capture captured/zlib.bin. Manifests for one video may arrive as several pages
(segment idx ranges); pages sharing a d_p are merged automatically.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import urllib.request

from Crypto.Cipher import AES
from Crypto.Util.strxor import strxor


def extract_manifests(path):
    """Pull every {d_p, k_l} manifest out of a zlib.bin capture file."""
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
        if txt[i] in '{[':
            depth, j, instr, esc = 0, i, False, False
            while j < n:
                ch = txt[j]
                if instr:
                    if esc: esc = False
                    elif ch == chr(92): esc = True
                    elif ch == '"': instr = False
                else:
                    if ch == '"': instr = True
                    elif ch in '{[': depth += 1
                    elif ch in '}]':
                        depth -= 1
                        if depth == 0: break
                j += 1
            if depth == 0 and j - i > 20:
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


def merge_by_video(manifests):
    """Group pages by d_p and merge segment entries keyed by idx."""
    vids = {}
    for m in manifests:
        d_p = m['d_p'].rstrip('/')
        v = vids.setdefault(d_p, {})
        for e in m['k_l']:
            if 'idx' in e and e.get('sf') and e.get('tk'):
                v[e['idx']] = e
    return vids


def decrypt_segment(data, tk, filename, salt):
    mask = hashlib.md5(filename.encode()).hexdigest()[:16].encode()
    key = hashlib.md5((tk + filename + salt).encode()).hexdigest().encode()
    if len(data) % 16:
        raise ValueError("segment not AES-block aligned")
    pt = AES.new(key, AES.MODE_ECB).decrypt(strxor(data, mask * (len(data) // 16)))
    pad = len(pt) % 188
    if pad and pad <= 16 and pt[-pad:] == b'#' * pad:
        pt = pt[:-pad]
    if not pt or len(pt) % 188 or any(b != 0x47 for b in pt[::188]):
        raise ValueError("decrypted data is not aligned MPEG-TS (wrong salt?)")
    return pt


def fetch(url, retries=4):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'restclient-cpp'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as ex:
            last = ex
    raise last


def process_video(d_p, by_idx, salt, outdir, jobs):
    entries = [by_idx[k] for k in sorted(by_idx)]
    tag = (d_p.split('/')[-1] or 'video')[:12]
    enc_dir = os.path.join(outdir, tag, 'enc')
    os.makedirs(enc_dir, exist_ok=True)

    def name_of(e):
        return e['sf'].split('?')[0].lstrip('/')

    def one(e):
        fn = name_of(e)
        path = os.path.join(enc_dir, fn)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            with open(path, 'wb') as f:
                f.write(fetch(d_p + e['sf']))
        return fn

    print("video %s: %d segments" % (tag, len(entries)), flush=True)
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for i, fn in enumerate(ex.map(one, entries)):
            print("  [%d/%d] %s" % (i + 1, len(entries), fn), flush=True)

    out_ts = os.path.join(outdir, tag + '.ts')
    with open(out_ts, 'wb') as out:
        for e in entries:
            data = open(os.path.join(enc_dir, name_of(e)), 'rb').read()
            out.write(decrypt_segment(data, e['tk'], name_of(e), salt))
    print("  -> %s" % out_ts, flush=True)
    return out_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', help='single manifest JSON file (has d_p + k_l)')
    ap.add_argument('--from-capture', help='captured/zlib.bin to pull all manifests from')
    ap.add_argument('--salt', required=True, help='runtime param used in the per-segment key')
    ap.add_argument('--out', default='ev2_out', help='output directory')
    ap.add_argument('--jobs', type=int, default=8, help='parallel segment downloads')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    mans = []
    if args.manifest:
        mans.append(json.loads(open(args.manifest, encoding='utf-8-sig').read()))
    if args.from_capture:
        mans += extract_manifests(args.from_capture)
    if not mans:
        ap.error('need --manifest or --from-capture')

    vids = merge_by_video(mans)
    print("manifests: %d, unique videos: %d" % (len(mans), len(vids)))
    for d_p, by_idx in vids.items():
        try:
            process_video(d_p, by_idx, args.salt, args.out, args.jobs)
        except Exception as ex:
            print("  !! %s: %s" % (d_p.split('/')[-1], ex), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
