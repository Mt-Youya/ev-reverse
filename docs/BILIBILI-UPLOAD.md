# B站投稿：流程、硬性约束与一次真实的翻车记录

这份文档写给下一个要把课程合集投到 B站 的 agent。它记录三件事：**必须显式处理的约束**（漏一个就出错）、
**本次实际踩到的坑**（按代价排序，每条给根因和可复现的命令）、以及**浏览器自动化能做到哪一步**。

写作时刻的实测结论写在最前面：

> **用 `tools/bili_upload.py`（底层是 biliup CLI）。不要用浏览器投稿。**
> 浏览器那条路本次丢了两次上传、daemon 重启四次、最后还因为误判页面模型多发了一条废稿件。

---

## 1. 当前事实（写作时刻，2026-09-23）

| 项 | 值 | 怎么核对 |
| --- | --- | --- |
| 本次稿件 | `BV1zqhb6bE1W`「前端工具链」，aid `117320890521692`，**27 分P** | `python tools/delete_submission.py BV1zqhb6bE1W`（dry run 只打印） |
| 分P 顺序 | ❌ **错乱**（第一/三/二/五/四章） | `biliup -u ~/.biliup/cookies.json show BV1zqhb6bE1W` |
| 第 1 分P 名 | ❌ 是「前端工具链」，应为「01. 课程概述」 | 同上 |
| 水印 | 浏览器那条提交时开关是关的；biliup append 的 26 条走 `state:0`，**未在本稿件上抽帧验证** | 见 §3.1 |
| biliup | `~/.biliup/bin/biliup.exe`，cookie 在 `~/.biliup/cookies.json` | `& "$env:USERPROFILE\.biliup\bin\biliup.exe" -u "$env:USERPROFILE\.biliup\cookies.json" list` |
| 分区 | `tid 208`（科技数码 → 计算机技术） | 从已有稿件的 `biliup show` 读回 |

遗留的两条问题已开成 issue：`.scratch/bilibili-upload/issues/01-fix-the-toolchain-part-order.md`。

---

## 2. 正确流程：一条命令

```powershell
cd D:\Codes\github\ev-reverse
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\bili_upload.py --only <合集名子串>          # 先 dry run
& 'D:\DevelopmentTools\Anaconda\python.exe' tools\bili_upload.py --only <合集名子串> --apply  # 再真发
```

它一条稿件一个合集：把一个目录下**递归**找到的全部视频交给 `biliup upload`，产生一条多分P稿件。
已经存在的稿件不会简单跳过 —— 它用 `biliup show` 取现有分P 标题，只把缺的用 `biliup append` 补上
（`前端架构课程/LangGraph工作流开发` 就是这种情况：稿件有 23 分P，而本地目录只剩 11 集，
因为其余的上传后已被删除，用「目录大小 ≥ 稿件」判断会误判为完整）。

稿件标题 = 目录名。标签在 `tools/bili_upload.py` 的 `COLLECTIONS` 里逐合集写死。

---

## 3. 三件必须显式处理的事

### 3.1 水印：默认是开的，两条路都要主动关

B站 **默认给稿件加水印**（左上角「bilibili + 昵称」）。这是本次被使用者明确要求必须在场的约束。

- **biliup 路径**：biliup 的 `Studio` 结构体里**没有** watermark 字段，什么都不发就等于要默认值 →
  水印被加上。必须显式传 `--extra-fields '{"watermark":{"state":0}}'`
  （`tools/bili_upload.py` 的 `WATERMARK_OFF`，upload 和 append 两条分支都要带）。
  已在测试稿件 `BV1FdhE6iExc` 上验证有效：同一集同一时间点，旧稿件左上角有「优雅先森」+ bilibili logo，
  新稿件没有。**本稿件 `BV1zqhb6bE1W` 未做这项抽帧复核。**
- **浏览器路径**：投稿页底部「更多设置（含声明与权益、视频元素、互动管理等）」里，**第一个选项就是
  「添加水印」，默认勾选**，文案是「仅对此次上传的视频生效」。DOM 里是
  `div.watermark.setting-content label.bcc-checkbox`，勾上时 class 多一个 `bcc-checkbox-checked`。
  ⚠️ **页面重新加载后它会回到默认开启** —— 本次就因为 daemon 重启导致页面重载，取消了两次。

### 3.2 封面：文件必须叫这个名字

`tools/bili_upload.py` 按约定找封面：

```
verify_fresh/out/<合集>/封面/<合集名>-合集封面-16x9.png
```

用 `node tools/covers/render.mjs <card-key>` 渲染，再 `powershell -File tools/covers/deliver.ps1` 交付。
卡片定义在 `tools/covers/cards.json`（文案 + AI 出图提示词）。

**出图那条路会断**：AI 背景图靠 Codex CLI（`tools/covers/backgrounds.ps1`），配额用尽时报
`You've hit your usage limit ... try again at <date>`，`MISS` 三次后 `FAIL`。此时 `render.mjs` 会退化成
**矢量兜底图**（`art=vector`）—— 版式、字体、配色、文案全部正常，只是右侧是节点图而不是 AI 插画。
本次 `前端工具链` 用的就是它，观感可用，不必因此阻塞投稿。

### 3.3 分P 顺序 = 上传顺序（biliup 不会替你排）

B站 按**分P 到达顺序**排列，不做重排。所以「本地文件的遍历顺序」直接决定观众看到的分P 顺序。

**中文目录名按字符串排序不是数字序**：`一`(U+4E00) < `三`(U+4E09) < `二`(U+4E8C) < `五`(U+4E94) < `四`(U+56DB)。
修复前 `bili_upload.py` 用 `key=str(p.relative_to(folder))`，于是章节制合集被投成 一/三/二/五/四 章。
已修（提交 `80a57bd`）：`chapter_index()` 解析 `第X章` 的汉字数字，先按章号再按文件名排。
核对方法：

```powershell
& 'D:\DevelopmentTools\Anaconda\python.exe' -c @"
import pathlib, sys; sys.path.insert(0,'tools'); import bili_upload as bu
folder = pathlib.Path('verify_fresh/out/前端架构课程/前端工具链')
vids = sorted((p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in bu.VIDEO_SUFFIXES), key=lambda p: bu.course_order(folder, p))
[print(f'{i:>2}. {v.relative_to(folder)}') for i, v in enumerate(vids, 1)]
"@
```

---

## 4. 本次踩到的坑（按代价排序）

### 4.1 ⚠️ 最大的一个：把 B站 批量投稿页当成「一稿件多分P」

- **当时怎么想**：页面上 27 个视频排成一个网格，下面是「基本设置（封面/标题/创作声明/分区/标签/简介）」，
  再下面是「存草稿 / 立即投稿」。看起来就是「一个稿件、27 个分P」。我照这个模型填完表单，点了「立即投稿」。
- **实际是什么**：这个页面是**每个视频一条独立稿件**的批量模式。标题栏那句
  「将以下所有视频 **[加入合集]**」才是它的真实语义 —— 加入合集是为了把这些**多条稿件**归拢，
  顺带用「不生成动态」避免刷屏粉丝。底下那套「基本设置」是**当前选中那一条**的表单，点哪张卡片就切到哪一条。
- **后果**：点提交时选中的是第 1 条，于是只发出 `BV1zqhb6bE1W` 一条 1 分P 稿件（`01. 课程概述` 39:02），
  标题还是我按「合集」填的「前端工具链」。后台立刻可见：`全部稿件 484 → 485`、`进行中 1`。
- **怎么发现**：页面里 `前端工具链` 和 `01. 课程概述` 同时消失、卡片只剩 26 张；
  去「创作中心 → 内容管理 → 稿件管理」看到新稿件的时长是 `00:39:02`（单集）而不是合集的十几小时。
- **补救**：见 §4.2 的 append。**下次要合并成一条多分P稿件，就用 biliup，不要在这个页面上找按钮。**

### 4.2 ⚠️ agent-browser 的 daemon 会反复重启，每次都吞掉在传的上传

- **症状**：上传进行到一半，页面变成 `chrome://new-tab-page/`，`tab list` 只剩一个新标签页。
- **根因（两条，都会触发）**：
  1. daemon 空闲会自行退出；下一次命令起一个全新的浏览器实例，标签页全丢。
  2. **父调用被中断时整棵进程树被杀** —— 本次四次重启里有三次是我在工具调用里放了
     `Start-Sleep -Seconds 150/180`，调用被打断，daemon 一起没。
- **唯一的救命稻草**：B站 自己在浏览器本地记住了**已完成**的上传，重开投稿页会提示
  「本地浏览器存在 N 个未提交的视频」+「继续编辑」。本次靠它一路累加：**6 → 10 → 11 → 13 → 24 → 26**，
  但**每次崩溃都会丢掉当时正在传的那几个**（第一次就丢了 4.69 GB 里的绝大部分）。
- **结论**：这条路的期望代价极高。要传大合集就用 biliup —— 它 4 并发直传，
  `前端工具链` 26 个文件 4.52 GB 约 13 分钟传完，全程无中断。

### 4.3 B站 稿件管理页的控件对合成点击无响应

- 试过且**全部无效**：`click div.article-card a.more-btn`（Playwright 真实点击）、`hover` 后再点、
  `mouse move` + `mouse down` + `mouse up`（坐标取自 `getBoundingClientRect()`，视口 1258×622、dpr=1，换算无误）。
  「编辑」按钮（`a.bili-btn`）同样点不动，URL 不变。
- 对照：**同一个浏览器上**，投稿页的 `div.cover-editor-head-close` 用 `click` 一次就关掉了，
  eval 里 `.click()` 反而无效（Vue 不响应合成 click）。所以不是 agent-browser 整体失灵，
  是这一页的控件不吃合成事件。
- **绕法**：走 API。`tools/delete_submission.py` 用账号自己的 cookie 直接发请求
  （`GET api.bilibili.com/x/web-interface/view?bvid=` 取 aid，再 POST 删除接口）。
  ⚠️ 老 endpoint `member.bilibili.com/x/vu/web/delete` **已经 404**，新 endpoint 本次没找到 —— 见 issue。

### 4.4 `cmd` 元字符会把传给 `eval` 的 JS 拆碎

agent-browser 的 stdout 会被浏览器进程继承，PowerShell 管道永远等不到 EOF，命令**打完输出就挂住**；
唯一可行的是 `cmd /c "... > 日志文件 2>&1"`。但这样一来 JS 表达式就落在 cmd 的解析里：

| 字符 | cmd 把它当什么 | 症状 |
| --- | --- | --- |
| `\|` | 管道 | `'等待上传' is not recognized as an internal or external command` |
| `&&` | 命令分隔 | `SyntaxError: Unexpected end of input` |
| `>` `<` | 重定向 | 同上，或参数被截断 |

**规避**：`eval` 的表达式里不要出现这四个字符。用 `x.indexOf(y)+1` 代替 `x.includes(y)`、
用 `k-5` 代替 `k<5`、用 `.filter(Boolean)` 代替 `&&`、`.join(',')` 代替 `' | '`。

### 4.5 PowerShell 5.1 把无 BOM 的 UTF-8 脚本读成 ANSI

写进 `.ps1` 的中文字面量会变乱码（`封面` → `灏侀溃`），本次一个上传脚本因此直接语法错误。
**约定**：`.ps1` 保持纯 ASCII，中文一律放外部 JSON/文本文件，脚本用 `-Encoding UTF8` 读。

### 4.6 daemon 通信卡死被误诊成网络故障

所有命令都报 `Failed to read: 由于连接方在一段时间后没有正确答复... (os error 10060)`，包括
`get url` 和根本不需要联网的 `set offline off`。同时 PowerShell 直连 `member.bilibili.com` 返回 200、
DNS 正常、系统代理 `ProxyEnable=0`、机器上没有 TUN 网卡。
**结论**：是 daemon 自己卡死了，不是网络。`close` + `Stop-Process -Name agent-browser -Force` 后立刻恢复。

### 4.7 免登录：注入 biliup 的 cookie，别去复制 Chrome profile

想用使用者的登录态，最直接的想法是复制 Chrome profile。**走不通**：

- Chrome 对 `Default\Network\Cookies` 是**独占锁**，运行中复制报 `WinError 32`；
  用 `FileShare.ReadWrite` 打开也不行（Chrome 不允许共享）。
- Chrome 136+ **禁止在默认 user-data-dir 上开远程调试**，所以「关掉 Chrome 再用原 profile 开调试端口」也不成立。

**可行做法**：把 `~/.biliup/cookies.json` 里的 `SESSDATA` / `bili_jct` / `DedeUserID` / `DedeUserID__ckMd5`
用 `agent-browser cookies set <name> <value> --domain .bilibili.com --path / --secure` 注入自己的浏览器，
然后打开 `member.bilibili.com/platform/home` 验证不跳登录页。本次全程这么用。

---

## 5. 浏览器自动化在这个仓库里的能力边界（实测）

| 能做 | 不能做 |
| --- | --- |
| 注入 cookie 免登录、打开创作中心任意页 | 在稿件管理页点「编辑」「⋮」 |
| 投稿页选文件（`upload "input[type=file]" <27 个路径>`）、看进度 | 让批量投稿页产出「一条多分P稿件」 |
| 填标题/分区/标签/简介（Quill 编辑器用 `#id` + `type`） | 保证上传期间状态不丢（daemon 会重启） |
| 取消「添加水印」、设封面（弹窗内两个 `input[accept*=image]`，先打 id 再传） | 合成点击触发 B站 的 Vue 事件（要真 selector 点击） |
| 截图 + `eval` 读 DOM 做断言 | — |

**其它环境事实**：`computer-use` 不可用（`~/.cua-driver` 不存在，插件 `dsh-computer-use` 缺底层驱动）；
Tabbit 未安装且官方安装器 `403`；`agent-browser` 0.19.0 在 `~/.cargo\bin\agent-browser.exe`。
PYTHON 用 `D:\DevelopmentTools\Anaconda\python.exe`（PATH 里的 `python` 是 Microsoft Store 占位符，会报
"Python was not found"）。

---

## 6. 本次会话的提交

| commit | 内容 |
| --- | --- |
| `80a57bd` | 修 `bili_upload` 章节排序（中文码点序 → 章号） |
| `f8a1fa9` | `前端工具链` 进上传表 + 新增 `tools/delete_submission.py` |
| `cc15593` | `verify_and_delete` 覆盖 `工程管理实战`、`D3.js` |
| `4cf199c` | `微前端` 封面卡 |
| `cf604f3` | 修 `clean_work` 合集匹配（见下） |

同一次会话里还修了两个磁盘清理的真 bug，它们是投稿的前置条件（D 盘曾 0 可用空间）：

- `clean_work.local_collection()` 只试路径**后缀**，而带章节的课程折叠后合集名不在末尾
  （`[前端架构课程, LangGraph, 第二章 快速入门]`），整门课被误判成「B站 没有」。
  改成按长度递减试所有连续子串。
- `clean_work` 按**父目录**精确匹配 R2 对象，而 `videos/ai/langgraph/architecture-duyi-edu/` 下的对象
  嵌在 `第一章/`… 子目录里，全部落空。改成按对象文件名 + 前缀判断。
