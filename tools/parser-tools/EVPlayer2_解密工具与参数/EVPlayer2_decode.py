"""Decode the EVPlayer2 TS variant verified for the supplied Redis lesson.

Requires Python 3 and pycryptodome (python -m pip install pycryptodome).
Usage: python EVPlayer2_decode.py --input EVPlayer2_download.zip
       --manifest redis_segments.json --output decoded.ts
Optional: ffmpeg -i decoded.ts -map 0:v:0 -map 0:a:0 -c copy -movflags +faststart lesson.mp4
The manifest contains video-specific keys. It is not a universal player key.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

def decode_segment(data, key, mask):
    from Crypto.Cipher import AES
    from Crypto.Util.strxor import strxor
    if len(key) != 32 or len(mask) != 16:
        raise ValueError('Expected a 32-byte AES key and 16-byte XOR mask')
    if not data or len(data) % 16:
        raise ValueError('Incomplete or invalid encrypted segment: length is not a multiple of 16')
    plain = AES.new(key, AES.MODE_ECB).decrypt(strxor(data, mask * (len(data) // 16)))
    padding = len(plain) % 188
    if padding > 15 or padding and plain[-padding:] != b'#' * padding:
        raise ValueError('Invalid padding: incorrect key, mismatched metadata, or corrupt segment')
    if padding:
        plain = plain[:-padding]
    if not plain or len(plain) % 188 or any(x != 0x47 for x in plain[::188]):
        raise ValueError('Decrypted data is not an aligned MPEG-TS stream')
    if any(((plain[i+3] >> 4) & 3) == 0 for i in range(0, len(plain), 188)):
        raise ValueError('Invalid MPEG-TS packet header')
    return plain

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new output filename')
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    segments = sorted(manifest['segments'], key=lambda x: x['index'])
    if [s['index'] for s in segments] != list(range(len(segments))):
        raise ValueError('Segment order has a gap or duplicate index')
    archive = zipfile.ZipFile(args.input) if args.input.is_file() else None
    try:
        members = {}
        if archive:
            for info in archive.infolist():
                if info.is_dir(): continue
                name = Path(info.filename).name
                if name in members: raise ValueError('Archive contains duplicate filenames')
                members[name] = info
        for s in segments:
            name = s['file']
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Manifest must use plain filenames')
            if archive and name not in members or not archive and not (args.input / name).is_file():
                raise ValueError('Missing segment: ' + name)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        # Each segment is validated before it is appended. Failure leaves a clearly named partial file.
        partial = args.output.with_suffix(args.output.suffix + '.partial')
        with partial.open('xb') as dest:
            for s in segments:
                data = archive.read(members[s['file']]) if archive else (args.input / s['file']).read_bytes()
                if hashlib.sha256(data).hexdigest() != s['encrypted_sha256']:
                    raise ValueError('Input fingerprint mismatch: ' + s['file'])
                plain = decode_segment(data, bytes.fromhex(s['key_hex']), bytes.fromhex(s['xor_mask_hex']))
                dest.write(plain)
                total += len(plain)
        partial.rename(args.output)
        print(json.dumps({'segments':len(segments),'decrypted_bytes':total,'output':str(args.output)},ensure_ascii=False))
    finally:
        if archive: archive.close()

if __name__ == '__main__':
    main()
