# R2 上传：目录约定与避坑清单

写给以后的 agent。R2 是 Cloudflare，**不是国内对象存储**，所以这里的坑集中在两处：
**目录该建在哪**（名字本身有歧义）和**并发该开多大**（多开会大面积 TLS 失败）。

一句话：

> **前缀靠文件名交集证明，不靠名字猜；总并发保持约 16 条连接；B站 和 R2 不要同时传。**

---

## 1. 桶与工具

| 项 | 值 |
| --- | --- |
| 桶 | `cyrus-media-private` |
| 账号 | `b48fdde62898699d8398a64c19217cd4` |
| 凭据 | Windows 凭据管理器（`cf-evmedia-r2_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY`，经 `build/CredRead.exe` 读取）。**没有任何密钥落盘或打印** |
| 上传 | `tools/upload_to_r2.py`（单合集）/ `tools/r2_upload_all.py`（一次传全部已导出的合集） |

```powershell
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\upload_to_r2.py `
    --local "verify_fresh\out\<合集>" --prefix "videos/<分类>/<主题>/duyi-edu/" --include-images [--apply]
```

`--include-images` 让 `封面/*.png` 也一起传（key = 前缀 + 相对路径，所以封面落在 `<前缀>/封面/`，与既有约定一致）。
不加 `--apply` 就是 dry run。

---

## 2. 目录约定

```
videos/<分类>/<主题>[/<子主题>]/duyi-edu/
```

- 全英文、小写、短横线分词：`frontend/engineering-management`、`frontend/micro-frontend`、`ai/langchain`。
- 同一门课有「架构课」和「实战课」两个版本时，用后缀区分：`videos/ai/langchain/duyi-edu/` 与
  `videos/ai/langchain/architecture-duyi-edu/`。
- **章节按本地结构保留**：带章节目录的课传成 `<前缀>/第一章 课程概述/01. xxx.mp4`，
  与 `videos/ai/langgraph/architecture-duyi-edu/第二章 快速入门/` 一致；不带章节的就平铺。
- 视频文件名保持中文原名。

---

## 3. ⚠️ 给一门课定前缀：靠文件名交集，不要靠名字猜

**前缀名是有歧义的。** 实例（2026-09-24 实测）：

| 前缀 | 名字看起来像 | 实际属于 | 判据 |
| --- | --- | --- | --- |
| `videos/frontend/scaffolding/duyi-edu/` | 「脚手架技术内幕」 | **新自定义脚手架** | 9 个本地文件里 7 个已在该前缀 |
| `videos/frontend/react/duyi-edu/` | React 源码课 | **两者都不是** | 里面的 `2-2-React和Vue描述页面的区别-rust.mp4` **不在 catalog 里** |
| `videos/frontend/gsap/duyi-edu/` | GSAP 课 | **不属于任何 catalog 课程** | 同上（`01-动画方法.mp4`） |
| `videos/backend/nodejs/koa/duyi-edu/` | Koa 课 | **不属于任何 catalog 课程** | 同上（`02-koa-api.mp4`） |

**这些是别的来源的文件，绝不可拿来当某门课的「家」—— 会把课程内容混进不相干的目录。**

**正确做法**：拿本地合集的**文件名集合**去和每个前缀的**对象名集合**求交集，有重叠才说明归属。
命令见 `r2_upload_all.py` 的 `PLAN` 注释；判定不是「有没有重叠」就够 —— 新合集本来就没重叠，
所以还要拿 **catalog** 核对：`verify_fresh/catalog.json` 里没有的文件名，说明那目录不是这门课的。

配对结果记录在两处（保持同步）：`tools/r2_upload_all.py` 的 `PLAN` 和 `tools/clean_work.py` 的
`R2_BY_COLLECTION`。

---

## 4. ⚠️ 并发：约 16 条连接是甜点，超了会大面积失败

**R2 按「每条连接」限速，不按账号。** 用合成小对象实测（2026-09-24）：

| 同时在飞的请求数 | 吞吐 |
| --- | --- |
| 1 | 0.24 MB/s |
| 4 | 0.33 MB/s |
| **16** | **3.79 MB/s** |
| 32 | 2.81 MB/s |

所以默认每文件 4 分片时，**单文件合集只有 4 条连接 → 0.3 MB/s**，一天的活。

**但并发是相乘的**：`MAX_CONCURRENCY`（同时几个对象）× `PART_CONCURRENCY`（每对象几个分片）。
一度把它设成 4 × 16 = **64 条并发 TLS 握手**，结果一次 pass 失败三分之一：

```
CSS企业应用方案   15/27 上传成功，12 失败
企业级监控平台     3/7，  4 失败
LangChain(架构课)  2/6，  4 失败
LangGraph(架构课)  6/9，  3 失败
——全部是 "SSL validation failed"
```

**现在的取值：`MAX_CONCURRENCY = 2`，`PART_CONCURRENCY = 8`，乘积 16 —— 正好是实测甜点。**

除此外还有**整对象级重试**（`MAX_ATTEMPTS = 3`，每次重试前递增停顿）。
重试才是恢复失败文件的手段；降并发只是别再制造失败。

---

## 5. ⚠️ B站 和 R2 不要同时传

上面那波 SSL 失败，环境上正好赶上 B站 上传 pass 同时在进行。
两个上传都是网络密集的，互相抢连接与带宽，**实测会诱发 TLS 失败**。
`tools/watch_and_upload.py` 因此加了互斥：任一方在跑就跳过整轮。

---

## 6. 会骗你的检查

| 看起来 | 其实 |
| --- | --- |
| `list_objects_v2` 的对象数没变 | 多段上传**整体完成时**对象才出现，中途计数不变 |
| 用「进行中的分片」算速率 | 分片完成会从 `list_multipart_uploads` 消失，会算出**负数** |
| 看到失败就以为全丢了 | 已成功的不受影响；重跑会按 MD5 跳过，只补失败的那些 |

**正确的进度测法**：`已完成对象字节 + 在飞分片字节`，对时间求差分。

**被中断的上传会留下孤儿多段上传**（白占存储），清理方式：

```python
s3.list_multipart_uploads(Bucket=..., Prefix='videos/')  # 对每个 UploadId 调 abort_multipart_upload
```

---

## 7. 相关文档

- `docs/BILIBILI-UPLOAD.md` —— B站 侧（水印、分P 顺序、批量投稿页的陷阱）
- `docs/BROWSER-AUTOMATION.md` —— 浏览器自动化通用避坑
