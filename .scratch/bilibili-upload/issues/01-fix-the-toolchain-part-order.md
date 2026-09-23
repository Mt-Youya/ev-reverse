# 01 — 修正 `前端工具链` 稿件（BV1zqhb6bE1W）的分P顺序与第 1 分P 名

Status: ready-for-agent
Type: task

## 现状

`BV1zqhb6bE1W`「前端工具链」，aid `117320890521692`，**27 分P 齐全**，但有两处不对：

1. **分P 顺序错乱。** B站 按分P 到达顺序排列，而投递时 `bili_upload.py` 用相对路径字符串排序，
   中文目录名的码点序是 `一(4E00) < 三(4E09) < 二(4E8C) < 五(4E94) < 四(56DB)`，
   于是章节顺序成了 **第一 / 第三 / 第二 / 第五 / 第四**。

   实测顺序（`biliup show BV1zqhb6bE1W`）：

   ```
   [1] 前端工具链        [2] 02. 抽象语法树      [3] 02. 检查规则        [4] 03. 配置文件part1
   [5] 04. 配置文件part2  [6] 05. CLI命令行      [7] 06. APIs            [8] 07. ESLint插件
   [9] 08. 自定义ESLint插件 [10] 09. 集成Prettier [11] 10. ESLint+Prettier实战
   [12] 01. Prettier介绍  [13] 02. 格式化规则    [14] 03. 命令行工具     [15] 04. APIs
   [16] 05. 实现简易CLI   [17] 01. 更多工具学习方法 [18] 02. Terser       [19] 03. SWC
   [20] 01. Babel介绍     [21] 02. 配置文件      [22] 03. CLI            [23] 04. 使用插件
   [24] 05. 使用预设      [25] 06. APIs          [26] 07. 自定义插件part1 [27] 08. 自定义插件part2
   ```

2. **第 1 分P 的名字是「前端工具链」**，应为「01. 课程概述」。这一条是误操作留下的：
   在浏览器投稿页填「基本设置」时选中了第 1 条，把合集标题填进了单条的标题框，
   随后点「立即投稿」把这一条单独发了出去（详见 `docs/BILIBILI-UPLOAD.md` §4.1）。
   其余 26 条是用 `biliup append` 补上的，名字正常。

## 为什么现在不能自动修

B站 稿件管理页的「编辑」「⋮」按钮对合成点击无响应（Playwright 真实点击、hover、原始鼠标事件都试过，
坐标换算已核对），所以**不能在页面上拖拽重排分P**。API 删除接口 `member.bilibili.com/x/vu/web/delete`
返回 404，新 endpoint 未找到。

## 两个候选做法

**A. 使用者在创作中心手动重排（最省事）**
创作中心 → 内容管理 → 稿件管理 → 该稿件「编辑」→ 分P 管理里拖拽排序，并把第 1 分P 改名。
约一分钟。

**B. 删掉重传（agent 可独立完成）**
排序 bug 已在 `80a57bd` 修好，重传即可得到正确顺序：

```powershell
# 1. 删（需要新 endpoint；旧的是 404，见上）
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\delete_submission.py BV1zqhb6bE1W          # 先 dry run 核对
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\delete_submission.py BV1zqhb6bE1W --apply

# 2. 确认 bili_upload.py 现在会当作「新建稿件」而不是 append（稿件已不存在）
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\bili_upload.py --only 工具链

# 3. 重传（27 集 4.69 GB，约 15 分钟）
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\bili_upload.py --only 工具链 --apply
```

代价是重传 4.69 GB，并且换一个 BV 号。

## 验收

```powershell
& "$env:USERPROFILE\.biliup\bin\biliup.exe" -u "$env:USERPROFILE\.biliup\cookies.json" show <BV>
```

- 27 个分P
- 顺序为 `01. 课程概述` → `02. 抽象语法树` → `01. Prettier介绍` → … → `03. SWC`（第一章到第五章）
- 抽帧确认左上角**没有**「优雅先森」+ bilibili logo（水印）
