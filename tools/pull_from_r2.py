"""Pull the collections that need re-uploading back out of R2.

The local copies were deleted once they were verified on both platforms, so the bytes have to
come back from R2 before they can be re-submitted. R2 closed a connection partway through the
first attempt and killed the whole run, so each file is retried on its own and files already
present at the right size are skipped -- the run resumes instead of starting over.

    python pull_from_r2.py
"""

import pathlib
import sys
import time

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
DEST_ROOT = pathlib.Path(r"C:\Users\Yonjay\AppData\Local\Temp\wm-check\reupload")
ATTEMPTS = 4

sys.path.insert(0, str(REPO / "tools"))
import upload_to_r2  # noqa: E402

MAP = {
    "LangChain": "videos/ai/langchain/architecture-duyi-edu/",
    "LangGraph": "videos/ai/langgraph/architecture-duyi-edu/",
    "NestJS": "videos/backend/nodejs/nestjs/duyi-edu/",
    "企业级文档协同实践": "videos/frontend/doc-collaboration/duyi-edu/",
    "企业级监控平台全栈架构设计": "videos/frontend/enterprise-monitoring/duyi-edu/",
    "音视频实时互动技术": "videos/frontend/realtime-media/duyi-edu/",
    "LangChain + DeepAgent 开发实战": "videos/ai/langchain/duyi-edu/",
    "LangGraph工作流开发": "videos/ai/langgraph/duyi-edu/",
}


def main() -> int:
    s3 = upload_to_r2.client(upload_to_r2.credentials())
    grand = 0
    for name, prefix in MAP.items():
        dest = DEST_ROOT / name
        dest.mkdir(parents=True, exist_ok=True)
        objects = {k: v for k, v in upload_to_r2.list_remote(s3, "cyrus-media-private", prefix).items()
                   if k.lower().endswith(".mp4") and v[0] > 0}
        pulled = skipped = failed = 0
        for key, (size, _etag) in sorted(objects.items()):
            out = dest / key.rsplit("/", 1)[-1]
            if out.exists() and out.stat().st_size == size:
                skipped += 1
                continue
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    s3.download_file("cyrus-media-private", key, str(out))
                    pulled += 1
                    grand += size
                    break
                except Exception as error:                  # noqa: BLE001
                    if attempt == ATTEMPTS:
                        failed += 1
                        print(f"   FAILED {out.name}: {type(error).__name__}", flush=True)
                    else:
                        time.sleep(2 * attempt)
        print(f"  {name:<34} pulled={pulled} skipped={skipped} failed={failed}", flush=True)
    print(f"\ntotal newly pulled: {grand / 1024**3:.2f} GB -> {DEST_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
