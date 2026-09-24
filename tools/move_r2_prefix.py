"""Move every object under one R2 prefix to another, entirely server-side.

CopyObject runs inside R2, so nothing is downloaded, no egress is billed, and the bytes never
pass through this machine. The order is copy -> verify -> delete, never delete-first: an
interrupted run leaves the source intact and can simply be repeated.

Three rules the loop exists to enforce:

  * The destination is never overwritten. CopyObject would clobber a same-named key without a
    word, so an existing key is compared first -- identical size and ETag means an earlier run
    already copied it and only the source delete is outstanding, while a mismatch is reported
    as a conflict and left alone for a human to settle.
  * A delete only follows a verify. The destination is re-read with head_object and must match
    the source's size and ETag; anything else aborts that object with the source still there.
  * Interruption is resumable. Finished source keys are appended to a progress file, and a
    re-run skips them -- necessary because a pass over a course is minutes long and the machine
    is also exporting while it runs.

    python move_r2_prefix.py --src <prefix> --dst <prefix> [--apply] [--workers 5]

Without --apply it prints the plan and touches nothing.

Credentials come from upload_to_r2.credentials(): R2_* in the environment, else the
DPAPI-encrypted pair in Windows Credential Manager under cf-evmedia-r2_*. No secret is read
from this file or written to disk.
"""

import argparse
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2  # noqa: E402

BUCKET = "cyrus-media-private"
MAX_ATTEMPTS = 3
DEFAULT_WORKERS = 5
DEFAULT_PROGRESS = pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\move-r2-progress.json")


def list_prefix(s3, prefix: str) -> dict:
    """key -> (size, etag) for everything under `prefix`, following pagination."""
    paginator = s3.get_paginator("list_objects_v2")
    found = {}
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for item in page.get("Contents", []):
            found[item["Key"]] = (item["Size"], item["ETag"].strip('"'))
    return found


def move_one(s3, src_key: str, dst_key: str, dst_seen: dict, outcome: dict, lock: threading.Lock):
    """Copy one object, verify it, then delete the source. Records the outcome."""
    want = outcome["src"][src_key]

    def record(kind, detail=""):
        with lock:
            outcome[kind].append((src_key, dst_key, detail))

    # An identical object already at the destination is a resumed run, not a conflict.
    # Zero-byte objects (the `duyi-edu/` directory markers) all share one ETag, so size and
    # ETag agreeing is the whole test and is enough for them too.
    existing = dst_seen.get(dst_key)
    if existing is not None:
        if existing[0] == want[0] and existing[1] == want[1]:
            try:
                s3.delete_object(Bucket=BUCKET, Key=src_key)
            except Exception as error:  # noqa: BLE001
                record("failed", f"delete after resume: {error}")
                return
            record("resumed", f"already at destination ({want[0]} bytes)")
            return
        record("conflict", f"destination holds {existing[0]} bytes / {existing[1][:12]}, "
                           f"source is {want[0]} bytes / {want[1][:12]} -- not overwritten")
        return

    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # CopySource is passed unquoted on purpose: boto3 URL-encodes it itself, and
            # quoting here as well turned 中文 into %25E5%25... and the copy came back NoSuchKey.
            s3.copy_object(Bucket=BUCKET, Key=dst_key,
                           CopySource={"Bucket": BUCKET, "Key": src_key},
                           MetadataDirective="COPY")
            head = s3.head_object(Bucket=BUCKET, Key=dst_key)
            if head["ContentLength"] != want[0]:
                raise RuntimeError(f"size {head['ContentLength']} != {want[0]} after copy")
            got_etag = head["ETag"].strip('"')
            if got_etag != want[1]:
                raise RuntimeError(f"etag {got_etag[:12]} != {want[1][:12]} after copy")
            s3.delete_object(Bucket=BUCKET, Key=src_key)
            record("moved")
            return
        except Exception as error:  # noqa: BLE001
            last = str(error)[:300]
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** (attempt - 1))
    record("failed", f"after {MAX_ATTEMPTS} attempt(s): {last}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, help="source prefix, e.g. videos/a/b/")
    parser.add_argument("--dst", required=True, help="destination prefix, e.g. videos/a/c/")
    parser.add_argument("--apply", action="store_true", help="actually move; default is a dry run")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--progress", type=pathlib.Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--no-progress", action="store_true",
                        help="ignore and do not write the resume file")
    args = parser.parse_args()

    src = args.src if args.src.endswith("/") else args.src + "/"
    dst = args.dst if args.dst.endswith("/") else args.dst + "/"
    if dst.startswith(src) or src.startswith(dst):
        sys.exit("refusing to run: the prefixes overlap, which would move objects onto themselves")

    s3 = upload_to_r2.client(upload_to_r2.credentials())

    done = set()
    if not args.no_progress and args.progress.exists():
        try:
            done = set(json.loads(args.progress.read_text(encoding="utf-8")))
            print(f"resume file lists {len(done)} already-moved object(s)")
        except (json.JSONDecodeError, OSError) as error:
            print(f"resume file unreadable ({error}); starting from the top")

    source = list_prefix(s3, src)
    destination = list_prefix(s3, dst)
    pending = {k: v for k, v in source.items() if k not in done}
    total_bytes = sum(v[0] for v in pending.values())

    print(f"\nsource      {src}")
    print(f"destination {dst}")
    print(f"planned     {len(source)} object(s) under source, {len(pending)} not yet moved, "
          f"{total_bytes / 1024 ** 3:.2f} GB")
    print(f"destination already holds {len(destination)} object(s)")

    if not pending:
        print("\nnothing to move")
        return 0

    if not args.apply:
        for key in sorted(pending):
            print(f"    {pending[key][0]:>12}  {key}\n        -> {dst + key[len(src):]}")
        print(f"\ndry run: nothing copied, nothing deleted "
              f"({len(pending)} object(s), {total_bytes / 1024 ** 3:.2f} GB)")
        return 0

    outcome = {"moved": [], "resumed": [], "conflict": [], "failed": [], "src": source}
    lock = threading.Lock()
    started = time.time()
    processed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(move_one, s3, key, dst + key[len(src):], destination, outcome, lock): key
                   for key in sorted(pending)}
        for future in as_completed(futures):
            key = futures[future]
            future.result()
            processed += 1
            with lock:
                if processed % 5 == 0 or processed == len(pending):
                    print(f"  [{processed}/{len(pending)}]  moved {len(outcome['moved'])}  "
                          f"resumed {len(outcome['resumed'])}  "
                          f"conflict {len(outcome['conflict'])}  "
                          f"failed {len(outcome['failed'])}", flush=True)
            if not args.no_progress and processed % 5 == 0:
                with lock:
                    moved_keys = [k for k, _d, _x in outcome["moved"] + outcome["resumed"]]
                args.progress.write_text(json.dumps(sorted(done | set(moved_keys))),
                                         encoding="utf-8")

    finished = [k for k, _d, _x in outcome["moved"] + outcome["resumed"]]
    if not args.no_progress:
        args.progress.write_text(json.dumps(sorted(done | set(finished))), encoding="utf-8")

    moved_bytes = sum(source[k][0] for k in finished)
    elapsed = time.time() - started

    print(f"\n{'=' * 72}")
    print(f"moved      {len(outcome['moved'])}")
    print(f"resumed    {len(outcome['resumed'])}  (already at destination, source deleted)")
    print(f"conflict   {len(outcome['conflict'])}  (left alone, not overwritten)")
    print(f"failed     {len(outcome['failed'])}")
    print(f"data       {moved_bytes / 1024 ** 3:.2f} GB of {total_bytes / 1024 ** 3:.2f} GB")
    print(f"elapsed    {elapsed:.1f}s")
    for kind in ("conflict", "failed"):
        for src_key, dst_key, detail in outcome[kind]:
            print(f"  ! {kind.upper()} {src_key}\n      -> {dst_key}\n      {detail}")

    left = list_prefix(s3, src)
    print(f"\nsource now holds {len(left)} object(s)"
          + ("" if not left else ":"))
    for key in sorted(left):
        print(f"    {left[key][0]:>12}  {key}")
    arrived = list_prefix(s3, dst)
    print(f"destination now holds {len(arrived)} object(s), "
          f"{sum(v[0] for v in arrived.values()) / 1024 ** 3:.2f} GB")
    return 1 if outcome["failed"] or outcome["conflict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
