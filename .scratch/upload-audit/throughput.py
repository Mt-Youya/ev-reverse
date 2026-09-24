"""Measure R2 throughput direct and through the local proxy, at a fixed width.

The upload pass runs at ~0.9 MB/s while the same code measured 3.8 MB/s earlier, and the
difference between those two runs is the proxy: 127.0.0.1:7897 was in the environment then
and is stripped now (a global proxy is wrong for the Bilibili half, which is domestic).
Downloading the same objects both ways answers it without touching the running pass's
upload path.

    python .scratch/upload-audit/throughput.py [--width 8] [--seconds 20]
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

PROXY = "http://127.0.0.1:7897"


def probe(proxy: str | None, width: int, seconds: int) -> float:
    if proxy:
        os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"] = proxy
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY", None)
    import importlib
    importlib.reload(u)
    s3 = u.client(u.credentials())
    keys = [k for k, (size, _e) in u.list_remote(s3, "cyrus-media-private",
                                                "videos/ai/langchain/duyi-edu/").items()
            if 6 * 1024 ** 2 < size < 40 * 1024 ** 2]
    if not keys:
        raise SystemExit("no objects of a suitable size")

    total = 0
    started = time.time()
    stop = started + seconds

    def fetch(key: str) -> int:
        body = s3.get_object(Bucket="cyrus-media-private", Key=key)["Body"]
        got = 0
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            got += len(chunk)
        return got

    with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
        while time.time() < stop:
            batch = [keys[i % len(keys)] for i in range(width)]
            total += sum(pool.map(fetch, batch))
    elapsed = time.time() - started
    return total / elapsed / 1024 ** 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument("--seconds", type=int, default=20)
    args = parser.parse_args()

    for label, proxy in (("direct", None), ("proxy", PROXY)):
        rate = probe(proxy, args.width, args.seconds)
        print(f"  {label:<7} width={args.width}  {rate:6.2f} MB/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
