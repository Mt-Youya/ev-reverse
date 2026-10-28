# 02 — 删掉 `工程管理实战`（BV1K9hp6WEyX）里多出来的 2 个「35. 单元测试」分P

Status: resolved
Type: task

## 需要人工做的（1 分钟，biliup 做不到）

1. 创作中心 → 内容管理 → 稿件管理 → 「工程管理实战」→ **编辑**
2. 分P 列表里有 **3 条完全同名**的 `35. [测试体系]单元测试.mp4`（45 个分P 里的第 43~45 条），
   **删掉其中 2 条**，保留 1 条
3. 保存

删完应为 **43 个分P**，且没有重复标题。

（实测 2026-09-24：`biliup show BV1K9hp6WEyX` 三个分P 的标题一模一样，都是
`35. [测试体系]单元测试.mp4`，所以不用挑「哪条干净」，删两条即可。）

## 起因（已修，不会再长出来）

平台侧这一集的标题本身是**双扩展名**：

```
file_id 982963   标题 "35. [测试体系]单元测试.mp4.mp4"   （见 verify_fresh/index.json）
```

导出成文件后是 `35. [测试体系]单元测试.mp4.mp4`，而 `bili_upload.py` 的 `norm()` 当时只剥离
**一个**扩展名，得到 `35.[测试体系]单元测试.mp4`，与稿件里已有的分P 名
`35. [测试体系]单元测试.mp4` 仍不相等（后者还多一个 `.mp4`）—— 于是**每跑一轮上传就补一个分P**，
直到有 3 条。

修法：`norm()` 现在剥离**重复**的扩展名（`(\.(mp4|mkv|ts))+$`），
`35. [测试体系]单元测试.mp4.mp4` → `35.[测试体系]单元测试`，而分P 名
`35. [测试体系]单元测试.mp4` 也归一到同一个值，不再重复追加。
`tools/courses_json.py` 的 `norm()` 同样处理，否则这一集会被状态页报成「本地没有成片」
（1.1 GB 的文件明明在 `out/` 里）。

本地文件也被改名成单扩展名 `35. [测试体系]单元测试.mp4`，所以它现在与分P 名对得上。

## 为什么不能自动删

- `member.bilibili.com/x/vu/web/delete` 返回 **404**（旧接口已下线，新 endpoint 未找到）。
- 删单个分P 只有 `POST member.bilibili.com/x/vu/web/edit`，需要提交**整份 archive 载荷**
  （`videos` 数组里每个分P 的 `filename` 必须是上传时的存储名）。手写这份载荷有**破坏线上稿件**
  的风险，所以没有做。
- 创作中心页面的「编辑」「⋮」按钮对合成点击无响应（详见 issue 01）。

## 验收

```powershell
& "$env:USERPROFILE\.biliup\bin\biliup.exe" -u "$env:USERPROFILE\.biliup\cookies.json" show BV1K9hp6WEyX `
  | Select-String '单元测试'
```

应只剩 **1** 行。

## Comments

**2026-10-13 — 已修完，人工那一步不需要了。**

「创作中心的编辑按钮对合成点击无响应」这个结论已经过时：它是用当时的工具测的，而
agent-browser + CDP 这套（`docs/BILIBILI-UPLOAD.md` §8）能驱动编辑页 —— 同一天用同一条路
把 Electron 从 51 个分P 加到 71、给另外三门课追加了分P，都是这么做的。

删分P 的实际流程（脚本在 `.scratch/delete-duplicate-parts.mjs`，可复用）：

1. 打开 `?type=edit&bvid=BV1K9hp6WEyX`；
2. 每行右侧有 `div.icon-btn`（垃圾桶图标），**先 hover 那一行**再点它，否则控件没接上；
3. 会弹出「确定要删除这个分P么」，点「确认」；
4. 删一条会让行号整体上移，所以每轮重新找、**永远删最后一条**，第一条就是要留的；
5. 点「立即投稿」保存。

结果：

```
BV1K9hp6WEyX  工程管理实战  parts=43  only_self=1  state=-50
重复标题: 无
单元测试分P 数: 1
```

45 → 43，与 `verify_fresh/index.json` 的 43 集对齐，重复标题清零。

顺带一提，这条 issue 的「为什么不能自动删」里说对了：`x/vu/web/delete` 仍然 404、编辑接口仍然
要整份 archive 载荷。走页面是绕开这两点的唯一路子，而不是不可能。

