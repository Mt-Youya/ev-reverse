# 导出（下载成片）：阶段、缓存与避坑清单

写给以后的 agent。上传侧的三份文档是 `docs/R2-UPLOAD.md`、`docs/BILIBILI-UPLOAD.md`、
`docs/BROWSER-AUTOMATION.md`；这份管**成片是怎么来的**。

一句话：

> **`work/` 里的 `lesson.ts` 就是成品的一半——先查缓存再下载；一次只跑一个导出；
> 平台自己的标题可能带双扩展名，别被它骗。**

---

## 1. 工具与调用形状

导出由 `target/release/evmedia.exe export-evs` 完成（GUI `evmedia-gui.exe` 也是调它）：

```powershell
& '.\target\release\evmedia.exe' export-evs `
    --session 'verify_fresh\session.json' `
    --account 119354 --course 280626 --file 81102 `
    --output 'verify_fresh\out\前端架构课程\前端工具链\第三章 ESLint\01. ESLint介绍.mp4' `
    --work 'verify_fresh\work\280626-81102' --jobs 8
```

| 约定 | 值 |
| --- | --- |
| 会话 | `verify_fresh/session.json`（过期会报 `-123: "长时间未登录，请退出重新登录"`） |
| 账号 | `119354`（`index.json` 的 `account_id`） |
| `--course` | 平台课程 id（`index.json` 的 key） |
| `--file` | 平台文件 id（`episodes[].file_id`） |
| `--output` | `out/<分类>/<课程>[/<章节>]/<标题>.mp4` |
| `--work` | **`work/<course>-<file>`**，名字就是两个 id，一个目录对应一集 |

`tools/export_missing.py` 负责算这些路径并批量跑：

```powershell
python tools/export_missing.py                 # dry run：列出「平台缺、本地也没有」的集
python tools/export_missing.py --apply         # 真下载
python tools/export_missing.py --from-cache --apply   # 只发布缓存里已合并好的集（见 §3）
```

---

## 2. 四个阶段，以及为什么值得分开

`--phase` 可选 `all / download / convert / merge / publish`：

| 阶段 | 干什么 | 需要什么 |
| --- | --- | --- |
| `download` | 拉分段到 `work/`（密文 + `manifest.json` 里的 key/xor） | 网络 |
| `merge` | 解密、拼接成 `work/<id>/lesson.ts` | 分段缓存 |
| `publish` | **完整解码校验**音视频，再 remux 成 `--output` | `lesson.ts` |
| `convert` | `merge` + `publish` 的向后兼容写法 | 分段缓存 |

**缓存是复用的，而且发布不会毁掉它**（发布后 `lesson.ts` 仍在）。所以：
»先 `--from-cache` 把能发布的都发布掉，再下载剩下的«——一次 `publish` 是一分钟级的解码，
一次下载是分钟级到十几分钟的传输。

---

## 3. ⚠️ `work/` 里躺着一整批「已下载已合并但从未发布」的课

2026-09-24 实测：11 门课（Electron 63 集、React源码分析 34、文档协同 29、脚手架技术内幕 15、
运维自动化项目实战 14、vite深度剖析 13、前端服务监控 10、rollup 9、国际化 9、企业级包管理方案 8、
esbuild 7，共 241 集）在 `work/` 里有完整的 `lesson.ts`，`out/` 里一个成片都没有，
两平台也没有任何映射 —— 状态页把整门课报成「从未导出」。

**判断方法**（不用猜）：

```python
# work/<course>-<file>/lesson.ts 存在 且 out/<...>/<标题>.mp4 不存在  →  可发布
```

`export_missing.py --from-cache` 就是这么筛的。发布一门课之后别忘了给它**建表**：
`clean_work.R2_BY_COLLECTION`（R2 前缀）+ `BILI_BY_COLLECTION`（稿件 id，没有就先写 `[]`）
+ `bili_upload.COLLECTIONS`（标题与标签），否则新成片会一直躺在 `out/` 里没人传。

`clean_work.py --apply` 会删掉「双平台已核验」的 `work/` 目录；**没核验过的绝不碰**，
因为那正是上面这批课的来源。一次实测释放 29.89 GB。

---

## 4. ⚠️ 平台标题可能是双扩展名

`工程管理实战` 的第 35 集在平台上就叫：

```
file_id 982963   标题 "35. [测试体系]单元测试.mp4.mp4"
```

于是：导出文件名带双扩展名 → 上传时 `norm()` 只剥一层 → 与稿件里的分P 名不相等 →
**每轮上传都补一个分P**（实测长到 3 条，见
`.scratch/bilibili-upload/issues/02-delete-the-duplicate-unit-test-parts.md`）。

两个 `norm()`（`tools/bili_upload.py`、`tools/courses_json.py`）现在都剥离**重复**扩展名；
`export_missing.py` 写出文件时也把重复扩展名收成一个。
**新写任何和分P/对象名比较的代码，都要用同样的规则。**

---

## 5. ⚠️ 有的集导不出来，而且可复现

`前端工具链 / 01. ESLint介绍`（file 81102）：

```
Error: 无法唯一确定视频保护参数（0 个候选）；保留日志，未发布成品
```

下载和 merge 都成功（`lesson.ts` 172 MB 生成），**publish 阶段拒绝出片**。
清空 `work/` 重下一次仍然一模一样 —— 所以不是网络或缓存问题，是这一集的保护参数（key 候选）
解析不出来。这类集只能等工具侧修，**不要在状态里写成「上传失败」**，它是导出失败。

排查入口：`work/<id>/media.log`（解码器报错）与 `validation.progress`；
`stderr.log` 里可能是**上一次**运行的残留（它不覆盖），别拿它当本次结论。

---

## 6. 一次只跑一个导出

`publish` 是**完整解码校验**，把 12 核吃到 88% 是常态。实测每集约 50 秒 ~ 4 分钟
（时长越长越慢，与视频分辨率也有关）。两个导出并行只是把各自的耗时拉长，
总吞吐不变，还会拖慢同时在跑的上传。

**超过 10 分钟的前台命令会被工具超时杀掉**（曾经把一个 1625 MB 的成片截断成 918 MB），
所以批量导出**一律后台跑**，输出逐集 flush，方便看进度。

---

## 7. 相关文档

- `docs/R2-UPLOAD.md` —— 目录约定、并发甜点、代理、上行带宽共用
- `docs/BILIBILI-UPLOAD.md` —— 水印、分P 顺序、批量投稿页的陷阱
- `.scratch/bilibili-upload/issues/` —— 需要人工处理的两件事（改名 / 删重复分P）
