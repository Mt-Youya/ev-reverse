"""Measure R2 upload throughput at a given connection width.

Reads came back at 25 MB/s direct, so the 0.9 MB/s the upload pass is achieving is either
the uplink itself or the retries. Upload a throwaway object to tmp/speedtest/ and time it;
every upload is aborted afterwards so nothing is left behind.

    python .scratch/upload-audit/upload-throughput.py --width 8 [--width 24] [--seconds 20]
"""

import argparse
import concurrent.futures
import os
import pathlib
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2 as u  # noqa: E402

BUCKET = "cyrus-media-private"
PART = 8 * 1024 ** 2
BLOCK = b"\0" * (1024 ** 2)


def probe(s3, width: int, seconds: int) -> float:
    key = f"tmp/speedtest/probe-{width}-{int(time.time())}.bin"
    upload = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = upload["UploadId"]
    sent = 0
    part_number = 0
    started = time.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
            while time.time() - started < seconds:
                numbers = list(range(part_number + 1, part_number + 1 + width))
                part_number += width
                list(pool.map(
                    lambda n: s3.upload_part(Bucket=BUCKET, Key=key, UploadId=upload_id,
                                             PartNumber=n, Body=BLOCK * 8),
                    numbers))
                sent += width * PART
    finally:
        s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id)
    return sent / (time.time() - started) / 1024 ** 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, action="append", default=None)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--proxy", action="store_true")
    args = parser.parse_args()

    if args.proxy:
        os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
    s3 = u.client(u.credentials())
    for width in (args.width or [8, 16]):
        rate = probe(s3, width, args.seconds)
        print(f"  width={width:<3} {rate:6.2f} MB/s"
              f"{'  (via proxy)' if args.proxy else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
