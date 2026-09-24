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

配对结果记录在两处（保持同步）：`tools/clean_work.py` 的 `R2_BY_COLLECTION` 和
`BILI_BY_COLLECTION`（`tools/verify_and_delete.py` 与 `tools/r2_upload_all.py` 都从这里取，
不再各写一份 —— 各写一份的结果就是漏掉新合集，永远清理不掉）。

**表里有前缀 ≠ 桶里有东西。** `微前端` 和 `微前端架构实战` 的前缀是「按约定预留」的，
桶里一个对象都没有（`videos/frontend/micro-frontend/` 整个目录都不存在）。所以：

- 判断「这门课在不在 R2」要看**对象**，不要看表；
- 审计脚本：`.scratch/upload-audit/prefix-overlap.py`（拿课程的分集标题去和每个前缀的对象名
  求重叠，输出「表里的前缀」与「真正的归属前缀」是否一致）。

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

### 代理：R2 不要走 Clash

实测（2026-09-24，8 条并发）：

| 方向 | 直连 | 经 `127.0.0.1:7897` |
| --- | --- | --- |
| 读（GET） | **24.9 MB/s** | 6.7 MB/s |
| 写（PUT，8 连接） | 0.35 MB/s（与 B站 pass 同时跑） | 连不上/卡住 |

R2 的出网直连是快的，代理只会更慢。**但别把 `HTTPS_PROXY` 设成全局**：B站 是国内的，
走了代理会掉速（实测 6.2 → 1.12 MB/s，并报 `tls handshake eof`）。
需要时只给 R2 的子进程单独设。

---

## 5. ⚠️ B站 和 R2 共用一条上行带宽

之前写的是「B站 和 R2 不要同时传」。更准确的说法是：**两个上传抢同一条上行**，实测（2026-09-24）：

| 状态 | R2 侧速率 | 全网卡上行 |
| --- | --- | --- |
| B站 pass 在跑 | 0.35 ~ 0.9 MB/s | 5.2 ~ 6.5 MB/s |
| B站 pass 结束 | **5.66 MB/s** | 5.2 ~ 6.5 MB/s |

上行总量约 6.5 MB/s，B站 是国内的、能把它吃满，R2 只能捡剩下的。
所以「R2 慢」先看 B站 是否在跑，别急着调并发。

那波 SSL 失败的主因是 **64 条并发连接**（见上一节），不是两者共存本身。
`tools/watch_and_upload.py` 现在**分别互斥**：R2 只在没有 R2 pass 时跑，B站 不受 R2 影响
—— R2 一轮要跑几小时，整轮跳过会让新导出的合集一直排队。

---

## 5.1 空间：删本地的前提是「两个平台都验过字节」

`tools/verify_and_delete.py` 的判据是**双证据**：R2 侧按上传器自己的规则比 MD5（单段 PUT 比
ETag，多段比 `-N` 的分片摘要），B站 侧要求分P 标题相同**且时长差 ≤ 3 秒**
（时长是 B站 报的，光靠标题同名的另一个视频骗不过）。

一次 dry run 报 155 个文件 / 32.5 GB 可删 —— 这批空间就是继续下载缺集的本钱。
**不要按标题或「看起来传完了」删**：`out/` 里 135 个文件当时正是「只差 R2」的状态。


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
