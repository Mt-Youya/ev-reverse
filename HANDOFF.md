# HANDOFF — EVPlayer2 逆向项目交接文档

> 本文件由 Claude Code 会话维护。开始前先读本文件，再读 `docs/ARCHITECTURE.md`、
> `tools/parser-tools/EVPlayer2_解密工具与参数/EVPlayer2_分析结果.md`。
>
> **脱敏说明**：本文件曾记录真实的会话素材，提交前已把其中的凭证类值替换为占位符。
> 凡是形如 `<REDACTED-TOKEN>` / `<REDACTED-SIGN>` / `<REDACTED-KEY>` / `<REDACTED-*-UUID>`
> 的位置，原文都是当时抓到的真实值——它们是分片 URL 的签名、清单里的 `tk`、以及一个已还原的
> 分片密钥。文档记的是**形状和结论**，不是那些值本身。需要复现时按本文的方法重新抓一份即可。

## 项目目标（用户原话）

1. 做一个针对 EVPlayer2 进程的启动器：弄清它登录时如何识别本机设备，把 Windows Sandbox 里的设备 ID 模拟到宿主机，让 EVPlayer2 直接在宿主机运行，不再依赖沙箱。—— **已完成并跑通**
2. **自动获取课程的全部目录**，然后做一个软件，根据目录**多线程高速批量下载**对应视频（用户明确表示不要"播放哪个下载哪个"的逐个抓取流程）。—— **完成一半，而且是决定性的那一半**：目录接口已截获、"播一遍 → 收割 → 下载解密 → 合并 MP4"整条链路已用 Rust 跑通全课时（第五~七轮）。**没有达成的是"不播放"**——每个课时仍需播一遍才有它的分片密钥，这与目标 2 的"不要逐个抓取"直接冲突。根因见"卡点 A"（离线派生在本构建上不成立）与"卡点 B"（API 明文读不到）。剩余工作已切成 12 张工单：`.scratch/offline-keys-and-bench/issues/`，其中 06–10 是"不播放"这条路的正面对攻。

## 环境事实

- 用户机器：Windows，Python 3 在 **conda 环境 `subgen`**，路径
  `C:\Users\Yonjay\.conda\envs\subgen\python.exe`（frida 17.18.0 装在这里；
  系统 PATH 上的 `python` 是 Microsoft Store 占位符，**不能用**）。
- EVPlayer2 安装目录：`D:\Learning\EVPlayer2`（Qt **5.9.9** + UPX 加壳，原版 5.0.5，
  SHA-256 = 873f936d3c4555bcbb0cd725b5d0731cfe63407049999e98864d8a734e4ca47a）。
- 离线下载缓存目录：`D:\Downloads\EVPlayer2Downloads\`（约 1770 个 `119354-<uuid>.ts` 加密分片）。
- 应用数据目录：`C:\Users\Yonjay\AppData\Local\EVPlayer2\`（`conf/Admin.conf`、`log/`）。
- 运行 frida 脚本请用：`C:\Users\Yonjay\.conda\envs\subgen\python.exe -u <script>`

## 已完成的工作

### 1. 设备模拟启动器（tools/device_launcher/）—— 已完成
`EVDeviceLauncher.cpp` 调试器式启动器：校验目标 exe SHA-256；替换三个设备 getter
（RVA `0x17ddc0` 计算机名 / `0x17ec30` MAC / `0x17f930` HardwareID）；从
`device-profile.ini` 读模拟值；子进程 `__COMPAT_LAYER=RunAsInvoker` 绕过 UAC；完成后脱离调试器。
`run-evplayer.cmd` 一键启动。**已验证可用**（本会话曾多次用它拉起 EVPlayer2，pid 23428）。

> 注意：从非交互 shell 直接跑 `EVDeviceLauncher.exe` 有时会 `Cannot detach startup debugger win32=5`；
> 重试或让用户双击 `run-evplayer.cmd` 即可。

### 2. 流量/API 截获（tools/parser-tools/capture_all.py）—— 已完成
**这是本会话的主要成果。** 之前卡在 `fatal TypeError: not a function`，根因与修复：

- **Frida 17 移除了静态 `Module.findExportByName` / `Module.enumerateExports`**（返回
  `TypeError: not a function`）。改用实例方法：
  `Process.getModuleByName(name).enumerateExports()`（**同步返回数组**）和
  `…findExportByName(exp)`。`capture_all.py` 已全程用这套 API 并保留旧版回退。

**关键环境事实（决定截获策略）：**

| 事实 | 结论 |
|---|---|
| Qt 5.9.9，**没有加载 OpenSSL**（System32 只有一个无关的 libcrypto.dll） | Qt 的 HTTPS 路径不通；TLS 走 **Schannel** |
| 加载了 **`nghttp2.dll`**（HTTP/2） | 应用流量是 **h2-over-TLS** |
| `PlayerLibRender56_vs.dll` 导入 `Secur32` 的 EncryptMessage/DecryptMessage、`zlib.dll`、`bcrypt.dll`(仅 GenRandom)、`CRYPT32`(仅证书) | 应用**自己做 TLS**；AES 是**静态链接**在 DLL 里（不在 CNG/CryptoAPI） |

**截获通道（capture_all.py 全部启用，任何可用者生效）：**
- `nghttp2_submit_request` → 出站请求头（含 `:path`/`:authority`）+ 请求体；
- `nghttp2_session_callbacks_set_on_header_callback[_2]` /
  `…_set_on_data_chunk_recv_callback` → 挂应用自己的回调 → **已解码的响应头 + body**
  （无需解 HPACK），按 `session:sid` 关联（sid 每个连接都从 1 开始，必须带 session）；
- **`zlib.dll` 的 `inflate`/`uncompress`/`deflate`/`compress`** → **绕过 AES** 直接拿到明文：
  响应是 `AES 加密 + zlib 压缩`，压缩后的密文被 inflate 时即可拿到解压后的明文 JSON；
- Qt（QNAM createRequest/get/post… + QIODevice::read + QJsonDocument）与 Schannel
  作为兜底（应用不用 Qt 解析 JSON，故 QJsonDocument 钩子无输出）。

**运行方式：** 先启动 EVPlayer2 并登录，再
`python -u capture_all.py`，在播放器里浏览目录 + 打开视频，抓完创建
`captured/STOP`（或 Ctrl+C）。产物在 `captured/`（已被 .gitignore 排除）：
`urls.log`、`events.jsonl`、`bodies.bin`（h2 body，含密文）、`zlib.bin`（**解压后的明文 JSON**）。

### 3. 已截获的 API（明文）

- 域名：`en2.ieway.cn`、`en2v4.ieway.cn`（API）；`cn<busi_id>.evplayer.cn`（分片 CDN）。
- 请求：`POST https://<host>/student/<endpoint>`，头带
  `Authorization: Bearer <JWT>`（JWT 内层 token：
  `{"stu_id":1113723,"acc_id":119354,"busi_id":28027,"ptms":1789273364855}`），
  `Content-Type: application/json`，客户端 `restclient-cpp`。
- 请求体：`{"params": "<base64(AES 加密后的 JSON)>", "version": 200|202}`
  （`params` 解出来是 800/2000 字节的高熵、16 字节对齐数据 → **真加密**，AES-**ECB**
  【已用"两条同 endpoint 请求共享 30/50 个 16 字节块"验证】）。
- 响应体：`{"errcode":0,"errmsg":"","uuid":"…","zip":1,"encrypt":1,"result":"<base64(AES 加密 [+zlib 压缩])>"}`。
- 已见 endpoint：`getEvsSignUrl`、`getPlayAuthorityEVS`、`getPlaySubtitle`、`getDownEVSKey`、
  `getPlayTimeKeySignEVS20231103`、`getPlayTimeKeySignEVS20260515`。

**目录接口（明文 JSON，来自 zlib.bin）** 形如：
`{"authority_list":[…60 门课详情…],"big_list":[…5 个大类…],"small_list":[…45 门课…],"live_list":null}`；
`big_list` 是大类（前端架构课程/前端课程/AI 大全栈/WebGIS课程/鸿蒙Next课程），
`small_list` 通过 `parent_uuid` 挂到大类，`authority_list` 是课程详情
（`account_id=119354`、`name`、`uuid`、`id`、`category`…）。

**分片清单接口（明文 JSON，来自 zlib.bin）**，一次响应 = **一页**（`idx` 可能从 0 或 30 起）：
```json
{ "d_p": "http://cn28027.evplayer.cn/<REDACTED-COURSE-UUID>",
  "k_l": [ { "idx": 0,
             "sf": "/119354-<REDACTED-SEGMENT-UUID>.ts?bid=119354&sid=1113723&t=<REDACTED-SIGN>&v=2.0&sign=<REDACTED-SIGN>",
             "tk": "<REDACTED-TOKEN>" } ] }
```
分片 URL = `d_p + sf`，**纯 HTTP**（`http://`，非 HTTPS）。实际下载已验证可跑通
（本会话成功下过一个 395744 字节的分片）。

### 4. 下载/解密管线（`ev2_batch.py`，已删）—— 当时的记录，已被下面的结案推翻

> 这一节写的是第二轮的状态。它当时判断"只差一把盐"，**那个判断是错的**：盐不存在，
> 见下方"历史卡点：全局盐（已结案）"。保留它是因为它记录了当时的进展与产物形状。

`ev2_batch.py --from-capture captured/zlib.bin --salt <盐> --out ev2_out --jobs 8`
自动：从 capture 抽清单 → 按 `d_p` 合并分页 → 并行下载分片 → 按已验证算法解密 → 合并 `<tag>.ts`。
已于本会话跑通下载/合并阶段（13 页 → 7 个视频，并行下载 5 片正常），
**当时以为只差 `--salt`**（否则报 "not aligned MPEG-TS (wrong salt?)"）——后来的结论是没有这把盐。
真正的密钥来源是播放上下文的 `+0x120`，见第五轮起。

**已验证的解密算法**（见 `EVPlayer2_分析结果.md`）：
```
mask = MD5(分片文件名)[:16]                    # 16 个 ASCII 字节，16 字节周期循环 XOR
key  = 分片自己的 32 位十六进制文本，直接当 32 字节 AES-256 密钥用   # ← 已修正
明文 = AES-256-ECB 解密(密文 XOR mask)，去掉尾部 '#' 填充 → 188 字节/包 MPEG-TS
```
> 原文档在这里写的是 `key = MD5(tk + 分片文件名 + 运行时附加参数)`。**那条公式对本构建不成立**
> （决定性否定见卡点 A：进程内存里根本不存在 `tk+文件名` 的拼接串）。mask 与 AES 那两行是成立的，
> 密钥那一行是错的，现按实测改为"分片自己的十六进制文本"。


## 历史卡点：全局盐（已结案 —— 此路不通，保留作为记录）

> **结论先行**：下面这一整节是"想做到任意视频离线解密"的失败记录。那个目标**没有达成**，
> 而且已经证明**在这个构建上不可能靠本地可见数据达成**——密钥不是算出来的，是播放器解密时
> 才产生的。今天的可用链路是"播一遍 → 收割 → 下载解密"，见第六、七轮。本节的唯一价值是
> **阻止下一个人重走这条路**：`MD5(tk + 文件名 + 盐)` 这条公式对本构建不成立。
> 另外注意：`src/evplayer_windows.rs` 的 `find_salt` 已随第七轮重构删除。

### 卡点 A：分片解密的 `运行时附加参数`（盐）
- 明文清单里**没有**盐（`p_p` 字段只是 `false`）。它是**运行时值**，播放器解密分片时才有。
- **本会话重大进展（已验证）**：运行中的播放器上下文（`PlayerLibRender56_vs.dll` 里
  vtable ∈ `dll+0x802000..0x804000` 的对象）里，`+0x120` 处的 32 字节"schedule"经
  `KeyFromSchedule`（前 16 字节原样 + 后 16 字节经 `Mix()` 变换）即得到该分片的
  **32 位十六进制 ASCII 密钥**。用它 + `mask=MD5(文件名)[:16]` 做 AES-256-ECB 解密，
  **实测解出合法 MPEG-TS（0x47 同步字节通过）**（见 `find_segments.ps1` 的 `KeyFromSchedule`
  与 `EVPlayer2_decode.py` 的 `decode_segment`）。→ **只要播放器加载了某视频，它的分片就能直接解密（无需盐）。**
- 但要让 `ev2_batch.py` 对**任意**视频的分片离线解密，仍需那把**全局盐**（key = MD5(tk+文件名+盐)）。
- 本会话尝试（均未命中）：已知值各种顺序组合；扫描 DLL 与截获流量里的字符串；按文件名位置扫进程
  内存 ±1–2MB；对盐做 1–5 位可打印字符暴力（以已恢复的 key 为校验目标，约 20 亿次/几分钟）；
  检查上下文原始 0x2a8 字节（只有 schedule，无盐内联）。`find_segments.ps1` 的两级盐恢复本次
  **两阶段都失败**。标准 MD5 K 表（文件偏移 `0xae12d0` / RVA `0xae1ed0`）在播放时**未见被读取**。
- **⚠️ 决定性否定结论（第二轮，重要）**：直接搜进程内存里 `tk+文件名` 的 ASCII 拼接串——**0 命中**
  （`file+tk` 也是 0；单独 `tk` 有 1 处命中，但后面是二进制数据，不是拼接）。也就是说
  **这个版本并不在内存里拼 `tk + 文件名 + 盐`**，文档里那条公式（`MD5(tk+分片标识+盐)`）对本构建
  **不成立**。这解释了为什么上面所有找盐的尝试（可打印串扫描、二进制字节穷举、1–5 位暴力、
  find_segments 两级恢复）全部失败——一直在找错误的目标。需要重新确定派生方式（可能不是简单拼接，
  也可能根本不用 MD5）。
- **完整三元组已拿到（关键进展）**：`wait_ready.py` 同时抓「清单条目」和「上下文」，已确证
  **清单里的 `tk` 与上下文 `+0x288` 的 tk 完全一致**（82 个分片重叠，逐条相等）。并且拿到形如
  `tk=<REDACTED-TOKEN> / file=119354-<REDACTED-SEGMENT-UUID>.ts / key=<REDACTED-KEY> /
  sf=…?bid=119354&sid=1113723&t=<REDACTED-SIGN>&v=2.0&sign=<REDACTED-SIGN>` 的**完整样本**。
  上下文 `+0x120` 未解密时是堆填充值 `0df0adba`(=0xBAADF00D)，解密后被填成真密钥——**ready 数量随播放增长**。
- **穷举仍未命中**：用上述完整三元组测试了约 **110 万+** 种假设——{tk, 文件名, 去扩展名, uuid, sf, qs,
  t, sign, v, bid, sid, idx, d_p, d_p-uuid, "4|0|"} 的 1–4 元排列 × 40+ 种分隔符（**含 0x00–0x20 控制字节**）
  × {md5, sha1, sha256, hmac-*} × 十六进制解码后的 tk / 大写 tk。**全部不匹配**。
  另用 JWT、JWT payload、内层 token、ptms、设备 ComputerName/MAC/HardwareID 作为"公共参数"测试——同样不匹配。
- **可读响应里没有任何密钥**：把解压后的全部明文（101 KB）通读，只有 3 种 JSON 形状
  （`{d_p,k_l}` / `{d_p,k_l,p_p}` / 目录 `{authority_list,big_list,live_list,small_list}`），
  32 位十六进制值全部是 `tk`，**没有任何密钥字段**；也没有数组/纯字符串响应。
- **结论**：分片密钥**不是**由本地可见数据算出来的。要么由**服务器下发**（在 `zip:0,encrypt:1`
  那些我方读不到的响应里，例如 `getPlayTimeKeySign*` / `getDownEVSKey`），要么用了一个我们从未见到的密钥。
  所以"任意视频离线解密"必须先打通「读取 API 响应明文」这一步。
- 另一条线索：AES 解密点 `PlayerLibRender56_vs.dll+0x792c78` 命中时栈上有 `4|0|119354-<uuid>.ts`
  这种"`<type>|<idx>|<文件名>`"形态的串，以及 `Authorization`（说明同一 AES 也用于 API 响应）。
  该断点**频率很低**（不是分片热路径），而 AES 的**调用方位于堆上动态生成的代码**（地址不属于任何
  模块），这正是静态翻 DLL 找不到派生代码的原因。
- **下一轮（攻"读 API 明文"）的结果（第三轮）**：
  - 锁定了唯一读不到的响应：`getDownEVSKey`(703B)、`getEvsSignUrl`(387B)、`getPlayAuthorityEVS`(1051/1071B)、
    `getPlaySubtitle`(105B)，全部 `zip:0,encrypt:1`。能读的都是 `zip:1`（= 目录 + 分片清单）。
  - `sniff_plain.py` 实测**在响应到达的瞬间捕获到了 zip:0 响应**（result 320 字符）——通道是通的；
    但在内存里定位/捞明文没做成（全内存扫太重、差分扫描实现有 bug）。
  - **关键否定结论**：`harvest_keys.py` 把进程内存里**全部 1745 个 32 位十六进制串**捞出来，
    逐个当 AES 密钥去试解 **玩家本次会话下载的 6 个分片**——**0 命中**。
    同时验证过：**播放器下载下来的 .ts 仍然是加密的**（不是明文 TS）。
    → **下载路径根本不计算分片密钥；密钥只在"播放解密"时存在于上下文 schedule 里。**
  - 这与项目原有结论一致：老流程就是"播放中抓密钥 → 再解密下载包"。
  - 新增可复用工具（仍在）：`find_in_mem.py`（**带边界、自检的内存搜索器，已实测可用**）、
    `wait_ready.py`（实时轮询上下文拿密钥）。当时的 `harvest_keys.py`（捞 32 位十六进制串 +
    批量试解）与 `compare_tk.py`（清单 vs 上下文比对，已确证 tk 一致）**已按工单 03 删除**——
    它们的能力现在由 Rust 的 `keyscan::recover` 承担，见该工单的判定表。
- **结论（务实）**：既然密钥只在播放时存在，可交付的链路是
  **「打开课时让它播 → `wait_ready.py` 收割 (文件→密钥) → `ev2_batch.py` 下载并解密 → MP4」**。
  这与项目已有做法一致，只是把"沙箱里抓"换成了"宿主机上抓"。要提升效率可配合调速/拖进度条。

### ✅ 已实现并实测跑通：`tools/parser-tools/ev2_grab.py`（第四轮）
一条命令完成「边播边收割密钥 → 抓清单 → 多线程下载 → 解密 → 合并 → 转 MP4」：

```
python ev2_grab.py --out ev2_out --mp4        # 然后打开课时让它播
```

**实测结果**：对课时 `c628090a-…`，播放期间收到 100 个分片清单，其中 60 个被播放器解密并
被工具收割到密钥 → 全部下载+解密成功 → 合并出 `c628090a-1c0.partial.ts`（46 MB），
`ffprobe` 识别为 **H.264 2992×1682 + AAC，时长 600 秒**，可用。分片连续性计数器跨文件递增，
证明顺序正确。剩下 40 个分片没播到，所以标为 `.partial.ts` 且拒绝转 MP4（不静默产出残缺成品）。

**重要数据形态发现**：课时**下载/播放时清单是「每响应 1 个分片」下发的，且 `idx` 恒为 0**
（早期抓到的 5/30 分片一页的是另一种请求）。所以合并**不能按 idx 做键**（会把分片折叠掉），
必须按分片文件名做键、排序优先用 idx、idx 不可用时退回**到达顺序**。这是让工具能跑通的关键修复。

**实现要点**：
- 密钥来自播放上下文 `+0x120`（`aes_key_from_schedule`：前 16 字节原样 + 后 16 字节 MixColumns 还原）；
- 清单靠 hook `zlib!inflate/uncompress` 拿明文（响应路径是 AES 解密后再 inflate）；
- 只对**已拿到密钥**的分片下载+解密，失败分段最多重试 `--max-attempts` 次；
- 已解密分片缓存在 `dec/`，**重跑自动续传**（不必重播已看过的部分）；
- 结束后按序合并；缺失则输出 `.partial.ts` 并非零退出，完整才转 MP4。

**已知限制（本质限制，非 bug）**：每个课时必须**播一遍**才能拿到它的全部分片密钥——
因为密钥只在"播放解密"那一刻存在于内存里（已用 1745 个内存十六进制串全量试解证伪了"密钥在
下载路径里"的可能）。提高效率：调速播放 / 拖进度条跳段。

**该工具经过一轮对抗性审查**（3 视角 + 逐条反驳验证），确认的 13 个缺陷（含"未收割的 in-flight
结果丢失"、"失败分段无限重投导致死循环"两个高危）已全部修复。

### ✅ Rust 实现：`src/grab.rs` + `evmedia grab`（第五轮，纯 Rust、无注入）
用户要求把整条链路用 Rust 实现，已完成并实测可用：

```
cargo build --release
./target/release/evmedia grab --pid <EVPlayer2 pid> --output rust_out --mp4
```

**关键设计（比 Python 版更干净：完全不需要 frida）**——播放器内存里同时有两个必需数据：
1. **播放上下文**：vtable 落在 `PlayerLibRender56_vs.dll + 0x802000..0x804000`（用
   `CreateToolhelp32Snapshot` 拿模块基址后在内存块里做范围比对预筛，避免每 8 字节一次
   `ReadProcessMemory`）；`+8` 是**分片序号**，`+0x18` 文件名，`+0x120` 解密后的 schedule，
   `+0x268` 掩码，`+0x28a` token。
2. **签名分片 URL**：形如
   `http://cn<busid>.evplayer.cn/<uuid>/119354-<uuid>.ts?bid=..&sid=..&t=..&v=2.0&sign=..`
   —— 播放器自己构建的完整下载地址，直接扫 `.ts?bid=` 反推即可。

**顺序问题已解决**：上下文 `+8` 的 `index` 就是分片序号，实测某课时 **259 个上下文 `0..258`
完全连续**，所以按 index 排序即可正确拼装（Python 版当时只能退回"到达顺序"）。

**实现要点**：`schedule_key`（MixColumns 还原）、`decrypt_segment`（mask=XOR(MD5(文件名)[:16]
的 ASCII) → AES-256-ECB → 去 `#` 填充 → 校验 188 字节 TS）、按 index 命名 `dec/%06d.ts`、
**启动时扫描 `dec/` 续传**、多线程并发下载、失败重试上限、缺失则输出 `.partial` 并非零退出、
`--mp4` 用 `-map 0:a:0?`（可选音轨）调 ffmpeg。

**实测**：完整跑通课时 `08. 依赖注入` —— 259/259 个分片全部收割、下载、解密、按 index 合并，
产出 `rust_out/lesson.ts` + `lesson.mp4`（195 MB / 43:04 / 1920×1080）。断点续传、
实时收割（`keys`/`urls`/`segments`/`done` 计数）、并发下载解密都验证可用。修复过程中定位的两个真实 bug：
(a) 文件名校验把 `.ts` 后缀也算进十六进制
导致 URL 全部被拒；(b) 掩码字段存的是**指向字符串的指针**而非字符串本身，"按 hex 串位置反推对象
地址"的思路不成立，必须回到 vtable 范围预筛。
- 注意：本构建用 `probe_at.py`、`fish_api_plain.py` 这类**轻量**脚本；
  `watch_key.py` 是重的那版（96 页内存监视 + 全内存重扫），**会把播放器拖死，别直接跑**。
  （`walk_aes.py` 已按工单 03 删除：它钩的 AES T 表点其调用方在堆上动态生成的代码里，
  沿栈上溯走不到任何东西——那是结论，不是脚本的 bug。）

### ✅ 自动扫描（`--sweep`）：把播放头推回缺口（第六轮，已实测跑通全课时）

**问题**：分片密钥只在"播放器解密该分片"那一刻存在。播放头**已经播过**的分片如果当时没被工具
收到（工具没在跑、或播放器崩过），它的密钥就永远不会再出现——干等是等不出来的。

**关键发现（本会话最大的可复用收获）**：**向播放器自己的窗口 PostMessage `WM_KEYDOWN`/`WM_KEYUP`
（VK_LEFT / VK_RIGHT）就能驱动它前后跳转**——不需要抢焦点、不需要注入、不需要知道任何 UI 坐标，
只要窗口句柄。之前一直卡在"怎么自动控制播放器"，答案就是这么简单。

**实测**：课时 `c628090a-…`（08. 依赖注入）播放完后缺 32 个分片（123、131–134、139、184–209）。
手动按上述方式向 `hwnd=0x680668` post 300 次 VK_LEFT，60 秒内缺口全部补齐 → `done=259/259`
→ 合并出 `rust_out/lesson.ts`（209,156,016 字节）→ `lesson.mp4`（195 MB，**43:04**，
1920×1080 H.264 High + AAC 48kHz 立体声）。抽帧检查（每 5 分钟一张，3×3 拼接）确认内容连续、
顺序正确（FastAPI 依赖注入那一课，代码从 `Depends` 演进到 `AnnotatedDepends`）。

**已固化进 `src/grab.rs`**（`--sweep`，默认开；`--no-sweep` 关闭；`--sweep-gap-ms` 调按键间隔）：
- `seen` 累积**本课时**的 index 全集（只用"文件名匹配到本课时签名 URL"的那些，避免把内存里
  其他课时的上下文算进来）→ `gap = seen - ok` 就是缺口；
- 静默 `SWEEP_AFTER=5` 个 poll 后，取**最早**的缺口 index 作为目标；
- `seek_window_to()`：**实时密钥窗口既是"已解密集合"也是"播放头读数"**——窗口起点≈播放头，
  终点≈播放头+70。据此算出还差多远，post 一批方向键，**量一下窗口实际移动了多少**得出
  "每次按键移动几个分片"，下一批按这个自校准值来算（首轮先假设 1.0）。来回都能走
  （目标在窗口前面就按右键）。收敛不了或者按键没反应就判定"该构建没绑方向键"，停手不再骚扰播放器；
- 上限 `MAX_SWEEPS=20`，避免死循环。

**验证**：无缺口时该分支正常走过、不误触发、不 panic；两次独立合并产出**字节完全相同**的
`lesson.ts`（209,156,016 字节），说明合并是确定性的。
（注意：真正"有缺口 → 自动 seek 补回"的端到端路径还没在自然缺口上跑过——这次是手工按同样
的 PostMessage 方式补的；机制已验证，控制环是照着它的行为写的。）

### ✅ 工程化重构：5 crate workspace + Tauri 桌面端（第七轮）

用户要求：继续用 Rust；`.rs` 分模块且**单文件不超过 500 行**；注意项目结构；做成**以 CLI 为核心
的桌面软件**，界面的交互就是执行 CLI。

**现在的结构**（`src/` 已不存在，全部迁入 `crates/`）：

```
crates/evmedia-contract/   argv + 事件 schema + 退出码 + Reporter（唯一共享面，无业务逻辑）
crates/evmedia-core/       catalog/download/decode/crypto/harvest/media —— 零 Windows API
crates/evmedia-win/        process/scan/playhead/capture/source —— 唯一持有 windows-sys 的 crate
crates/evmedia/            CLI（main.rs 60 行 + commands.rs 64 行）
crates/evmedia-gui/        Tauri 桌面端（Rust 后端 + 静态 HTML/CSS/JS 前端，无打包器）
```

依赖方向 `contract ← core ← win ← evmedia`，`contract ← evmedia-gui`。**`evmedia-gui` 的
Cargo.toml 里没有 `evmedia-core` / `evmedia-win`** —— "GUI 只能执行 CLI"因此是链接期事实而不是
纪律，`crates/evmedia/tests/source_budget.rs` 会在有人把依赖加回来时测试失败。

**最大文件 288 行**（`harvest/grab.rs`），其余都更小；500 行上限和 420 行"舒适线"都由测试守着。

**两道接缝**：
- `harvest::Harvester`（trait）：core 里的收割循环因此完全不碰 Windows，`evmedia-win::WinSource`
  是唯一真实实现，测试用夹具实现同一 trait —— 于是**不用播放器、不用 Windows、不用网络**就能测
  续传/重试/完整性判定/合并（`tests/grab_loop.rs` 4 个用例全过）。
- `evmedia-contract`：GUI 从 `spec::describe()`（运行时遍历 clap 定义）**生成表单**，不在 JS 里
  手写第二份参数表；提交前还要用 `Cli::try_parse_from` 再解析一次，所以 GUI 不可能跑出 CLI 会拒绝
  的命令。`argv_roundtrip.rs` 保证 `ToArgv` 与 clap 解析互为逆运算。

**CLI 行为保持不变**（R5）：默认模式下 stdio 与重构前逐字相同（`adapters`/`tree` 已比对）。
新增两个**只在显式传参时生效**的全局开关：`--json-events`（stdout 变 JSON Lines，人读文本改走
stderr）和 `--stop-file`（GUI 用它做优雅取消）。退出码仍是 0/1/2，富语义走
`finished.status = complete|partial|nothing|cancelled|failed`。契约定在 `docs/CLI-CONTRACT.md`。

**取消**：GUI 先创建 `<output>/.evmedia-stop`（CLI 在每个 poll 边界和 sleep 内检查，≤250ms 察觉），
再兜底用 Job Object 收掉整棵进程树（否则 ffmpeg 孙进程会变孤儿）。`AssignProcessToJobObject` 失败
时降级为 `child.kill()` 而不是 panic。

**`capture-ev` 重写（唯一"从死变活"的行为改动）**：删掉了 `src/evplayer_windows.rs` —— 它的
`find_salt` 依赖已被证伪的 `MD5(tk+文件名+盐)`，在本构建下**恒返回 None**，即该命令今天 100% 以错误
退出，所以改它不构成回退。新实现（`evmedia-win/src/capture.rs`）走 `active_keys` + `schedule_to_key`，
即 `grab` 已验证的同一路径；输出保持 `{tool, segment_count, segments}` 三字段形状。**前置条件变了**
（要求全部 .ts 成员都已被播放器解密），写进了文件头。**尚未在真实课时上复验。**

**桌面端**：Tauri v2.11 + WebView2（用户机器已装 Node 24 / pnpm / WebView2 152）。前端是**静态
文件**（`ui/index.html` + `app.js` + `style.css`），`frontendDist: "ui"`，所以 `cargo build` 直接出
二进制，不需要 npm/node_modules/打包器。图标由 `tools/make_icon.py`（纯 stdlib，超采样）生成到
`crates/evmedia-gui/icons/`（tauri-build 强制要求 `icon.ico`）。窗口标题栏常显 CLI 路径+版本；
日志面板是 CLI 的 stderr（与终端逐字一致），进度条来自 JSON 事件。

**测试**：`cargo test --workspace` → 24 个用例全过，包括 500 行上限、core 不含 windows_sys、
GUI 不链接 core、argv 往返、crypto 两种密钥表示不可互换、以及夹具驱动的收割循环。

### 卡点 B：API 请求 `params` 的加密密钥（用于"自动遍历目录 → 逐视频拉清单"）
- 明文截获已验证：`params` 是 **AES-ECB** 加密（16 字节块独立；同 endpoint 两条请求共享大量块）。
- AES 实现**静态链接**在 `PlayerLibRender56_vs.dll`（不在 CNG/CryptoAPI，故 hook bcrypt 无输出）。
- 本会话已定位到 AES 的 T-table 实现：**RVA `0x792c78`**（该指令读反 S 表，
  `PlayerLibRender56_vs.dll` 基址 `0x7ffe75ae0000`）；反 S 表在文件偏移 `0xb5eea0`（RVA `0xb5faa0`），
  T 表在 RVA `0xb5daa0`+。已 hook `0x792c78` 并抓到寄存器与轮密钥/状态内存。
- **建议做法之一（最省事）**：把应用自己的 AES 当**预言机**——用 frida RPC 调应用的加/解密函数，
  我们自己的 Python 负责 HTTP。**之二**：hook 密钥派生函数读出密钥后，用 pycryptodome 自己加密。
- 一旦拿到 API 密钥，就能重放 `POST /student/getPlayTimeKeySignEVS*` 等接口，按目录逐个取清单，
  实现完全自动化（不再需要"播放哪个抓哪个"）。

## 其它可用线索

- `AppData\Local\EVPlayer2\conf\Admin.conf`：`ev2_userslist`（base64/混淆的登录数据）、
  `cache-<stu_id>-<course_id> : |id|id|…|`、`file_cache`（base64→`cache-1113723-446794`）、
  `ev2_savepath`、每课程的 `<prefix>/119354-<uuid>.evs : <score>` 缓存索引。
- `AppData\Local\EVPlayer2\log\AdminYYYY-MM-DD.log`：明文记录了下载 URL、重试、
  `uploadKey=<...>.evs`、HTTP 头等（可当作分片 URL / 下载行为的旁路来源）。
- 沙箱身份与算法细节：`.codex\sessions\` + `EVPlayer2_分析结果.md`。
- 本会话产生的分析脚本 `tools/parser-tools/_*.py` 为临时探索脚本，可删。**已经删了**（工单 03）。

## 仓库里现成的东西

- Rust 工作区 `crates/`（第七轮起 `src/` 已不存在，见上文"工程化重构"）：
  `evmedia` 是 CLI，`evmedia-core` 是全部业务逻辑，`evmedia-win` 是唯一碰 Windows API 的 crate。
- `tools/parser-tools/EVPlayer2通用解密工具/EVPlayer2_decode.py`（厂商的按清单解密器，清单路径已废）。
- `tools/parser-tools/capture_all.py`（API/明文截获工具，工单 08 的主要仪器）。
- **研究台（`tools/parser-tools/`）已在工单 03 分类**：18 个被产品覆盖或在本构建上不可能工作的脚本
  已删除，13 个仪器原样保留，逐条判定与理由见 `.scratch/offline-keys-and-bench/issues/03-*.md`。
  下文提到的 `ev2_grab.py`、`ev2_batch.py`、`find_segments.ps1`、`harvest_keys.py`、`compare_tk.py`、
  `walk_aes.py`、`一键抓取.cmd`、`解密转MP4.cmd` 都是**历史名**，文件已不在。
