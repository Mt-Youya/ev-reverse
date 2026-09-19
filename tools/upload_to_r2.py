"""Uploads a lesson folder to R2, skipping objects the bucket already holds.

This exists because the REST endpoint cannot take the files: a 320 MB PUT comes back
`413 Payload Too Large`, and R2's REST API has no multipart routes to work around it (every multipart
path 404s). Five of the lessons here are over 300 MB and one is 1141 MB, so the upload has to go
through the S3-compatible API, which means SigV4 credentials.

"Already there" is decided by MD5, not by name. An R2 object's ETag is the MD5 of what was uploaded
whenever the upload was a single PUT, and a match means the bytes are the same -- a same-named object
with different bytes is re-uploaded rather than trusted.

Credentials are looked for in two places, in this order:

  1. `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` in the environment. These only live
     as long as the shell that set them, which is how the first attempts lost them.
  2. Windows Credential Manager, under `cf-evmedia-r2_ACCESS_KEY_ID` and
     `cf-evmedia-r2_SECRET_ACCESS_KEY` -- DPAPI-encrypted, readable only by this account. Read
     through `build/CredRead.exe`, built from `tools/CredRead.cs`.

No secret is written to disk or printed.

    python upload_to_r2.py --local <dir> --prefix <key prefix> [--apply] [--only <name>]

Without `--apply` it uploads nothing and prints the plan.
"""

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

ACCOUNT_ID = "b48fdde62898699d8398a64c19217cd4"
CRED_TARGET = "cf-evmedia-r2_{}"
CRED_READER = pathlib.Path(__file__).resolve().parent.parent / "build" / "CredRead.exe"
CHUNK = 8 * 1024 * 1024          # multipart part size
MAX_CONCURRENCY = 4              # objects in flight; each one is itself multipart
MIB = 1024 * 1024


def from_credential_manager(name: str):
    if not CRED_READER.exists():
        return None
    result = subprocess.run([str(CRED_READER), CRED_TARGET.format(name)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def credentials() -> dict:
    account = os.environ.get("R2_ACCOUNT_ID") or ACCOUNT_ID
    access = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    source = "environment"
    if not (access and secret):
        source = "Windows Credential Manager"
        access = access or from_credential_manager("ACCESS_KEY_ID")
        secret = secret or from_credential_manager("SECRET_ACCESS_KEY")
    if not (access and secret):
        sys.exit(
            "no S3 credentials found.\n"
            "  looked for R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY in the environment, then for\n"
            f"  'cf-evmedia-r2_ACCESS_KEY_ID' / 'cf-evmedia-r2_SECRET_ACCESS_KEY' via {CRED_READER}\n")
    print(f"credentials: {source}")
    return {"account": account, "access": access, "secret": secret}


def client(creds: dict):
    return boto3.client(
        "s3",
        endpoint_url=f"https://{creds['account']}.r2.cloudflarestorage.com",
        aws_access_key_id=creds["access"],
        aws_secret_access_key=creds["secret"],
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                      max_pool_connections=MAX_CONCURRENCY * 2),
    )


def list_remote(s3, bucket: str, prefix: str) -> dict:
    """key -> (size, etag without quotes) for everything already under `prefix`."""
    found: dict[str, tuple[int, str]] = {}
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for item in page.get("Contents", []):
            found[item["Key"]] = (item["Size"], item["ETag"].strip('"'))
        if not page.get("IsTruncated"):
            return found
        token = page["NextContinuationToken"]


def md5_of(path: pathlib.Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * MIB), b""):
            digest.update(block)
    return digest.hexdigest()


def multipart_etag(path: pathlib.Path, part_size: int = CHUNK) -> str:
    """The ETag a multipart upload of this file produces: md5 of the part md5s, then `-N`."""
    digests = []
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(part_size), b""):
            digests.append(hashlib.md5(block).digest())
    joined = hashlib.md5(b"".join(digests)).hexdigest()
    return f"{joined}-{len(digests)}"


def already_there(path: pathlib.Path, size: int, etag: str) -> bool:
    if path.stat().st_size != size:
        return False
    if etag.endswith("-"):        # defensive: R2 does not emit these, but do not guess if it does
        return False
    return md5_of(path) == etag or multipart_etag(path) == etag


def upload(s3, bucket: str, path: pathlib.Path, key: str, progress: dict, lock: threading.Lock):
    config = TransferConfig(multipart_threshold=CHUNK, multipart_chunksize=CHUNK,
                            max_concurrency=4, use_threads=True)
    s3.upload_file(str(path), bucket, key, Config=config)
    with lock:
        progress["done"] += 1
        progress["bytes"] += path.stat().st_size
        print(f"  [{progress['done']}/{progress['total']}] uploaded {path.name} "
              f"({path.stat().st_size / MIB:.1f} MB)  "
              f"running total {progress['bytes'] / 1024 / MIB:.2f} GB", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", type=pathlib.Path, required=True)
    parser.add_argument("--bucket", default="cyrus-media-private")
    parser.add_argument("--prefix", required=True, help="key prefix, with trailing slash")
    parser.add_argument("--apply", action="store_true", help="actually upload; default is a dry run")
    parser.add_argument("--only", help="substring filter on file name, for a single-file retry")
    parser.add_argument("--exact-key",
                        help="upload the single matched file under exactly this key. Needed when the "
                             "key is not `prefix + path relative to --local`: passing the file's own "
                             "folder as --local silently flattens the key, which is how the cover "
                             "images ended up one level too high.")
    parser.add_argument("--include-images", action="store_true",
                        help="also upload non-video files such as the cover pngs")
    args = parser.parse_args()

    prefix = args.prefix if args.prefix.endswith("/") else args.prefix + "/"
    files = sorted(p for p in args.local.rglob("*") if p.is_file())
    if not args.include_images:
        files = [p for p in files if p.suffix.lower() in (".mp4", ".mkv", ".ts")]
    if args.only:
        files = [p for p in files if args.only in p.name]
    if not files:
        sys.exit(f"no files under {args.local} matched")
    if args.exact_key and len(files) != 1:
        sys.exit(f"--exact-key needs exactly one file, {len(files)} matched")

    total_bytes = sum(p.stat().st_size for p in files)
    print(f"local: {len(files)} file(s), {total_bytes / 1024 / MIB:.2f} GB under {args.local}")
    print(f"target: s3://{args.bucket}/{args.exact_key or prefix}")
    print(f"mode: {'UPLOAD' if args.apply else 'dry run (pass --apply to upload)'}\n")

    s3 = client(credentials())
    remote = list_remote(s3, args.bucket, prefix)
    print(f"bucket already holds {len(remote)} object(s) under this prefix\n")

    todo, skip = [], []
    for path in files:
        # Keep the relative path so a nested folder does not collapse into flat keys.
        key = args.exact_key or (prefix + path.relative_to(args.local).as_posix())
        entry = remote.get(key)
        if entry and already_there(path, *entry):
            skip.append((path, key))
        else:
            why = "not in bucket" if not entry else (
                f"size differs ({entry[0]} vs {path.stat().st_size})" if entry[0] != path.stat().st_size
                else "same size, different bytes")
            todo.append((path, key, why))

    print(f"SKIP  {len(skip)} already present and identical")
    for path, key in skip:
        print(f"   = {key}  ({path.stat().st_size / MIB:.1f} MB)")
    print(f"\nUPLOAD {len(todo)} file(s), {sum(p.stat().st_size for p, _, _ in todo) / 1024 / MIB:.2f} GB")
    for path, key, why in todo:
        print(f"   + {key}  ({path.stat().st_size / MIB:.1f} MB)  [{why}]")

    if not args.apply:
        print("\ndry run: nothing uploaded")
        return 0
    if not todo:
        print("\nnothing to do")
        return 0

    print(f"\nuploading with {MAX_CONCURRENCY} files in flight...", flush=True)
    progress = {"done": 0, "bytes": 0, "total": len(todo), "failed": []}
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        futures = {pool.submit(upload, s3, args.bucket, path, key, progress, lock): (path, key)
                   for path, key, _ in todo}
        for future in as_completed(futures):
            path, key = futures[future]
            try:
                future.result()
            except Exception as error:
                with lock:
                    progress["failed"].append((path, key, str(error)[:200]))
                print(f"  FAILED {key}: {str(error)[:160]}", flush=True)

    print(f"\nuploaded {progress['done']}/{progress['total']}, "
          f"{progress['bytes'] / 1024 / MIB:.2f} GB, {len(progress['failed'])} failed")
    for path, key, error in progress["failed"]:
        print(f"   ! {key}: {error}")

    # Verify against the bucket, not against the upload calls.
    print("\nverifying against the bucket...")
    after = list_remote(s3, args.bucket, prefix)
    bad = []
    for path, key, _ in todo:
        entry = after.get(key)
        if not entry:
            bad.append((key, "missing after upload"))
        elif entry[0] != path.stat().st_size:
            bad.append((key, f"size {entry[0]} != {path.stat().st_size}"))
    print(f"verified {len(todo) - len(bad)}/{len(todo)}")
    for key, why in bad:
        print(f"   ! {key}: {why}")
    return 1 if (progress["failed"] or bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
