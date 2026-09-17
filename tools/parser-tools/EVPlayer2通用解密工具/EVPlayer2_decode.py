"""Decode a manifest produced by EVPlayer2_capture_keys.ps1."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def decode_segment(data: bytes, key: bytes, mask: bytes) -> bytes:
    from Crypto.Cipher import AES
    from Crypto.Util.strxor import strxor
    if len(key) != 32 or len(mask) != 16 or not data or len(data) % 16:
        raise ValueError("Invalid key, mask, or incomplete encrypted segment")
    plaintext = AES.new(key, AES.MODE_ECB).decrypt(strxor(data, mask * (len(data) // 16)))
    padding = len(plaintext) % 188
    if padding > 15 or (padding and plaintext[-padding:] != b"#" * padding):
        raise ValueError("Invalid segment padding")
    if padding:
        plaintext = plaintext[:-padding]
    if not plaintext or len(plaintext) % 188 or any(value != 0x47 for value in plaintext[::188]):
        raise ValueError("Decrypted segment is not aligned MPEG-TS")
    if any(((plaintext[offset + 3] >> 4) & 3) == 0 for offset in range(0, len(plaintext), 188)):
        raise ValueError("Invalid MPEG-TS packet header")
    return plaintext


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path, help="EVPlayer2 ZIP or extracted segment directory")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a different name")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    segments = sorted(manifest.get("segments", []), key=lambda item: item["index"])
    if not segments or [item["index"] for item in segments] != list(range(len(segments))):
        raise ValueError("Manifest segment order is incomplete or invalid")
    archive = zipfile.ZipFile(args.input) if args.input.is_file() else None
    try:
        members = {}
        if archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                name = Path(entry.filename).name
                if name in members:
                    raise ValueError(f"Duplicate archive member: {name}")
                members[name] = entry
        for item in segments:
            name = item["file"]
            if Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError("Manifest contains an unsafe filename")
            if archive and name not in members:
                raise ValueError(f"Missing segment: {name}")
            if not archive and not (args.input / name).is_file():
                raise ValueError(f"Missing segment: {name}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        partial = args.output.with_suffix(args.output.suffix + ".partial")
        written = 0
        with partial.open("xb") as target:
            for item in segments:
                data = archive.read(members[item["file"]]) if archive else (args.input / item["file"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != item["encrypted_sha256"]:
                    raise ValueError(f"Input fingerprint mismatch: {item['file']}")
                plaintext = decode_segment(data, bytes.fromhex(item["key_hex"]), bytes.fromhex(item["xor_mask_hex"]))
                target.write(plaintext)
                written += len(plaintext)
        partial.rename(args.output)
        print(json.dumps({"segments": len(segments), "decrypted_bytes": written, "output": str(args.output)}, ensure_ascii=False))
    finally:
        if archive:
            archive.close()


if __name__ == "__main__":
    main()
