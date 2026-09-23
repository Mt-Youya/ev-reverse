# 浏览器自动化：避坑清单（agent-browser）

写给以后的 agent。这些坑**不限 B站**，任何用 `agent-browser` 驱动浏览器的任务都会遇到。
开头五条是「不遵守就干不成活」级别的，后面是速查。

一句话：

> **文件重定向调用；不要放长 sleep；不要指望合成点击；免登录用 cookie 注入。**

---

## 0. 本机能力现状（先确认你手上有什么）

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| `agent-browser` 0.19.0 | ✅ 可用 | `C:\Users\Yonjay\.cargo\bin\agent-browser.exe` |
| `computer-use`（桌面点击/截图） | ❌ 不可用 | 插件 `dsh-computer-use` 缺底层驱动 `cua-driver`；`~/.cua-driver` 不存在，PATH 里也没有 |
| Tabbit 浏览器 | ❌ 不可用 | 未安装，官方安装器下载 **HTTP 403** |
| 使用者自己的 Google Chrome | ⚠️ 只能看不能控 | 无调试端口；见 §4 |
| Python | ⚠️ 不在 PATH | 用 `D:\DevelopmentTools\Anaconda\python.exe`；PATH 里的 `python` 是 Microsoft Store 占位符，报 "Python was not found" |

**所以：不要假设 `computer_click` / `app_list` / `screen_observe` 能工作。**
需要整机截图时用 `screenshot` skill 的 PowerShell 脚本（那个是纯截图，不依赖驱动）。

---

## 1. 怎么调 agent-browser（不这样调就一定挂）

### 1.1 必须用文件重定向，不能用管道

agent-browser 启动的浏览器**继承了 stdout 句柄**，所以 PowerShell 的管道永远等不到 EOF：
命令**打完输出就挂住**，直到超时。表现是「结果明明打出来了，但工具调用不返回」。

**可行写法**（`cmd` 里的 `>` 让子进程直接写文件，shell 只等 CLI 进程退出）：

```powershell
$ab = 'C:\Users\Yonjay\.cargo\bin\agent-browser.exe'
$log = Join-Path $env:TEMP ("ab-" + [guid]::NewGuid().ToString('N').Substring(0,8) + ".txt")
cmd /c "`"$ab`" --profile `"$profileDir`" <命令> > `"$log`" 2>&1"
Get-Content $log -Encoding UTF8
Remove-Item $log -Force
```

### 1.2 日志文件名必须每次唯一

浏览器 daemon **继承着上一个日志文件的句柄**，固定路径会让下一次调用报
`The process cannot access the file because it is being used by another process`。
用 GUID 前缀，或者用完立刻删。

### 1.3 `eval` 的表达式里不能出现 `|`、`&&`、`>`、`<`

因为整条命令最终落在 `cmd` 的解析里，这四个字符会被当成 cmd 的运算符，把 JS 拆碎。
症状是 `SyntaxError: Unexpected end of input` 或
`'xxx' is not recognized as an internal or external command`。

| 想写 | 改成 |
| --- | --- |
| `a \|\| b` | `a ? a : b`，或分两次 eval |
| `x && y` | `.filter(Boolean)` / 拆成两条 eval |
| `s.includes(t)` | `s.indexOf(t)+1` |
| `k < 5` | `k-5`（当布尔用） |
| `.join(' \| ')` | `.join(' , ')` |

**并且**：结论性的判断（是不是、有没有）**优先用截图看**，别硬凑 eval 表达式。

### 1.4 不要在工具调用里放长 sleep

见 §2.2 —— 长 `Start-Sleep` 一旦让调用超时/被打断，**整棵进程树会被杀掉**，页面和在传的上传一起没。
需要等进度就**多次短调用轮询**（每次 20–30 秒以内）。

---

## 2. daemon 的生命周期（最大的一个坑）

### 2.1 它是常驻的，但会自己重启

`agent-browser` 首次调用起一个常驻 daemon + 一个浏览器实例，后续命令复用。
**daemon 空闲会自行退出**；下一次命令起一个全新实例 —— **所有标签页丢失**，当前 URL 变成
`chrome://new-tab-page/`。命令输出里那句
`⚠ --profile ignored: daemon already running` 就是在提示你 daemon 的生命周期不受你控制。

**症状识别**：`get url` 返回 `chrome://new-tab-page/`、`tab list` 只剩一个新标签页。

### 2.2 父调用被中断 = daemon 被杀

这条比空闲退出更致命，因为它是**你自己造成的**：工具调用被中断时，进程树一起被杀。
本仓库一次投稿任务里 daemon 重启了四次，其中三次的诱因就是调用里的 `Start-Sleep -Seconds 150/180`。

**对策**：
- 单次调用保持短（几秒到几十秒），长等待拆成多次轮询。
- 需要长时间跑的任务（上传、导出）**不要挂在浏览器上** —— 用 CLI 工具在后台跑，见 §6。

### 2.3 恢复

```powershell
cmd /c "`"$ab`" close > `"$log`" 2>&1"
Get-Process -Name 'agent-browser' -ErrorAction SilentlyContinue | Stop-Process -Force
# 然后重新 open
```

### 2.4 有些站点的进度存在浏览器本地，能续

B站 投稿页会在本地记住**已经传完**的视频，重开时提示「本地浏览器存在 N 个未提交的视频 / 继续编辑」。
崩溃丢掉的是**当时正在传的那几个**。这类站点允许你「崩溃 → 重开 → 继续」地累加，
但代价极高：一次投稿任务靠这个机制一路累加 6 → 10 → 11 → 13 → 24 → 26，**第一次崩溃就丢了 4.69 GB**。

**结论：能不用浏览器就不用。** 见 §6。

---

## 3. 交互：找元素与点击

### 3.1 点击要用 agent-browser 的 `click <selector>`，不要用 eval 里的 `.click()`

B站 这类 Vue/React 页面对**合成 click** 不响应：`element.click()` 返回成功但什么也没发生。

```powershell
# ✅ 有效（Playwright 真实点击）
cmd /c "`"$ab`" click div.cover-editor-head-close > `"$log`" 2>&1"
# ❌ 常常无效
cmd /c "`"$ab`" eval document.querySelector('div.cover-editor-head-close').click() > `"$log`" 2>&1"
```

选择器里**不要含空格**，就不用加引号，能省掉一层转义。

### 3.2 找元素靠读 DOM，不要靠 `find text`

`find text "中文"` 经常报 `Element not found`，即使那段文字就在页面上。
可靠的顺序是：

1. `eval` 列出候选的 `tag+class+坐标`：

   ```
   eval JSON.stringify(Array.from(document.querySelectorAll('*')).filter(function(e){return e.textContent.trim()==='目标文字'}).map(function(e){var r=e.getBoundingClientRect(); return e.tagName+'.'+String(e.className).slice(0,30)+' '+Math.round(r.left)+','+Math.round(r.top)}))
   ```

2. 挑到**真正带 handler 的那个**节点 —— 常常是内层元素而不是外层容器
   （外层 `div.title` 点了没反应，内层 `span.label` 带 `cursor:pointer` 才是它）。
3. 用 `click` 打它。

### 3.3 找不到唯一选择器时，先给它打 id

一次 `eval` 里给目标 `setAttribute('id', ...)`，之后就一直用 `#id`：

```
eval (function(){var a=document.querySelectorAll('input[accept*=image]'); a[0].id='cover43'; a[1].id='cover169'; return a.length})()
```

### 3.4 截图会被缩放显示，DOM 坐标才是准的

工具回给的预览图可能缩过（例如 1258×622 的截图按 1137×562 展示）。
用 `getBoundingClientRect()` 拿到的坐标 + `window.innerWidth/innerHeight` + `devicePixelRatio` 才是真值。
**别照着预览图量像素去点。**

### 3.5 弹窗超出视口 → 先放大视口

按钮点不到时，先确认它在不在视口内：

```
eval JSON.stringify({w:innerWidth,h:innerHeight,dpr:devicePixelRatio})
set viewport 1400 1300
```

### 3.6 隐藏的 `input[type=file]` 可以直接塞文件

不需要先点「上传」按钮把它显形 —— Playwright 对隐藏 input 一样能 setInputFiles：

```powershell
cmd /c "`"$ab`" upload `"input[type=file]`" `"C:\路径\a.mp4`" `"C:\路径\b.mp4`" > `"$log`" 2>&1"
```

一次传多个文件时，路径用**双引号**逐个包起来再拼成一行；含中文的路径可以正常传
（`.ps1` 里不要写中文字面量，见 §5.1 —— 清单从外部 UTF-8 文件读）。

---

## 4. 免登录：注入 cookie，不要复制 Chrome profile

想复用使用者的登录态时，**「复制 Chrome profile」这条路是死的**：

1. Chrome 对 `Default\Network\Cookies` 是**独占锁**，运行中复制报 `WinError 32`；
   用 `FileStream` + `FileShare.ReadWrite` 也不行。
2. Chrome 136+ **禁止在默认 user-data-dir 上开远程调试**，所以「关掉 Chrome 再用原 profile 开调试端口」
   也不成立。

**可行做法**：把已有的 cookie 注入 agent-browser 自己的浏览器。

```powershell
cmd /c "`"$ab`" cookies set SESSDATA `"<值>`" --domain .bilibili.com --path / --secure > `"$log`" 2>&1"
```

本仓库可以直接用 `~/.biliup/cookies.json`（biliup 已经登录过），取
`SESSDATA` / `bili_jct` / `DedeUserID` / `DedeUserID__ckMd5` 四个就够。
注入后打开目标站点的**登录后页面**（例如 `member.bilibili.com/platform/home`）验证不跳登录页。

⚠️ 安全问题：**不要在密码框里代替使用者输入**。需要账号密码时让使用者自己输（或扫码）。

---

## 5. Windows / 编码陷阱

### 5.1 PowerShell 5.1 会把无 BOM 的 UTF-8 脚本读成 ANSI

写进 `.ps1` 的中文字面量会变乱码（`封面` → `灏侀溃`），轻则报错重则建出乱码目录。

**约定（本仓库已在用）**：`.ps1` 保持**纯 ASCII**，中文一律放外部 JSON / 文本文件，
脚本用 `Get-Content -Encoding UTF8` 读。

### 5.2 `cmd` 元字符（与 §1.3 同源，这里再强调一次）

进了 `cmd /c "..."` 之后，`|` `&` `&&` `>` `<` `^` 都是运算符。
**任何要传给子进程的字符串（JS 表达式、JSON、正则）都不能含这些字符。**

### 5.3 不要用 `python`，用绝对路径

PATH 里的 `python` / `python3` 是 Microsoft Store 占位符。用
`D:\DevelopmentTools\Anaconda\python.exe`（含 `jsonschema`、`boto3`）。

### 5.4 参数里的引号会被逐层吃掉

PowerShell → `cmd` → 目标程序，每层都可能吃掉引号。**最省事的办法是不产生引号**：
选择器写成不含空格的形式（`div.foo.bar`）、正则里避免空格、值从文件读。

---

## 6. 什么任务不该用浏览器

用浏览器 = 接受 §2 的所有不稳定性。以下情形**优先找 CLI/API**：

| 任务 | 别用浏览器，用 |
| --- | --- |
| 批量上传视频到 B站 | `biliup`（见 `docs/BILIBILI-UPLOAD.md`） |
| 上传对象到 R2/S3 | `tools/upload_to_r2.py` |
| 删自己的一条 B站 稿件 | `tools/delete_submission.py`（走创建中心 API） |
| 下载课程 | `evmedia.exe`（见 `docs/CATALOG-DOWNLOAD.md`） |
| 抓取静态内容 | `web_fetch` |

**只有两种情况值得上浏览器**：
(a) 目标功能只有网页有（合集、分P 重排、封面编辑器）；
(b) 必须看渲染结果才能判断（水印开关的真实状态、封面预览效果）。

---

## 7. 一句话速查

```powershell
# 调用模板（唯一可靠的形式）
$ab = 'C:\Users\Yonjay\.cargo\bin\agent-browser.exe'
$log = Join-Path $env:TEMP ("ab-" + [guid]::NewGuid().ToString('N').Substring(0,8) + ".txt")
cmd /c "`"$ab`" open https://example.com > `"$log`" 2>&1"
Get-Content $log -Encoding UTF8 ; Remove-Item $log -Force

# 崩了
cmd /c "`"$ab`" close > `"$log`" 2>&1"
Get-Process -Name 'agent-browser' -ErrorAction SilentlyContinue | Stop-Process -Force

# 判断还活着吗
cmd /c "`"$ab`" get url > `"$log`" 2>&1"   # chrome://new-tab-page/ = 已经重启过了
```
