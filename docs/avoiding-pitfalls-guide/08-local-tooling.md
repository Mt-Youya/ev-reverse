# 08 · 本地工具链

> Windows + PowerShell + 一堆原生工具，组合出的失败模式比代码里的多。

这一节的坑有个共同特征：**报错信息指向错误的地方**。`tail` 吞掉退出码，于是构建失败看起来像成功；
PowerShell 5.1 按 ANSI 读 `.ps1`，于是中文变成 `灏侀溃` 目录名；屏幕捕获拿到覆盖窗口，
于是「播放器没渲染」看起来像「播放器是黑的」。

[`docs/RETROSPECTIVE.md`](../RETROSPECTIVE.md) §12 已经记了 13 条操作层面的坑
（JSON 参数用文件传、ffmpeg 用数组传参、Ghidra 地址带基址、`Set-Content` 会加 BOM、
不要反复 attach 同一进程、`git commit -F`……）。本节**不重复它们**，只补那 13 条之外、
在别的会话里出现过就再没人记下来的。

---

<a id="tool-01"></a>
### TOOL-01 · `tail` 吞掉退出码，于是构建失败看起来像成功

`batch` 2026-10 · `severity` high · `status` resolved · `source` claude-code--7b0c412f:1124

**在哪里咬人**：为了少看输出，构建命令被管道接上 `tail`（或 `Select-Object -Last N`）：
`cargo build ... 2>&1 | tail -20`。

**怎么暴露的**：代理宣布「Tauri 探针构建干净（built clean）」。

**怎么诊断的**：代理自己发现了：

> The probe actually **failed** — `tail` masked the exit code. Tauri v2 requires `icons/icon.ico`
> for the Windows resource file. Good thing I probed.

管道的退出码是**最后一个命令**（`tail`）的退出码，而 `tail` 无论上游死活都会成功。
`$LASTEXITCODE` / `$?` 因此全是 0。

**当时提了什么**：重新查看真实输出，补上缺失的 `icons/icon.ico`。

**最后怎么解决**：生成真实图标后重建。注意代理紧接着的第二个坑：机器上没有 Pillow，
于是用纯 Python 超采样生成图标——**在探针阶段就发现工具链缺口**，比在交付时发现便宜得多。

**影响面**：这类错误的危险在于它**消灭了失败信号**。同一个仓库里它至少出现过两次
（另一次在 `claude-code--db027b91:3739`，同样是 Tauri 探针、同样是 `tail`）。
在没有 CI 的本地工作流里，它是「假通过」最常见的来源。

**证据**：原话见存档。

**经验**：永远不要把**决定成败的命令**接进管道再判断成败。
需要截断输出时，用 `set -o pipefail`（bash）或 `$LASTEXITCODE` 在管道**之前**取，
更简单的做法是先判断、后展示：`cmd; $ok = $?; cmd 2>&1 | Select -Last 20; if (-not $ok) {...}`。
「探针」的意义就是让失败暴露出来——把它接进管道等于取消探针。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-01

---

<a id="tool-02"></a>
### TOOL-02 · PowerShell 5.1 按 ANSI 读 `.ps1`，中文变成了 `灏侀溃` 目录名

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-a48ac823:17470

**在哪里咬人**：`deliver.ps1` 里写死了中文目录名，脚本在 Windows PowerShell 5.1 下运行。

**怎么暴露的**：产生了 4 个 `灏侀溃` 之类的乱码目录（UTF-8 字节被按 GBK 解读的典型形状）。

**怎么诊断的**：PowerShell 5.1 读取 `.ps1` 时**按系统 ANSI 代码页解码**，不认 UTF-8 无 BOM。
脚本里带中文就等于在猜代码页。

**当时提了什么**：**所有 `.ps1` 一律只用 ASCII**，中文全部搬进 `cards.json` 由程序读。

**最后怎么解决**：脚本 ASCII 化，中文进数据文件；4 个乱码目录清理掉。

**影响面**：乱码目录会被后续的遍历、比对、上传逻辑当成真实课程目录处理——
一个编码问题会变成数据污染。

**证据**：记录见存档（与 TOOL-03 同段）。

**经验**：脚本文件里的**字面量**是代码，不是数据。非 ASCII 的文案、路径、标题全部放进
数据文件（JSON），让脚本保持纯 ASCII——这样脚本的运行结果不依赖运行它的那个 shell 的代码页。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-02

---

<a id="tool-03"></a>
### TOOL-03 · `-Key a,b` 的逗号被拆坏：匹配不到任何卡片，却报告成功

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-a48ac823:17471

**在哪里咬人**：`powershell -File backgrounds.ps1 -Key a,b`。

**怎么暴露的**：脚本**一张卡片也没匹配到，却报告成功**。

**怎么诊断的**：`-File` 传参时逗号不会被拆成数组，脚本拿到的是一个整串（或拆成了与预期不同的形状），
于是「选择器」匹配空集合；而脚本对空集合不做失败处理，正常走完就退出了 0。

**当时提了什么**：修正参数拆分，并在 `$targets` 为空时**大声失败**。

**最后怎么解决**：加入「空目标即失败」的检查。

**影响面**：这是「零结果被当成成功」的又一个实例（同族见 VER-03、DB 一节）。
零结果是最危险的返回值，因为它同时兼容「正确」和「什么都没做」。

**证据**：记录见存档（与 TOOL-02 同段）。

**经验**：任何「按选择器处理一批对象」的脚本，**空输入必须失败**，除非「空」正是被验证的正确结果。
默认退 0 的零结果循环，会在几周后变成一次「我明明跑过了」的争论。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-03

---

<a id="tool-04"></a>
### TOOL-04 · Codex CLI 只认 `HTTP(S)_PROXY`，忽略 Windows 系统代理

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-a48ac823:17469

**在哪里咬人**：调用 Codex CLI（Rust 实现）访问上游。

**怎么暴露的**：直连失败，报 `stream disconnected before completion`——

> Codex direct-connect failure (`stream disconnected before completion`)

这个报错看起来像服务端问题或鉴权问题，与「代理没生效」毫无字面联系。

**怎么诊断的**：CLI 是 Rust 写的，**读环境变量 `HTTP(S)_PROXY`，不读 Windows 系统代理设置**（注册表里的 `ProxyEnable` / `ProxyServer`）。

**当时提了什么**：脚本自己镜像注册表代理；后来在 `ProxyEnable=0` 时又扩展出端口探测。

**最后怎么解决**：脚本负责把代理注入环境变量；不依赖系统代理生效。

**影响面**：本项目**代理是硬性要求**（见 `docs/RETROSPECTIVE.md` §1：直连 GitHub 约 170 KB/s，
走代理 8.2 MB/s，差约 50 倍）。因此「哪个工具读哪个代理配置」是一个反复出现的环境问题。

**证据**：记录见存档。

**经验**：每接入一个新的原生工具（Rust/Go/CLI），先确认它的代理**读取源**：
环境变量、系统代理、还是自有配置文件。系统代理不是通用约定，尤其是用 Rust/Go 写的新 CLI。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-04

---

<a id="tool-05"></a>
### TOOL-05 · 播放器每次启动都换 PID，而「两个 EVPlayer2 进程都在解码」是错的

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--c99ccf68:39

**在哪里咬人**：Frida 脚本与 x64dbg 挂在 `EVPlayer2.exe` 上。

**怎么暴露的**：`frida.attach` 抛 `ProcessNotFoundError`；以及一次「哪个进程在解码」的判断错误。

**怎么诊断的**：两条操作事实：

1. EVPlayer2 是**一个小启动器进程 + 一个重型渲染进程**；解码的是渲染进程，
   要按**工作集大小**挑它（`tools/parser-tools/count_decode.py` 就是这么做的，并打印模块基址）。
2. **PID 每次启动都变**，所以存下来的 PID 一过期，`frida.attach` 就抛 `ProcessNotFoundError`；
   而 x64dbg 挂过的那个进程（18024）是随调试会话一起退出的。
   早先「两个 EVPlayer2 PID 都在解码」的假设是**错的**。

**当时提了什么**：按工作集识别渲染进程，不再靠 PID 复用或「进程名有两个」。

**最后怎么解决**：识别规则写进工具；PID 按每次会话重新发现。

**影响面**：PID 类错误的表现是「进程不存在」，很容易被读成「播放器没起来」，
从而去排查错误的方向（用户其实开着播放器）。

**证据**：同段还并列写下了一条反复被重新学到的验证纪律（见 VER-07），记录见存档。

**经验**：任何跨越「一次进程生命周期」保存的句柄都视为**已过期**。
挑选目标进程时用**内容特征**（工作集、加载的模块、解码调用），不要用 PID，也不要用进程名计数。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-05

---

<a id="tool-06"></a>
### TOOL-06 · 磁盘写满，而报错出现在一个不相关的写文件上

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-a48ac823:17478

**在哪里咬人**：写 `courses.json` 时抛 `OSError: [Errno 28] No space left on device`。

**怎么暴露的**：一个纯文本 JSON 写入失败，看起来像权限或路径问题。

**怎么诊断的**：D: 盘 **500 GB 已用满、剩余 0 GB**。运行 `clean_work.py --apply` 释放了 19.62 GB 后，
同一条命令立即成功。

**当时提了什么**：先清磁盘，再重跑；把这次记录成一条错误。

**最后怎么解决**：释放 19.62 GB 后重跑成功
（`wrote .../courses.json (60 courses, 1910 episodes)`，`local=137 r2=552 bilibili=511 both=511`）。

**影响面**：这个项目的中间产物是**几十 GB 量级**（导出视频、R2 拉回、封面），
磁盘耗尽是一个会周期性重访的环境状态，而不是一次性事故。

**证据**：记录见存档。

**经验**：`Errno 28` 只说明「写不进去」，不说明原因在写的那一行。遇到本地写失败先看磁盘余量；
在这个仓库里，任何批量导出/拉回任务开始前都应先检查目标盘余量——
否则失败会在流水线的中后段才出现，浪费的是前面全部的工作。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-06

---

<a id="tool-07"></a>
### TOOL-07 · 用 PowerShell 批量改 UTF-8 源码：13 个文件的中文注释全毁，且**自动回滚不可靠**

`batch` 2026-10 · `severity` high · `status` resolved · `source` dsh--session-79bdf5ca:5961

**在哪里咬人**：对 `crates/evmedia-gui/**` 的一轮批量文本替换
（`Get-Content -Raw` + `Set-Content -Encoding UTF8`）。

**怎么暴露的**：整份 GUI 源码的中文注释变成 mojibake；
`tools/verify-gui.ps1` 同时报

```
Set-Content : An object at the specified path ...\1. 鏁扮粍 绗?1 鑺?..mp4 does not exist
```

**怎么诊断的**：清点出 `catalog.rs`/`config.rs`/`job.rs`/`lib.rs`/`locate.rs`/`plan.rs`/
`protocol.rs`/`queue.rs`/`runner.rs`/`server.rs`/`session.rs`/`sniff.rs`/`evmedia-stub.rs`
共 **13 个源文件**受损（3 个测试文件可逆）。
反向修复**失败**：13 个文件 `CANNOT REPAIR`——因为写入时加了 BOM
（`'\ufeff'` 无法用 gbk 编码）且 `server.rs` 等处有不可逆替换；
连修复脚本自己都被 GBK 控制台卡住（`'gbk' codec can't encode character '\u20ac'`）。

**当时提了什么 / 怎么解决**：放弃自动还原、逐个文件重写内容；
并让 PowerShell 脚本保持**纯 ASCII**，把生成中文路径的活儿交给 Rust（`make_fixture.rs`）。
结果：`repaired 3, unrepairable 13` → 最终靠重写恢复；脚本改为 ASCII-only + Rust 生成预置产物。

**影响面**：一次批量改写就能毁掉整个 crate 的中文注释；
而「反着做一遍即可还原」这个直觉**在这里不成立**——BOM 和不可逆替换让回滚有损。
这是 TOOL-02 的升级版：TOOL-02 毁的是**目录名**，这条毁的是**源码**。

**证据**：`repaired 3, unrepairable 13` 与两条 `CANNOT REPAIR` 的编码错误见存档。

**经验**：**绝不用 PowerShell 的 `Get-Content`/`Set-Content` 批量改 UTF-8 源码**。
Windows 上让脚本 ASCII-only，多字节内容交给 Rust/Python 写。
更一般地：做**批量**改写前先确认「失败能不能回滚」——
本例里回滚路径本身依赖同一个有损编码，所以真正的保护是**改之前先提交/备份**。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-07

---

<a id="tool-08"></a>
### TOOL-08 · 本地工具链的四种静默少报：管道、解码器、反汇编器、解释器

`batch` 2026-10 · `severity` high · `status` resolved · `source` claude-code--7b0c412f:1940

**在哪里咬人**：不是一处，而是四个工具在四件事上**安静地少给了结果**：

1. **管道吞退出码**（TOOL-01 的第二次）：
   `cargo build … 2>&1 | tail -30` 对管道返回 0 → 「构建成功」是假的；
   第二次用 `tail` 把 **534 条结果截成 7 条**——代理自己写下「同样的错误我犯了两次」，
   用户据此要求**停止在构建里用 `tail`**。
2. **`std::str::from_utf8` 在「URL 后紧跟堆二进制」处失败** → 课程扫描 0 命中，
   当时被误归因于「`regions()` 只留 `MEM_PRIVATE`」。
3. **Capstone 的 `md.disasm` 在第一个无法解码的字节处停止**：
   8 MB 的 `.text` 只返回 **448 个 call**，直到设了 `md.skipdata=True`。
4. **PATH 上的 `python` 是 WindowsApps 商店占位 stub** → 报
   「Python was not found」，看起来像没装 Python。
   还有 **frida 17.18 移除了静态 `Module.findExportByName` / `enumerateExports`**
   → `fatal TypeError: not a function`（改用 `Process.getModuleByName(...)` 的实例方法）。
5. **reporter 丢掉分片失败事件** → `failed=1` 从头到尾**没有原因**。

**当时提了什么 / 怎么解决**：逐条改成「以退出码为准」、
用 `skipdata=True`、显式记录解释器绝对路径、让 reporter 保留失败原因。

**影响面**：五种失效全部表现为**「结果比真实情况少」**，
而少报的方向恰好都支持「这个方向不行」的结论——
于是它们会主动**消灭**正确的方向。第 3 条尤其隐蔽：448 个 call 看起来像一个真实的低数字。

**证据**：`534 → 7`、`8 MB → 448 calls`、Store stub 的报错与 frida 17 的 API 变更见存档。

**经验**：工具「给得少」比「给错」更难发现。
三条可执行的：决定成败看**退出码**而不是输出量与颜色；
解析器类工具要问「遇到坏字节它是跳过还是停止」（默认往往是停止）；
PATH 上的同名解释器可能是 stub，用**绝对路径**并打印它的版本。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-08

---

<a id="tool-09"></a>
### TOOL-09 · 并发 + 重试的流水线，必查四件事

`batch` 2026-10 · `severity` high · `status` resolved · `source` claude-code--db027b91:2864

**在哪里咬人**：导出流水线的对抗性复核（三视角 + 逐条反驳），抓出 **13 个真实缺陷**
（7 条被 skeptic 驳回），其中两条高危。

**怎么暴露的 / 怎么诊断的**：两条高危的形状值得单独记住——

1. **关闭时在途任务未回收** → 结果丢失 / 合并被跳过；
2. **永久失败的分片每轮重投都算「有进展」** → idle 计数永不累积 → **死循环**。

同批还有：`keys.json` 加载了却未用于调度；`done` 集合是死代码；
**信任被截断的 `dec/`/`enc/`**；`player.keys()` 未捕获异常；
merge 静默跳过；`--mp4` 失败被忽略；硬编码 `-map 0:a:0` 在纯视频流上失败；
`looks_like_segment` 把 `.ts` 也算进十六进制校验 → **所有 URL 被拒**；
Rust 预过滤把 `+0x268` 当成内联字符串（**实为指针**）；
陈旧 `dec/` 播种丢弃了 178 个已解密文件。

最关键的修复：清单合并从「按 `idx`」（每响应恒为 0）**改为按文件名 + `idx`-or-到达顺序**。

**最后怎么解决**：13 条逐条修复；复核本身也产出了误报（见 VER-13）。

**影响面**：这类缺陷的共同点是**不报错**——它们表现为「跑完了但结果不对」或「永远跑不完」。

**证据**：13 条缺陷清单、两条高危的形状、以及 `idx` 恒为 0 的塌缩见存档。

**经验**：并发/重试流水线必查四件事：
**在途任务的归宿**（被关闭/取消时它们的输出去哪了）、
**永久失败的重试上限**（否则「重试」本身被当成进展）、
**被截断的中间产物是否被信任**、
**「有进展」的判据是否把重试算成了进展**。
再加一条：失败必须带原因，`failed=1` 而没有原因等于没有 information。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-09

---

<a id="tool-10"></a>
### TOOL-10 · 配置文件写着「开启」，注册表里其实是关的

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-477f1338:1253

**在哪里咬人**：排查 Codex 桌面端「反复重试、不建窗口」。

**怎么暴露的**：怀疑对象一路换（`cc-switch` 接管残留 → `~/.codex` 被删），
都靠时间戳排除；真正卡住的是「配置文件说开着、注册表说关着」。

**怎么诊断的**：

- clash-verge 配置里写着 `enable_system_proxy: true`，
  但 8:27 读注册表时 `ProxyEnable = 0`（**系统代理实际是关的**），
  直到 8:32:32 clash 重写配置后才变成 1；
- `chatgpt.com` 的 DNS 被污染成 `69.171.229.73`（Facebook 的 IP），**裸直连必然超时**；
  走 clash 的 7897 一切正常（`chatgpt.com/cdn-cgi/trace` → 200）；
- 8:33 时它的网络进程已经连上 `127.0.0.1:7897` ——
  Chromium 会**热读**系统代理，代理一开就通了；8:34 那次启动立刻正常。

**最后怎么解决**：8:32:32 系统代理被 clash 写回 1，问题消失；
`cc-switch` 用日志时间段（08:11:45–08:17:06）证明不是原因。

**影响面**：任何「读配置文件即结论」的排查都会**锁错对象**——
本例多花了两轮去排查 `cc-switch` 与 `~/.codex`，而它们都是清白的。

**证据**：`ProxyEnable` 的变化时刻、DNS 污染结果与「代理一开就通」见存档。

**经验**：判定**运行时行为**只看**运行时有效状态**，配置文件只说明「它想怎样」。
读到「配置说 A」与「实际行为是 B」冲突时，**先信行为**，
再去查「谁在什么时候把状态改回去了」——本例里那个时刻（8:32:32）直接指出了元凶。

- 📎 excerpt: [evidence.gz](evidence.gz) → TOOL-10
