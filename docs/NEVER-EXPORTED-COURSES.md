# 从未导出过的 25 门课：补齐它们需要先做的准备

这份文档只讲一件事：**本地一个成片都没有、两个平台也都没有任何记录的那 25 门课**（807 集、
源文件合计 104.5 GB），要补上需要先做什么。它们不是上传失败，而是**从来没下载过**。

实时数量见 `verify_fresh/upload-status/gaps.md`（每个数据刷新周期自动重生成）。

---

## 1. 为什么它们现在传不了

`tools/export_missing.py` 规划「要导出哪些课时」时，依据是 `courses.json` 里每个课时的
`local / r2 / bilibili` 三态：

* `local=false` 且 `r2=false`（或 `bilibili=false`）→ 会规划导出；
* 三个都是 `null`（**没有映射**：既没有 R2 前缀，也没有稿件）→ **不规划**，因为在没有归属地的情况下
  「缺」这件事无法断言，输出路径也没有依据。

这 25 门课正是第三种。所以补齐它们的第一步不是下载，而是**给它们一个家**（R2 前缀 + 封面 + 稿件条目）。

---

## 2. 需要做的三件事（每门课约 15 分钟准备 + 之后全自动）

1. **定 R2 前缀**：沿用 `videos/<分类>/<主题>/duyi-edu/`（下表是建议值，分类按内容而不是按课程
   所属的售课目录——`NestJS` 这门「前端架构课程」就住在 `videos/backend/nodejs/`）。
   写进 `tools/clean_work.py` 的 `R2_BY_COLLECTION`。
2. **生成封面**：往 `tools/covers/cards.json` 加一张卡（标题、英文副题、标签、集数、时长、画面描述），
   然后 `node tools/covers/render.mjs <key>` + `tools/covers/deliver.ps1`。
3. **登记 B站 合集**：在 `tools/bili_upload.py` 的 `COLLECTIONS` 加一行（合集路径 + 5 个标签），
   稿件由看门狗在下一轮 pass **自动创建**（带 `watermark.state=0`，无水印）。

做完这三步，`courses.json` 会把它们标成 `r2=false`，于是 `export_missing.py` 就能规划导出：

```powershell
python tools/export_missing.py --only <课程名子串>            # dry run，看要导多少
python tools/export_missing.py --only <课程名子串> --apply    # 逐集下载
python tools/watch_and_upload.py --loop-minutes 20            # 之后交给看门狗：发布→上传→清理
```

导出是**逐集串行**的，实测每集约 2–4 分钟（时长越长越慢）；807 集约需 **2–3 天**机器时间，
期间上行为上传、下行为下载，互不干扰。

---

## 3. 建议的 R2 前缀（未采纳，等确认）

分类沿用桶里已有的顶层分类（`ai` / `backend` / `frontend` / `python` / `3D` …），
鸿蒙系与求职系各新建一个顶层分类。

### 鸿蒙Next课程（10 门）

| 课程 | 集数 | 源大小 | 建议前缀 |
| --- | ---: | ---: | --- |
| HarmonyOS Next SDK | 76 | 5.37 GB | `videos/harmonyos/next-sdk/duyi-edu/` |
| ArkUI应用框架 | 62 | 10.38 GB | `videos/harmonyos/arkui/duyi-edu/` |
| HarmonyOS应用模型 | 40 | 6.91 GB | `videos/harmonyos/app-model/duyi-edu/` |
| HarmonyOS Next 特色与生态 | 48 | 2.31 GB | `videos/harmonyos/ecosystem/duyi-edu/` |
| 坚果驾考 | 57 | 3.89 GB | `videos/harmonyos/driving-test/duyi-edu/` |
| 实战案例-简记 | 21 | 3.64 GB | `videos/harmonyos/case-notes/duyi-edu/` |
| daysmatter | 17 | 1.53 GB | `videos/harmonyos/daysmatter/duyi-edu/` |
| 实战案例-华夏旅行者 | 14 | 2.40 GB | `videos/harmonyos/case-traveler/duyi-edu/` |
| 鸿蒙就业指导 | 4 | 0.24 GB | `videos/harmonyos/career/duyi-edu/` |
| 鸿蒙认证考试辅导 | 1 | 0.07 GB | `videos/harmonyos/certification/duyi-edu/` |

### 前端课程（2 门）

| 课程 | 集数 | 源大小 | 建议前缀 |
| --- | ---: | ---: | --- |
| 求职之道极速版 | 69 | 8.95 GB | `videos/career/job-hunt-fast/duyi-edu/` |
| 求职之道常规版 | 68 | 8.80 GB | `videos/career/job-hunt-standard/duyi-edu/` |

### 前端架构课程（13 门）

| 课程 | 集数 | 源大小 | 建议前缀 |
| --- | ---: | ---: | --- |
| 前端+AI（基础篇） | 56 | 6.79 GB | `videos/frontend/ai-basics/duyi-edu/` |
| React源码解析 | 54 | 7.77 GB | `videos/frontend/react-source/duyi-edu/` |
| vue源码解析(新) | 51 | 9.22 GB | `videos/frontend/vue-source/duyi-edu/` |
| vue源码分析 | 17 | 2.47 GB | `videos/frontend/vue-internals/duyi-edu/` |
| vue-router源码分析 | 24 | 4.98 GB | `videos/frontend/vue-router-source/duyi-edu/` |
| 从零实现Coding Agent | 36 | 5.32 GB | `videos/frontend/coding-agent/duyi-edu/` |
| AI平台篇（扣子&Dify） | 26 | 2.90 GB | `videos/ai/platform-coze-dify/duyi-edu/` |
| 通用组件库开发 | 16 | 3.40 GB | `videos/frontend/component-library/duyi-edu/` |
| 浏览器插件开发 | 15 | 2.64 GB | `videos/frontend/browser-extension/duyi-edu/` |
| Recoil源码分析 | 13 | 1.45 GB | `videos/frontend/recoil-source/duyi-edu/` |
| pinia源码 | 10 | 0.67 GB | `videos/frontend/pinia-source/duyi-edu/` |
| RxJS | 7 | 1.40 GB | `videos/frontend/rxjs/duyi-edu/` |
| 主题切换 | 5 | 1.00 GB | `videos/frontend/css/theme-switching/duyi-edu/` |

**两个容易撞名的**：
`React源码分析`（38 集，已上传，住 `videos/frontend/react-internals/`）与
`React源码解析`（54 集，未导出，上表建议 `react-source`）；
`vue源码解析(新)`（51 集）与 `vue源码分析`（17 集）同理——
前缀名要能一眼区分，否则以后按名字配对会配错（`docs/R2-UPLOAD.md` §3 记的就是这类事故）。

---

## 4. 顺序建议

先做**小课**（`鸿蒙认证考试辅导` 1 集、`鸿蒙就业指导` 4 集、`主题切换` 5 集、`RxJS` 7 集、
`pinia源码` 10 集）：准备成本一样，但几十分钟就能整门走完「导出→发布→R2→B站→删本地」，
用它们验证命名与封面流程，再铺开大课。

磁盘：这 25 门合计源文件 104.5 GB，导出后与源文件相近；按 `docs/EXPORT-PIPELINE.md` 的
「发布→上传→清理」循环，峰值只比当前多几十 GB —— 现余量 180 GB 够用。
