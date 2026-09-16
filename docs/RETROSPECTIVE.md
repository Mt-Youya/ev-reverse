# 项目复盘：EVPlayer2 离线解密（写给下一个接手此任务的 agent）

这份文档是**作战依据**，不是汇报。它的用途是让下一个 agent 在不重复踩坑的前提下继续推进。
所有事实都回到磁盘核对过（`git log`、源码、捕获目录、导出目录、脚本本体），每条都附可复现的命令。

阅读顺序建议：先读「目标与达成状态」和「验证方法学」两节，再读「当前未解的问题」，最后按需翻地址表。
**最重要的一句结论写在最前面：截至本文件写作时，工具链能稳定取出正确的密钥、正确的顺序和结构合法的
MPEG-TS，但解出的视频画面是灰的——「能稳定解密成可看视频」尚未达成。**
（这句话的适用范围后来被划出来了：同一条流水线在三节课上确实出了画面，其余仍是灰的，见 §13.7。）

---

## 1. 环境事实（先读这节，它决定了你能怎么干活）

| 事实 | 值 | 怎么核对 |
| --- | --- | --- |
| 仓库根 | `D:\Codes\github\ev-reverse` | `cd D:\Codes\github\ev-reverse` |
| 当前分支 | `feat/recover-segment-keys` | `git rev-parse --abbrev-ref HEAD` |
| 目标 DLL | `D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll`，12,828,160 字节（12.23 MB） | `Get-FileHash <dll> -Algorithm SHA256` → `8BD3E21C14AD3C760B0C2400CE38D8BE3AFBDAF9B347AF6A40F05C1788229635` |
| DLL 映像基址 | `0x180000000` | Ghidra 里所有地址都带这个基址 |
| 播放器 | `D:\Learning\EVPlayer2\EVPlayer2.exe`，5.0.5 | — |
| 下载目录 | `D:\Downloads\EVPlayer2Downloads` | 见下 |
| 导出目录 | `D:\ev-export` | 见下 |
| Python | `C:\Users\Yonjay\.conda\envs\subgen\python.exe`（3.13.13）。**`python` 不在 PATH 上**，直接敲 `python` 会命中 Microsoft Store 别名并报 "Python was not found" | `& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' --version` |
| 本机代理 | `http://127.0.0.1:7897` | — |

### 代理是硬性要求，不是加速选项

- 直连 GitHub：约 **170 KB/s**；走代理：**8.2 MB/s**（同一 URL 实测）。
- **Maven 不配代理就完全连不上 Central**。配置在 `%USERPROFILE%\.m2\settings.xml`（已存在，两个 `<proxy>` 条目，`127.0.0.1:7897`）。
  经代理下载偶发 `(bad_record_mac) Tag mismatch`，**重跑构建即可，Maven 会从缓存续传**。
- git 全局已配 `http.proxy` / `https.proxy`。裸 `curl.exe` 不会读 git 配置，需要显式 `-x http://127.0.0.1:7897`。

### 下载目录与环境现状（写作时刻实测，会随播放器下载而增长）

```
D:\Downloads\EVPlayer2Downloads   10,636 个文件 / 7.12 GB
  .ts       10,498 个   7,158.0 MB     ← 加密分段（现代分段格式）
  .evtemp      115 个     127.9 MB     ← 下载中的临时文件
  .part         16 个       5.4 MB
  .tmp           7 个       1.5 MB
```

**这个目录里没有任何索引**：没有数据库、没有清单、没有「文件 → 课程」映射。
唯一带元数据的是播放器的配置：

```
C:\Users\Yonjay\AppData\Local\EVPlayer2\conf\Admin.conf
```

它包含：`ev2_savepath`（下载路径）、`<b>-<uuid>/119354-<lesson-uuid>.evs : <进度>`（已下载课程）、
`cache-1113723-<lessonId> : |900902|901748|...|`（每课的播放位置，单位毫秒）、`playhistory-*`、
以及一个 base64 的 `ev2_userslist` / `file_cache`。
**它不含 `tk`，也不含文件到课程的映射**（核对方法：`Get-Content C:\Users\Yonjay\AppData\Local\EVPlayer2\conf\Admin.conf`）。

---

## 2. 目标与达成状态

### 2.1 原始目标一：让工具自己取清单 —— 已完成并验证

`evmedia fetch` 现在自己构造请求、自己签名、自己解密响应，不需要播放器内存也不需要抓包。

验证方式（可复现，但需要一枚有效 JWT）：

```powershell
cd D:\Codes\github\ev-reverse
.\target\release\evmedia.exe fetch --from-capture tools\parser-tools\captured\kdf_round3.jsonl `
    --token (Get-Content verify_out5\token.txt -Raw).Trim() --output verify_out5\live3_list.json
# 实测输出：5 segment(s) signed to verify_out5\live3_list.json
```

`--from-capture` 从 `probe_kdf.py` 的 JSONL 里取**最新一条签名前像**，从中解析 `evs_playkey` 与 `ts_liststr`，
再用当前时间重新签名。`--from-body` 从抓到的请求体里取同样的两个字段。

### 2.2 原始目标二：补齐剩余两个调用点 —— 已定性，但**没有抓到它们的实时载荷**

两个从未触发的解密调用点是：

| 调用点 | 真实身份 | 证据 |
| --- | --- | --- |
| `0x1B480` | **`Decryptor_V8A` 虚表第 4 槽**（12 槽表，起点 `0x801420`） | RTTI：`vtable-8` 指向 COL，COL 的类型描述符名为 `.?AVDecryptor_V8A@@` |
| `0x34390` | **EV4/V4A 取密钥响应的解密步骤** | `Decryptor_EV4`（表 `0x8021B8`）槽 3/4 → `0x32070`/`0x310F0` → `0x34390`；`Decryptor_V4A`（表 `0x802310`）槽 3/4 → `0x37080`/`0x361A0` → `0x34390` |

可达链（导出名反查，提交 `2f0a406`）：

```
MediaPlayer::playFile(QString const&)   0x48ED0
  -> MediaPlayer::resume()              0x49220
  -> EVPlayer::play(std::string, ...)   0x151C0
  -> 0x385B0   分配 0x58 字节对象
  -> 0x174C0   8.4 KB，读加密字符串表
  -> 0x1B170   构造 Decryptor_V8A
  -> Decryptor_V8A slot 3 = 0x1B480
```

**为什么它们从不触发**：解密器是**按被播放文件的格式**选的，这两条属于 V8A / EV4 / V4A 老格式。
实测证据：下载目录里 10,498 个 `.ts` 全是现代分段格式；全盘（深度 3–4）**没有** `.v4a`/`.ev4`/`.v8a`/`.v3a`/`.ev5`–`.ev7`/`.evs` 文件。
会话内累计数小时的真实使用（下载、播放、拖动、断网、完整 EVS 链）里，`0x1EA10` 报出的调用者**只有 3 个**：
`0x0283F2`、`0x03B435`、`0x042C49`。

复现方式：

```powershell
# 每轮的调用者分布
& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' -u tools\parser-tools\crypto_classes.py `
    D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll
# 老格式文件搜索
Get-ChildItem D:\ -Recurse -File -Include *.v4a,*.ev4,*.v8a,*.v3a,*.ev5,*.ev6,*.ev7,*.evs -Depth 4 -ErrorAction SilentlyContinue
```

### 2.3 扩展目标：把下载目录稳定解密成可看视频 —— **未达成**

分成三层看，前两层解决、第三层没解决：

| 层 | 状态 | 证据 |
| --- | --- | --- |
| 密钥 | ✅ 正确 | 音频在同一份密文里解码干净；`md5_hex(tk+文件名+"20220507")` 与 258+42+56 组历史三元组吻合 |
| 顺序 | ✅ 解决 | 按每段自身 PTS 排序，309 段全部归位；时间轴缺口可量化 |
| 容器/结构 | ✅ 正确 | TS 同步 100%、AUD/SPS/PPS/IDR 结构完整、`ffprobe` 读出 H.264 High@4.0 / yuv420p / 1920×1080 / 30fps |
| **画面** | ❌ **灰色** | 全解码 1,874–110,200 行错误；抽帧是灰底 + 竖条（解码器错误遮蔽）。**这是逐文件的结论，不是流水线的性质**：三节课正常出画面，见 §13.7 |

### 2.4 哪些「完成」后来被证明是错的（必须记住）

1. **「导出已通过验证」是错的。** 见 §5.3。曾经的验证标准是「TS 同步字节计数」+「ffprobe 能读出流参数」，
   这两者对一个**结构合法但画面全灰**的文件完全成立。文档 `docs/KEY-DERIVATION.md` 里那段
   「18,854 packets with 18,854 sync bytes … `ffprobe` reads H.264 2992×1682 plus AAC」曾被当作成功证据，
   现已在该文件里加了专门一节（"What those checks do not prove, and what does"）更正。
2. **「两个静默调用点属于我们没抓到的接口」是错的。** 它们属于**别的媒体格式的解密器**（§2.2）。
3. **「加密字符串能静态解开」是错的。** 它们由 Bridge 在运行时解密，密码学不在 DLL 里（§5.6）。

---

## 3. 走对的方向（每条给证据与复现方式）

### 3.1 请求签名 `MD5(canonical + "&&" + "ieway.cn@20200611")`

- 常量在 `crates/evmedia-core/src/api.rs:52`：`pub const SIGN_SECRET: &str = "ieway.cn@20200611";`
- 参与签名的字段 9 个，定义在 `crates/evmedia-core/src/api.rs:41` 的 `SIGNED_FIELDS`（含 `type`）。
- 活捉的前像（`tools/parser-tools/captured/kdf_round3.jsonl` 里 `md5_input` 字段）：

  ```
  app_version=5.0.5&evs_playkey=…&need_zip=1&os_name=windows&platform=1&platform_type=1
  &req_time=…&ts_liststr=…&type=0&&ieway.cn@20200611
  ```

- 验证：对 `captured/bodies/` 里 **238** 个真实请求全部复算成功；测试固定在 `crates/evmedia-core/tests/api.rs`。
- 复现：`cargo test -p evmedia-core --test api`

### 3.2 密钥推导 `key = MD5_hex(tk + 文件名 + "20220507")`

- 实现在 `crates/evmedia-core/src/crypto.rs:84` `key_from_tk`；第三输入常量在 `crypto.rs:77` `DERIVATION_EXTRA = "20220507"`。
- 该常量是**运行时常量**：它由播放器向 Bridge 按混淆名（`m4OEgjo4nU`，DLL `.rdata:0x803550`）索取，
  所以在任何文件里都搜不到（核对过 110 个播放器文件、`%LOCALAPPDATA%\EVPlayer2`、下载目录）。
  活捉点在 `0x1FD60`（`std::string = MD5_hex(input)` 包装）。
- 累计验证：258/258（`ev2_out/keys.json` × `manifests.capture`）、42/42（`ctx_dump.json`）、
  56/56（播放中玩家的活动上下文）、`triple.json` 单条吻合。
- **两个必须分清的密钥表示**（`crypto.rs` 顶部注释）：
  - 活路径：32 个十六进制**字符**直接当 32 字节密钥用（`key_from_text`）。
  - manifest 路径：同一个字符串被 hex 编码成 64 字符，需要**解码**回 32 字节（`key_from_hex`）。
- 复现：`cargo test -p evmedia-core --test derivation`（该测试钉住 13 组三元组，并会在 extra 被去掉时失败）。

### 3.3 请求/响应体：AES-128-ECB + gzip + 信封

- 常量：`crates/evmedia-core/src/api.rs:29` `DATA_KEY = b"x!@#y.cn_xnk0506"`，`api.rs:34` `PROTOCOL_VERSION = 202`。
- **`version` 必须是 202**：写 200 会得到误导性的 Go 风格 JSON 解析错误（`invalid character '/' looking for beginning of value`）。
- 填充必须是 **PKCS#7**：用零填充会得到 `invalid character '\u0080'`。
- 响应信封 `{errcode, errmsg, uuid, zip, encrypt, result}`；`result` = base64 → AES-128-ECB → gzip。
  gzip 流会在缓冲区结束前结束，**必须用流式解压**（`gzip.GzipFile` / `GzDecoder`），不要整块解压。
- 复现：`cargo test -p evmedia-core --test api`；离线解一条响应：`tools/parser-tools/decrypt_response.py`。

### 3.4 descriptor 协议（两级密钥）

descriptor 是一个 JSON，用常量 `11585ec1b1f8f30e` 解密，字段实测为：

```
host, req, dkey, dkey_ver, cache_key, base_key, ext, tiku, water_ts, evc_val
```

实测样例（今天抓到的三条，可用会话记录复现）：

```
{host: https://en2v4.ieway.cn, req: /student/getPlayTimeKeySignEVS20231103,
 dkey: x!@#y.cn_xnk0506, dkey_ver: 202,
 cache_key: evs_2026_119354_12_0_450252_63aac7ca-13ef-42ab-b0d5-aea046670f61,
 base_key: cf1bcf04d55a1ba7ba8953e4870c0f93, ext: .evs, tiku: 0, water_ts: "", evc_val: 0}
```

关键事实：**`evs_playkey`（签名前像里的字段）就是 descriptor 里的 `cache_key`**（同一串 `V4bsTWiO…` 前缀）。
数据密钥随 `dkey_ver` 轮换，所以旧 `dkey_ver` 下的归档捕获事后无法解开（`tools/parser-tools/replay_bodies.py` 记录了这件事）。
descriptor **不在磁盘上**，它本身就是流量（`find_descriptors.py` 在安装目录、`%LOCALAPPDATA%\EVPlayer2`、下载目录里都找不到）。

### 3.5 `evmedia fetch` 离线取清单（无需播放器）

- 请求构造 + 签名 + 响应解密全在 `crates/evmedia-core/src/api.rs` 的 `ListRequest` / `fetch_list`。
- `DEFAULT_HOST = https://en2v4.ieway.cn`（`api.rs:36`），`LIST_ENDPOINT = /student/getPlayTimeKeySignEVS20260515`（`api.rs:37`）。
- token 处理有个坑：抓到的 header 里已含 `Bearer ` 前缀，再拼一次会得到 `Bearer Bearer …`，
  服务器回 `长时间未登录，请退出重新登录`（-123），看起来像 token 过期，其实是前缀重复。已在 `fetch_list` 里剥离。
- `download` 现在**同时接受**手写清单和 `fetch` 产出的清单（检测 `k_l` 字段），并且**按清单里的分段文件名落盘**
  —— 那个文件名是密钥派生和 XOR 掩码的输入。见 `crates/evmedia-core/src/download.rs` 的 `load_input`
  与 `crates/evmedia-core/src/playlist.rs` 的 `to_download_manifest`（提交 `9deb767`）。

### 3.6 按 PTS 排序重组（顺序问题的唯一正解）

工具：`tools/parser-tools/merge_by_pts.py`。

做法：解密每段 → 读该段**第一个视频 PES 的 PTS** → 按 PTS 排序合并 → 报告时间轴缺口。

必须知道的两个实现细节（都踩过）：

1. **PES 头不在 TS 包的第 4 字节**：视频包通常带 adaptation field，
   payload 起点 = `4 + (1 + packet[4] if control == 3 else 0)`，`control = (packet[3] >> 4) & 3`。
   忽略 adaptation field 会让每一段都报 "no video PTS"。
2. **不要通过 PAT/PMT 找视频 PID**：HTTP 分发的分段通常**不含节目表**，直接从 PES 的 `stream_id`
   落在 `0xE0..0xEF` 判断即可。

实测输出（`--lesson d5c3302f`）：

```
lesson d5c3302f-2363-4534-83b0-de45bd1e4135: 309 of 309 captioned segment(s) on disk
decryption self-check: sync byte in 100.00%..100.00% of packets over 309 segment(s)
309 segment(s) placed, 0 unusable
first PTS 1632000 (18.133s), last 279132000 (3101.467s), typical step 8.333s
61 gap(s) or repeat(s) in the timeline, first few:
  after 119354-1e09ad56-c070-4205-9e40-78995c92935a.ts: +16.667s
...
wrote D:\ev-export\d5c3302f-2363-4534-83b0-de45bd1e4135.pts.ts  (214157192 bytes)
```

含义：每段约 8.333 秒；61 处 +16.667 秒（= 2 倍步长）说明**约 372 段里缺了 63 段**。
**这就是「下载目录不等于完整课程」的量化方式**——以后别再用「文件都在」当完整性判据。

### 3.7 用导出表 / RTTI 定位类与虚表

- DLL 导出 **185 个 C++ 方法**，带名字。查法（PowerShell 里跑 Python，解析 PE 导出表）见 §9 的
  `crypto_classes.py` 与本节末尾命令。名字直接给出了语义，例如
  `?playFile@MediaPlayer@@QEAA_NAEBVQString@@@Z` = `MediaPlayer::playFile(QString const&)`。
- RTTI 定位类的正确姿势：`vtable[-1]`（即 `vtable-8`）是 **Complete Object Locator 的虚拟地址（VA，不是 RVA）**；
  COL 的第 4 个 dword 指向**类型描述符结构体**，其名字字段在 `+16`。
  这三点错一个就读不到类名——本项目为此错了两次（§5.5）。

复现：

```powershell
& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' -u tools\parser-tools\crypto_classes.py `
    D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll
```

### 3.8 离线全链路（当前唯一能跑通的端到端流程）

```
evmedia fetch   --from-capture <kdf jsonl> --token <JWT> --output list.json
evmedia download list.json enc --parallel 5
evmedia derive  --playlist list.json --input enc --output manifest.json
evmedia decode-ev enc manifest.json lesson.ts
```

`evmedia decode-ev` 的清单来源被 `crates/evmedia-core/src/playlist.rs:TOOL` 校验：

```rust
pub const TOOL: &str = "EVPlayer2 5.0.5 derived-from-segment-list";
```

---

## 4. 已经产出但**画面不可用**的批量导出（别把它当成成品）

工具：`tools/parser-tools/export_cached.py`（按捕获顺序重排 + 逐课导出）。

写作时刻实测（`D:\ev-export\report.json`）：

```
15 节课, 1,633 段, 1,053,258,344 字节 (1.05 GB), 5,602,438 个 TS 包 / 5,602,438 个同步字节
  3428f5c3 176/176   94,041,548 B      4589d038 287/287  145,439,056 B
  63aac7ca 190/190  106,585,660 B      7410427e 154/158  144,159,716 B
  91773801 163/165   68,427,300 B      ab0833eb  95/95   106,111,148 B
  cca0f596  97/101   52,850,372 B      d5c3302f 309/309  214,157,192 B
  dc43ebc2   6/13     3,358,432 B      e3508944  70/76    45,339,772 B
  ...（共 15 行，完整见 report.json）
```

**覆盖率**（`collect()` 实测，写作时刻）：

```
清单文档数（含同文件内多份）: 876
去重后的课程数: 15
清单命名的分段总数: 1,771
其中盘上存在: 1,745        → 占下载目录 10,638 个文件的 16.4%
```

也就是说：**手里有密钥的分段只占下载目录的约 1/6**，其余因为没有 `tk` 而完全无法解密。

> 注意：下载目录在播放器运行时持续增长。上面三个数字（10,636 / 10,638 / 1,745）取自不同时刻的两次测量，
> 差几个文件是正常的。**要做对比就把命令重跑一遍取同一时刻的数**，别引用本文档里的绝对值。

**这些文件的画面大多是灰的**——三节是正常画面，见 §13.7；验证命令见 §6。

---

## 5. 走错的方向 / 被推翻的假设

这一节是本文档的主要价值。每条写清：当时怎么想、什么证据推翻、代价。

### 5.1 把下载目录当「整节课」

- **当时怎么想**：下载目录里那批 `.ts` 就是一节课的分段，凑齐即可合并。
- **推翻证据**：捕获到的清单每次只命名**一个窗口**（5–6 段），`idx` 是**窗口内**序号（0..4）。
  按 `idx` 做并集必然错乱——第一版 `export_cached.py` 就是这么写的，`derive` 用
  `segment indexes are not a contiguous 0..n range (N of them, 0..4)` 拒绝了 14 节课。
- **代价**：一次错误实现的批量导出 + 一节 `derive` 报错的排查。
- **正确做法**：窗口按**捕获顺序**排、窗口内按 `idx` 排（播放器顺序下载）；
  更可靠的是直接用 §3.6 的 PTS 排序。

### 5.2 把捕获顺序当播放顺序

- **当时怎么想**：探针按时间记录 payload，窗口顺序就是播放顺序。
- **推翻证据**：播放器**并发下载**（多个窗口、甚至多节课同时进行），且多个探针会话的 payload 混在一个目录树里。
  按捕获顺序合并的 214 MB 成品，全解码报 **110,200 行错误**（29,398 次 MB 解码错、28,510 次参考帧错）。
- **代价**：一轮 15 节课的批量导出全部作废（1.05 GB 无用产物）。
- **正确做法**：**用流自己携带的 PTS 排序**，并同时输出时间轴缺口。见 §3.6。

### 5.3 ⚠️ 只用 TS 同步字节当验证标准（最严重的一次，导致长期误判「导出成功」）

- **当时怎么想**：`plaintext.iter().step_by(188).all(|b| b == 0x47)` 通过对 → 密钥和掩码都对 → 导出成功。
  再加上 `ffprobe` 能读出 `H.264 2992×1682 + AAC`，就写进了文档当作已验证。
- **推翻证据**：把 `verify_out5/lesson.ts` 和 `verify_out4/live2_lesson.ts` **完整解码**（不是 probe）：

  ```
  verify_out5/lesson.ts   1888 KB → ffmpeg 全解码错误 1,698 行
  verify_out4/live2_lesson.ts 3127 KB → 1,475 行
  ```

  抽帧出来是**灰底 + 竖条**（解码器错误遮蔽），不是画面。
  同一份密文里**音频解码只有 8 行告警**——这恰好证明密钥层是对的，
  于是「同步字节」这个判据把两个完全不同的结论混为一谈。
- **代价**：**最大的一次代价**。「导出成功」被写进了 `docs/KEY-DERIVATION.md` 和历次汇报，
  浪费了后续多轮把这条当成前提的工作。修正记录见提交 `57ea09a`。
- **正确做法**：见 §6，同步字节只作为**解密自检**，绝不能作为画面正确性的证据。

### 5.4 把 `0x76b603` 当 AES 密钥编排

- **当时怎么想**：`captured/keys_by_caller.jsonl` 里 31,775 次命中都归到 `PlayerLibRender56_vs.dll+0x76b603`，
  于是写了个探针钩 `0x76B603` 想抓「每段的密钥编排」。
- **推翻证据**：钩上后第一次命中就暴露形态——`rcx` 是一段结构体指针、`rdx` 为 `None`，
  而且命中量**爆掉**（消息洪水，把一次调用拖到 180 秒超时）。
  `watch_key.py` 的文档说明真正读逆 S 盒的地址是 **`0x792c78`**，`0x76b603` 是**调用它的轮内代码**，
  也就是「每个块每轮都可能命中」的 S-box 读取点，不是每段一次的密钥编排。
- **代价**：一个走错路的脚本，已删除（`probe_aes.py`，未提交，勿重建）；一次 3 分钟的探针超时。
- **正确做法**：密钥设置点在 **`0x20EC0`**，签名在 `0x20EC0` 的 `(rcx=schedule, rdx=key, r8d=bits, r9d=direction)`；
  判断「有没有在解码」看这个钩子，不要看 S-box。

### 5.5 把 `0x266690` 当 `h264_decode_frame`（其实是 `update_thread_context`）

- **当时怎么想**：在 `.rdata` 里找同时指向 `"h264"`（`0x817F44`）和 long_name（`0x817F50`）的结构体，
  得到 `AVCodec` 起点 `0xBBB2C0`；再把里面的代码指针里**最大的**那个（2500 字节，偏移 `+0x80`）当 `decode`。
- **推翻证据**：反编译 `0x266690` 出来是 `FUN_180266690(param_1, param_2)`：两个参数、比较
  `+0x72F4`/`+0x7958`/`+0x82A0` 等解码器上下文字段、`memcpy(..., 0x180)` —— 这是
  **`ff_h264_update_thread_context`**。而且用它做钩子时参数形态完全不可能是 AVPacket：
  `r8` 竟是函数自身地址、`r9` 指向一段代码桩。
- **代价**：两次错误钩子 + 一次 150 秒空跑。
- **正确定位方法（不依赖结构体偏移）**：从 ffmpeg 自己的报错串反查调用链——

  ```
  字符串 "error while decoding MB" @ 0x903920 / 0x84E418
    ← 引用点 0x265624，所在函数 0x265624..0x2656xx
      ← 调用者 0x265EE0..0x26620C   (decode_slice)
        ← 调用者 0xB9AC4..0xBA27D   (分派)
          ← 调用者 0xB9648..0xB993B (call at 0xB97A9)
  0xB9648 无任何调用者 → 它就是函数指针槽 → 即 AVCodec +0xB0 处的 decode
  ```

  **`h264_decode_frame` = RVA `0xB9648`**（755 字节）。用它做钩子立刻抓到了真实解码输入（§7.1）。

### 5.6 以为加密字符串能静态解开（实际由 Bridge 运行时解密）

- **当时怎么想**：DLL 里有 296 个 UTF-16 的 `m4OEgjp…` base64 串，解开它们就能拿到接口名/格式名。
  依次试了：AES-128-ECB + `11585ec1b1f8f30e`、AES + `x!@#y.cn_xnk0506`、
  单字节 XOR、按已知明文（内存里见过的 `/student/getPlayTimeKeySignEVS20231103`）做已知明文攻击。
- **推翻证据**：全部失败。反编译 `0x174C0` / `0x34390` 看到真正的机制：

  ```
  bridge = *(0xbf26c8 + 0x78)
  bridge->vtable[0xd0](bridge, out, wchar_ptr, len)      // 解密一个字符串
  bridge->vtable[0x40](bridge, out, value, 0xa6)         // 按数字 id 取一个值
  ```

  也就是说**密码学不在 DLL 里**（在 Bridge，很可能在 `EVPlayer2.exe`），
  而且那两个「任何文件里都搜不到」的常量（`20220507`、`ieway.cn@20200611`）正是第二条路径取出来的。
  另一个坑：这些字符串在表里**首尾相连、没有分隔符**，按 NUL 切或按正则切都会得到粘连的长串。
- **代价**：多轮密码学尝试（都记在提交历史里，`search_third.py`、`key_formula.py` 等工具是那个阶段的产物）。
- **正确做法**：要么**钩 `vtable[0xd0]`** 把明文截下来（本机可做，未做），要么继续从流量侧拿事实（已足够）。

### 5.7 把 `0x1B480` / `0x34390` 当成「没抓到的接口」

见 §2.2 / §2.4。当时假设它们属于 `searchCourseVideos`/`getCourseDetailPreView`/`playReport`/
`errorReport`/`feedBackV2`/`getEvsSignUrlByKey` 或 `tiku`/`water_ts` 那几条从未抓到的流程；
推翻证据是 RTTI 类名（`Decryptor_V8A`、`Decryptor_EV4`、`Decryptor_V4A`）。
**代价**：反复请求使用者去点界面（下载管理页、字幕、反馈、断网播放），都没用，还惹恼了使用者。

### 5.8 `mask` 用错：`MD5(filename)` 的原始摘要 vs 十六进制串前 16 字符

- **当时怎么想**（写 Python 版解密时）：`mask = hashlib.md5(name).digest()[:16]`。
- **推翻证据**：自检同步率只有 **0.12%–0.93%**，看起来像「密钥错」，实际是掩码错。
  权威实现在 `crates/evmedia-core/src/crypto.rs:106`：

  ```rust
  /// XOR mask as it exists in the live path: the first 16 ASCII bytes of `MD5(filename)`.
  pub fn mask_from_filename(filename: &str) -> [u8; 16] {
      let digest = md5_hex(filename.as_bytes());
      *<&[u8; 16]>::try_from(&digest.as_bytes()[..16]).expect("md5 hex is 32 characters")
  }
  ```

  即：**`MD5(filename)` 的十六进制字符串的前 16 个 ASCII 字符**。
- **代价**：一次「明明密钥对却全乱码」的排查。
- **附带细节**：明文末尾有 `#` 填充补齐到 AES 块，必须去掉才能得到整数个 188 字节包
  （`crypto.rs:136-142` 与 `merge_by_pts.py` 的 `decrypt()` 都实现了）。

### 5.9 其他已被修正的错误（简记）

| 错误 | 症状 | 修正 |
| --- | --- | --- |
| 外层信封 `version` 写 200 | `invalid character '/' looking for beginning of value` | 必须是 202（`api.rs:34`） |
| 用零填充替代 PKCS#7 | `invalid character '\u0080'` | 用 PKCS#7 |
| `Bearer Bearer …` 双前缀 | 服务器回 `长时间未登录…`（-123），像 token 过期 | `fetch_list` 里剥离已有 scheme |
| 把 `args[2]` 当 C 字符串读密钥 | 每个 payload 都报 `key=None` | MSVC `std::string` 的 size 在 **+0x10**、capacity 在 **+0x18**（union/buffer 占前 16 字节）。见 `capture_keys.py` 的 `sstring` |
| `nghttp2_nv` 按 16 字节步长读 | 钩子挂上但什么都不报 | x64 上是 **40 字节**：name, value, namelen, valuelen, flags |
| 用 `readUtf8String` 读命中 | 扫描报「0 命中」，其实把真命中全丢了（映射末尾会抛异常，`continue` 掉） | 逐字节读到 NUL，异常不丢命中。见 `dump_endpoints.py` |
| 反复 `frida.attach` 同一进程 | `VirtualAllocEx returned 0x00000005` | 重启播放器，或用 `--follow` 等一个新进程 |
| `probe_kdf.py` 只读 512 字节 | 签名前像被截断，`&&ieway.cn@20200611` 看不见 | 提到 4096 字节 |

---

## 6. 验证方法学（单独一节，因为它决定了前面所有结论的可信度）

### 6.1 不可信的检查（对本任务而言）

| 检查 | 为什么会骗人 |
| --- | --- |
| **TS 同步字节计数** | 只要 AES+XOR 层没错，同步率就是 100%，**哪怕视频切片全是乱的**。本项目因此误判「导出成功」数轮（§5.3） |
| `ffprobe` 能读出流参数 | 元数据来自 SPS，**SPS 在容器头里，是好的**；画面坏不坏它不知道 |
| 「端口在监听」 | MCP 服务、调试器端口都可能只是进程活着。必须做一次真实协议握手 |
| 「文件都在磁盘上」 | 不等于课程完整（§3.6 的 PTS 缺口才是判据） |
| 「捕获顺序」 | 不等于播放顺序（§5.2） |

### 6.2 可信的检查（附可直接复制的命令）

**(a) 全解码错误数——判断画面是否可用**

```powershell
$f = 'D:\ev-export\d5c3302f-2363-4534-83b0-de45bd1e4135.pts.ts'
$log = "$env:TEMP\decode.log"
& ffmpeg -v warning -i $f -f null - 2> $log
(Get-Content $log).Count          # 0 = 干净；成千上万 = 画面不可用
```

分离音频/视频（这是区分「密钥错」与「只有视频坏」的关键手段）：

```powershell
& ffmpeg -v warning -i $f -vn -f null - 2> "$env:TEMP\a.log"   # 音频：实测 8 行
& ffmpeg -v warning -i $f -an -f null - 2> "$env:TEMP\v.log"   # 视频：实测 1,874 行
```

**(b) 抽帧看画面——最直观的证据**

```powershell
# 注意：PowerShell 里用数组传参，避免逗号被转义吃掉
$out = 'D:\ev-export\frame10.png'
& ffmpeg @('-v','error','-ss','10','-i',$f,'-frames:v','1','-y',$out)
# 然后用 read_image 之类的工具看这张图；灰底+竖条 = 解码器错误遮蔽
```

多帧拼图（一次看 8 帧）：

```powershell
& ffmpeg -v error -i $f -vf "fps=0.2,scale=480:-1,tile=4x2" -frames:v 1 -y D:\ev-export\montage.png
```

**(c) 解密自检（只用来判断密钥/掩码，不用来判断画面）**

```powershell
& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' -u tools\parser-tools\merge_by_pts.py `
    --lesson d5c3302f --out D:\ev-export
# 输出里的 "decryption self-check: sync byte in 100.00%..100.00%" 才是这一步的意义
```

**(d) MCP 服务：必须做真实握手**

```powershell
# x64dbg：token 在 D:\DevelopmentTools\x64dbg\release\x64\mcp_config.json
$tok = (Get-Content 'D:\DevelopmentTools\x64dbg\release\x64\mcp_config.json' | ConvertFrom-Json).AuthToken
Set-Content -Path "$env:TEMP\init.json" -Encoding ascii -NoNewline -Value `
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
curl.exe -s -X POST 'http://127.0.0.1:9094/' -H "Authorization: Bearer $tok" `
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' `
  --data-binary "@$env:TEMP\init.json"
# 期望：serverInfo.name = "x64dbg-MCP Server"，随后 tools/list 返回 80 个工具
```

**(e) 字节级比对——判断「播放器的解密」与「我们的解密」是否一致**

这一步**已经做了**（2026-09-16），工具是 `tools/parser-tools/verify_packet_identity.py`，
结论与适用范围见 §13.1–§13.2：在 `119354` 上两侧**逐字节相同**，所以「有第二层」不能不加限定地说。
原计划见 §7.3。

**(f) 单元测试**

```powershell
cd D:\Codes\github\ev-reverse
cargo test --workspace     # 写作时刻：全部 ok，0 failed（各测试二进制合计 63 个通过）
```

---

## 7. 当前未解的问题与下一步

### 7.1 问题：视频切片还有第二层

事实链：

1. 单个分段独立解密后（同步 100%、长度正好 2330×188、PTS 正确），**单独解码也报 170–428 行错误**
   → 损坏在文件内部，不是合并问题。
2. 同一份密文里音频解码干净（8 行告警）→ **AES-256-ECB 层与掩码是对的**。
3. NAL 结构完整（AUD / SPS 26 B / PPS 6 B / IDR 74,224 B），`ffprobe` 报 H.264 High@4.0 / yuv420p / 1920×1080 / 30fps。
4. 结论：**盘上的明文里，H.264 切片负载是乱的**；播放器在送解码器前会再做一次还原。
   DLL 里对得上的线索是 `DecryptFilterMgr`、`DecryptFilter@ev`、`release DecryptFilter start!`。
   **这条结论现在必须限定到课**（§13.1）：在 `119354` 上做过逐字节比对，播放器交给解码器的字节
   **就是**我们解出来的字节，该课上不存在第二次还原。它是不是别的课上的现象，没有被测过。

嫌疑对象与其虚表（用 `crypto_classes.py` 可复现）：

```
DecryptFilter@ev   vtable 0x800D60  15 槽   +  vtable 0x800DE0  2 槽   （多继承：主接口 + 次接口）
FilterImpl@ev      vtable 0x800F18  15 槽
```

排查记录（避免重走）：`DecryptFilter` 的 `0x19C20` 已经被反编译过，它是**状态/回调转发**
（转调另一个对象 `vtable+0x38`，再回调 `param_1+8`），不是变换本身。
它**在下载期间被调用 16 次**（一次 150 秒的观察窗口），说明过滤器链在下载时就在跑。

### 7.2 播放器内部的解码入口已经定位

- DLL 内**静态链接 FFmpeg 4.2.9**（字符串 `Lavc58.54.100`、`FFmpeg version 4.2.9`，`avcodec` 出现 121 次）。
- `h264_decode_frame` = **RVA `0xB9648`**（`AVCodec` 结构体 `0xBBB2C0` 的 `+0xB0` 槽）。
- 参数按 Windows x64 ABI：`rcx=AVCodecContext*, rdx=void* data, r8=int* got_frame, r9=AVPacket*`。
- **`AVPacket` 偏移：`data` 在 `+0x18`，`size` 在 `+0x20`，`stream_index` 在 `+0x24`。**

实测（钩 `0xB9648`，75 秒窗口）：

```
[   1] packet     262 B  head=0000000109f000000001419b3349a841
[   2] packet     244 B  head=0000000109f000000001419f5145152c
...
[  50] packet   36109 B  head=0000000109f000000001419a4249a841
共收到 2250 个包；前 60 个已落盘到 D:\ev-export\decoder_packets\
```

形态解读：`00 00 00 01 09 f0` = AUD，`00 00 00 01 41` / `01` = 非 IDR 切片（nal_ref_idc=2/0，type=1）。
另外：钩 `CreateFileW` 确认播放器**确实在读下载目录里的 `.ts`**（45 秒内 4 个文件）。

### 7.3 下一步：三点同时抓，然后做字节比对

> **本节已执行（2026-09-16），结果见 §13。** 下面保留原计划，因为「先确认在播哪个文件再比对」
> 这条纪律仍然有效，而且第 3 步那两个分支的措辞需要改：一个字节命中只对该课下结论。

工具已写好并可用：**`tools/parser-tools/capture_playback.py`**。

```powershell
& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' -u tools\parser-tools\capture_playback.py `
    --pid <EVPlayer2 PID> --seconds 3600
# 或者等一个新进程：
& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' -u tools\parser-tools\capture_playback.py `
    --follow --seconds 3600
```

它同时钩四件事，输出写到 `tools/parser-tools/captured/playback.jsonl`，
前 80 个解码包落盘到 `tools/parser-tools/captured/playback/`：

| 钩子 | 抓什么 |
| --- | --- |
| `kernel32!CreateFileW` | 播放器正在读哪个 `.ts`（据此定位「哪节课在播」） |
| DLL `0x20EC0` | 它设置的 AES 密钥：`(schedule, const char* key, bits, direction)` |
| DLL `0x40AF0` | `hls_decode` 拿到的密钥字符串（`std::string` 引用，注意 size 在 +0x10） |
| DLL `0xB9648` | **它交给 H.264 解码器的原始字节** |

**比对步骤**（拿到数据后照做）：

1. 从 `playback.jsonl` 里找出 `OPEN` 的文件名，取它对应的 `KEY`（或 `HLS` 密钥）。
2. 用那把密钥 + §3.6 的 `decrypt()` 解这个文件（掩码 = `MD5(文件名)` 十六进制串前 16 字符，末尾去 `#` 填充）。
3. 从抓到的解码包里取一个较大的（IDR 通常是几 KB 到几十 KB），和明文里的对应 NAL 做**逐字节**比较：
   - **不同** → 差值（按位 XOR）就是缺失的那一层。若差值有周期性/与帧序或段密钥相关，直接实现进
     `crates/evmedia-core`；然后在 `decode-ev` 里应用。
   - **相同** → 「播放器有第二层」的假设错了。此时回头检查：是不是我们比对错了对象
     （播放器解的是流媒体而非本地文件）、或者本地文件与流式内容不同（下载链自己做过再加工）。

   实测结果（2026-09-16）：对 `119354` 走的是**「相同」**这一支（§13.1–§13.2），
   并且播放器自己的解码器把同一批字节解成了真画面，我们的解码器解成灰（§13.3–§13.4）。
   所以「假设错了」成立的范围**仅限被测的那节课**；其余课仍然是灰的，见 §13.7。
   另外注意：这一支并不意味着任务结束，它把问题从「找变换」改成了「找解码器差异」（§13.8）。
4. 已经做过的**同类比对曾经零命中**（20 个针 × 900 个分段），但那一次**无法确认播放器在播哪节课**，
   所以结论无效——这正是要加 `CreateFileW` 钩子的原因。**下次务必先确认「在播哪个文件」再比对。**

### 7.4 覆盖率的缺口（第二个未解问题）

即使第二层解决，也只有约 1/6 的文件能解（§4）。
补齐的方式有两条，其中第一条已经做过实测（见下），第二条验证有效：

1. **在线重取清单**：为盘上每节课重新取一次 `k_l`。需要三样东西——有效的 JWT、
   该课的 playkey（签名前像里的 `evs_playkey`）、以及**该课完整的分段文件名列表**。

   **实测（本次会话，`tools/parser-tools/tk_probe.py` / `tk_check.py`）**：

   | 事实 | 实测结果 |
   | --- | --- |
   | 一次请求能带多少名字 | **195 个一次成功**（10,646 字节签名域，未触上限）。所以「一次请求拿一整节课的 tk」在**名字已知**时成立 |
   | 响应是否会多给 | **不会**。5 个名字→5 条，195 个→195 条，从未多给一条。播放器为 `d5c3302f` 取 309 个 tk 时发了约 62 次请求（每次 5 个名字）——它自己也没有「一次拿全」的接口 |
   | `tk` 随什么变 | **`tk = f(playkey, 文件名)`**。同一 playkey + 同一文件名，隔 3 秒、隔几分钟、隔几小时都**完全一致**（同课内 5/5 吻合播放器当时拿到的值） |
   | 客户端传的 `idx` | **不是输入**（只被回显）。把 `idx` 从 0..4 改成 100..104，`tk` 不变 |
   | 名字是否校验归属 | **不校验**。用 `91773801` 的 playkey 请求 `63aac7ca` 的 190 个名字，服务器照样签了 195 条 |
   | 错 playkey 的后果 | 拿到的 tk **看起来完全正常，但解不开你的文件**：实测同步率掉到 **0.13%–0.29%**，而同一批文件用播放器的正确 tk 是 **100%**。好消息是错误**响亮**——`derive` 会以 `not aligned MPEG-TS` 拒绝，不会悄悄给你一个坏文件 |

   结论：**这条路可行，门槛只剩「名字」和「playkey」两样**。仍未验证的是 playkey 的**寿命**——
   跨登录、跨天是否还认（本次只验证了数小时内、跨进程重启有效）。

   **第三样（名字列表）来源仍未确认**：播放器是先知道文件名再发
   `ts_liststr=0|0|<名字>,1|0|<名字>,…` 的，这些名字来自哪个响应，本项目没有查明。
   `Admin.conf` 里的 `cache-1113723-<lessonId> : |900902|901748|…|` 看起来是**播放位置（毫秒）**，
   不是分段 id，也不是文件名。
2. **带探针下载**：挂上 `capture_keys.py --requests` 让播放器重新下载目标课程，
   下载过程中播放器会为每个窗口请求密钥，探针即可收齐该课全部分段的 `tk`。
   实测有效——`d5c3302f` 的 309 段就是这么收齐的。代价是需要使用者配合下载。


---

## 8. 执行过但失败的自动化尝试（别再重复）

| 尝试 | 结果 | 说明 |
| --- | --- | --- |
| 反复挂 Frida 探针等 `0x1B480`/`0x34390` 触发 | **从未触发** | 累计多轮、数小时真实使用（下载/播放/拖动/断网/EVS 链）。原因已在 §2.2 定性：格式不匹配。探针本身没问题（`--sites` 的 8 个流程钩子也全程未触发） |
| `probe_aes.py` 钩 AES 命中点 `0x76B603` | 消息洪水，调用超时 180 秒 | 该地址是 S-box 读取点（§5.4）。**文件已删除，不要重建** |
| 钩 `0x266690` 当解码入口 | 参数形态不对（`args[3]` 读出 `-1`） | 那是 `update_thread_context`（§5.5） |
| 静态解密 `m4OEgjp…` 字符串 | 全部失败 | 由 Bridge 运行时解密（§5.6） |
| 按捕获顺序批量导出 | 1.05 GB 产物画面全灰 | 顺序错（§5.2） |
| 请求使用者点「下载管理/字幕/反馈/断网播放」 | 均无效 | 那些接口根本不是这两个调用点的归属（§5.7）。**不要再用界面操作去逼这两个点** |
| 让播放器跑在容器/沙箱里的想法 | **未实施** | 它**不能替代进程内钩子**：播放器无论跑在哪，解密都在它自己的内存里完成，要拿到「它交给解码器的字节」仍然必须挂 Frida/x64dbg。容器的价值只在安全与可重复（不弄坏使用者的实例、可自动化），属于第二层定位之后的工程化，不是现在的关键路径 |
| 用 `handle64` 查进程打开的句柄 | 工具不存在（`FileNotFoundError`） | 改用 Frida 钩 `CreateFileW`（有效） |

---

## 9. 工具清单

### 9.1 `tools/parser-tools/` 脚本（一句话职责 + 状态）

**当前有效、优先使用：**

| 脚本 | 职责 | 状态 |
| --- | --- | --- |
| `capture_playback.py` | 同时抓 `CreateFileW` / `0x20EC0` 密钥 / `0x40AF0` hls 密钥 / `0xB9648` 解码输入，供字节比对 | **可用（当前关键路径）** |
| `capture_decoder.py` | 只抓解码器输入（`0xB9648`），并记录 `DecryptFilter` 方法调用 | 可用 |
| `merge_by_pts.py` | 按每段自身 PTS 排序合并 + 报告时间轴缺口 + 解密自检 | 可用 |
| `export_cached.py` | 用手里的全部清单逐课导出（按捕获顺序，见 §4 的覆盖率与警告） | 可用但产物画面不可用 |
| `capture_keys.py` | 记录播放器设置的每个 AES 密钥 + 调用点 + 解密出的 payload；`--sites` 钩流程、`--requests` 记录请求路径 | 可用（工作马） |
| `crypto_classes.py` | 枚举解密器家族、虚表槽位，标记两个静默调用点 | 可用 |
| `flow_map.py` | 打印一个函数调用了谁、引用了哪些字符串（自动识别 UTF-16） | 可用 |
| `dump_endpoints.py` | 从**运行中的进程**读接口表（静态扫不到，因为接口名是加密字符串） | 可用 |
| `static_xref.py` | 查包含某 RVA 的函数、以及所有直接调用者 | 可用 |
| `probe_kdf.py` | 在 `0x1FD60` 抓密钥推导的三个输入 | 可用 |
| `replay_bodies.py` | 用新 token 重放抓到的请求体，并记录重放能/不能解开什么 | 可用 |
| `cached_lessons.py` | 报告哪些捕获清单能在本地磁盘上满足 | 可用 |
| `decrypt_response.py` | 离线解捕获的 API 响应体 | 可用 |
| `api_summary.py` / `extract_bodies.py` | 汇总捕获流量 / 从 `bodies.bin` 拆分请求体 | 可用（历史捕获用） |
| `annotate.py` | 反汇编一个 RVA 区间，解析操作数、导入、字符串 | 可用 |
| `vtable.py` | 把一个 C++ 虚调用解析到落点方法 | 可用 |
| `ghidra_scripts/DecompileAt.java` | 用 Ghidra 无头模式反编译指定地址的函数（**地址必须带 `0x180000000`**） | 可用 |

**2026-09-16 新增的 8 个工具见 §13.10**：`inspect_ts.py`、`verify_packet_identity.py`、
`dump_player_frames.py`、`compare_with_player.py`、`detect_inplace_transform.py`、
`decode_report.py`、`count_decode.py`、`probe_avframe.py`。

**历史/已被取代（保留但别当主路径）：**

`capture_all.py`（v6 全量捕获）、`capture_api.py`、`capture_plaintext.py`、`hook_aes_key.py`、
`hook_caller.py`、`watch_key.py`、`probe_schedule_key.py`、`probe_write.py`、`probe_at.py`、
`probe_bridge_value.py`、`probe_urls.py`、`wait_ready.py`、`live_contexts.py`、`find_in_mem.py`、
`find_aes_key.py`、`find_sign.py`、`sign_variants.py`、`key_formula.py`、`search_third.py`、
`pair_capture.py`、`find_descriptors.py`、`field_refs.py`、`registrations.py`、`bridge_values.py`、
`fish_api_plain.py`、`sniff_plain.py`、`sniff_headers.py`、`fetch_list.py`、`endpoints_by_caller.py`。

关于 `endpoints_by_caller.py`：它的假设（「接口名字面量在 DLL 里，按字符串引用就能定位处理函数」）
**已被证伪** —— 静态扫 `/student/…` 得到 **0 个**。别再用它下结论，用 `dump_endpoints.py` 或 `capture_keys.py --requests`。

非 Python 的工具文件：`press_keys.ps1`（PostMessage 发按键；参数叫 `-ProcessId`，因为 `$Pid` 是只读的）、
`triple.json`、`ctx_dump.json`、`dump_contexts.json`、两个 `EVPlayer2_*.zip`。

### 9.2 捕获数据现状（`tools/parser-tools/captured/`）

```
api/            397 个 payload（12 个批次，2026-09-15 的探针会话；最新一批 218 个）
inflated/       103 个（播放器自己解压出来的响应，归档会话）
plaintext/       83 个
bodies/         317 个请求体
bodies.bin      722,233 B        events.jsonl   708,541 B（317 次 API 调用）
urls.log        336,925 B        keys_by_caller.jsonl 7,225,097 B（2026-09-13）
kdf_round3.jsonl / kdf_inputs_round2.jsonl / kdf_inputs.jsonl   ← 签名前像（fetch --from-capture 用）
tokens.jsonl    1,247 B（两条 JWT 记录；带 exp，用前先检查是否过期）
keys_by_caller.jsonl 的调用点分布：PlayerLibRender56_vs.dll+0x76b603 ×31775、+0x76c203 ×664
  ↑ 这两个是 S-box 相关读取点，不是密钥编排（§5.4）
```

### 9.3 外部工具（安装位置与验证结果）

| 工具 | 位置 | 验证结果 |
| --- | --- | --- |
| x64dbg（snapshot 2026-05-27，commit `9c8ca1cae0b6d56cc44f31fddcb10e3b02ffbb87`） | `D:\DevelopmentTools\x64dbg` | 可运行 |
| x64dbg-MCP v1.3 | `D:\DevelopmentTools\x64dbg\release\x64\plugins\x64dbg-MCP-Server.dp64`（x32 同名 `.dp32`） | **真握手通过**：`initialize` → `x64dbg-MCP Server 1.3`，`tools/list` → **80 个工具** |
| x64dbg MCP 配置/令牌 | `D:\DevelopmentTools\x64dbg\release\x64\mcp_config.json`（IP/Port/AuthToken/AutoStart，端口 9094；x32 是 9095） | 首次运行自动生成 |
| Ghidra 12.1.2 | `D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC` | 扩展已部署；`/check_connection` 回 `Connected: GhidraMCP plugin running, but no program loaded` |
| ghidra-mcp 7.0.0（bridge 1.28.1） | `D:\DevelopmentTools\mcp\ghidra-mcp`；stdio 桥 `…\.venv\Scripts\bridge-mcp-ghidra.exe` | **真握手通过**：`initialize` → `ghidra-mcp 1.28.1`；自动连 `127.0.0.1:8089`，注册 **238 个工具** |
| Apache Maven 3.9.11 | `D:\DevelopmentTools\maven\apache-maven-3.9.11` | `mvn -v` 通过（需 `JAVA_HOME`） |
| JDK 21.0.12.1 LTS | `D:\DevelopmentTools\jdks\jdk-21.0.12.1` | 机器原有 |
| uv 0.12.15 | `C:\Users\Yonjay\.conda\envs\subgen\Scripts\uv.exe` | 为 ghidra-mcp 安装 |
| agent-browser 0.19.0 | `C:\Users\Yonjay\.cargo\bin\agent-browser.exe` | `--version` 通过（open/click/type/snapshot/screenshot/eval/CDP）；**与当前第二层问题无关**（那是浏览器自动化，播放器是原生 Qt 程序） |
| Ghidra 分析工程 | `%USERPROFILE%\ghidra_projects\evplayer`（已导入并完成 304 秒自动分析） | 见 §9.4 |

### 9.4 Ghidra 无头反编译（可直接复制）

```powershell
$env:JAVA_HOME = 'D:\DevelopmentTools\jdks\jdk-21.0.12.1'
$ghidra  = 'D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC'
$project = "$env:USERPROFILE\ghidra_projects"

# 首次导入（已完成，勿重复；重复就加 -overwrite）
& "$ghidra\support\analyzeHeadless.bat" $project evplayer `
    -import D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll -overwrite -analysisTimeoutPerFile 2400

# 之后每次：-noanalysis，地址带 0x180000000 基址
& "$ghidra\support\analyzeHeadless.bat" $project evplayer -process PlayerLibRender56_vs.dll -noanalysis `
    -scriptPath D:\Codes\github\ev-reverse\tools\parser-tools\ghidra_scripts `
    -postScript DecompileAt.java 0x180266690 0x180034390 0x18001b480
```

排查提示：`### no function contains 0x…` 十有八九是**地址没带基址**（写成 `0x1800266690` 这种多一位的也会）。

---

## 10. 关键地址速查表（RVA，基址 `0x180000000`）

| RVA | 是什么 | 备注 |
| --- | --- | --- |
| `0x1EA10` | 响应解密 `(out, input, key_str, ok)` | 它报出的返回地址 = 调用点 |
| `0x20EC0` | AES 密钥设置 `(schedule, const char* key, bits, direction)` | 判断「有没有在解码」看这里 |
| `0x1FD60` | `std::string = MD5_hex(input)` 包装 | 签名 + 密钥推导的观测点 |
| `0x40AF0` | `hls_decode` 的 AES 初始化 `(ctx, std::string key)`；错误串在 `0x803330` | 播放期密钥观测点 |
| `0x0283F2`（属函数 `0x26730`） | 解密 **分段清单**（gzip JSON `d_p`/`k_l`），密钥 `x!@#y.cn_xnk0506` | 三个在用调用点之一 |
| `0x03B435`（属 `0x3B2B0`） | 解密 **descriptor**，密钥 `11585ec1b1f8f30e` | 之一 |
| `0x042C49`（属 `0x42A30`） | 解密 **descriptor**，密钥 `11585ec1b1f8f30e` | 之一 |
| `0x1B480` | **`Decryptor_V8A` 槽 3**（表 `0x801420`） | 静默点 1 |
| `0x34390` | EV4/V4A 取密钥响应的解密步骤 | 静默点 2 |
| `0x48ED0` / `0x49220` / `0x151C0` | `MediaPlayer::playFile` / `MediaPlayer::resume` / `EVPlayer::play` | 导出名 |
| `0x385B0` → `0x174C0` → `0x1B170` | 构造 `Decryptor_V8A` 的链 | — |
| `0x31060` / `0x36120` | `GetEv4Key` 槽 0 / `GetV4AKey` 槽 0 | 与 `0x34390` 同片 |

解密器家族虚表：`Decryptor` `0x8010A0`、`Decryptor_EV5` `0x801108`、`Decryptor_EV6` `0x801190`、
`Decryptor_EV7` `0x801200`、**`Decryptor_V8A` `0x801420`**、`Decryptor_EV4` `0x8021B8`、
`Decryptor_V4A` `0x802310`、`Decryptor_V3A` `0x802D20`、`Decryptor_EVS` `0x802DD0`、
`GetEv4Key` `0x8026B8`、`GetV4AKey` `0x802CA8`。
每个 12 槽表的后 6 槽是基类共用尾部：`0x9AC0`、`0x1AF60`、`0x1A590`、`0x1A5C0`、`0x1A5A0`、`0x7920`。

FFmpeg 相关（DLL 内静态链接 FFmpeg 4.2.9）：

| RVA / 地址 | 是什么 |
| --- | --- |
| `0xBBB2C0` | h264 的 `AVCodec` 结构体（`name` → `0x817F44` "h264"，`long_name` → `0x817F50`） |
| **`0xB9648`** | **`h264_decode_frame`**（结构体 `+0xB0` 槽，755 字节） |
| `0x266690` | `ff_h264_update_thread_context`（**不是**解码入口；`+0x80` 槽，2500 字节） |
| `0x265624` | 报错路径，引用 `"error while decoding MB"`（串在 `0x903920` / `0x84E418`） |
| `0x265EE0` | `decode_slice` |
| `0xB9AC4` | 分派函数（被 `0xB9648` 与 `0x26624C` 调用） |
| `0x904508` / `0x9045A0` | 字符串 `"top block unavailable"` |
| `0xA440B0` | 字符串 `"cabac decode of qscale"` |
| `0x792C78` | 读 AES **逆 S 盒**（注意：`0x76B603` 是调用它的轮内代码，不是密钥编排） |

Bridge（运行时字符串/常量来源）：

```
bridge = *(0xbf26c8 + 0x78)
bridge->vtable[0xd0](bridge, out, wchar_ptr, len)   // 解密一个 m4OEgjp… 字符串
bridge->vtable[0x40](bridge, out, value, 0xa6)      // 按数字 id 取一个值（20220507 / ieway.cn@20200611 由此而来）
```

---

## 11. 提交记录（`git log --oneline -40`，按时间倒序，每条一句话）

| commit | 改了什么 |
| --- | --- |
| `57ea09a` | **更正导出结论**：画面是灰的，同步字节从来不是成功判据（§5.3） |
| `a02f664` | 恢复被上一次编辑吞掉的小节标题 |
| `76d213a` | 记录 Bridge 解密字符串的机制 + Ghidra 无头反编译用法 |
| `2f0a406` | 用导出名追出两个静默调用点的可达链 |
| `17eb53c` | 记录 desktop profile 的 patch 层**只在启动时**读取 |
| `ae1b533` | 记录逆向工具链的安装位置与验证结果（`docs/RE-TOOLS.md`） |
| `49f818e` | 说明接口表只是快照，补记请求侧新发现的 `/student/checkIn` |
| `246fac8` | **定性两个静默调用点**：`Decryptor_V8A` 槽 3、EV4/V4A 取密钥响应 |
| `f9a73e5` | 新增 `flow_map.py`（函数邻域映射） |
| `bb3e37c` | 写清当时对两个静默调用点已知与未知的边界（后被 `246fac8` 取代部分内容） |
| `355b33d` | 探针把每个解密与当时在飞的请求配对 |
| `fa03c9c` | 探针把每个 payload 归属到课程 |
| `81d65a4` | 按 `std::string` 正确读解密密钥（修 `key=None`） |
| `91b8d57` | 记录 DLL 自己的请求字段表与「一个请求两种形态」（`ts_liststr` vs `wdisklist`） |
| `7346e57` | 从运行中的进程读接口表（新增 `dump_endpoints.py`） |
| `9deb767` | `download` 直接吃 `fetch` 产出的清单，并按清单文件名落盘 |
| `727490f` | 从探针捕获直接取 playkey，并监视每一次 AES 密钥设置 |
| `606f003` | 重放捕获请求体的工具，并记录重放不能解开什么 |
| `0528092` | 工具支持 `--follow` 等新进程（复用进程会拒绝注入） |
| `4b5427c` | 单会话同时记录 token 与明文，以及签名的长尾 |
| `c4ed389` | **自己签名请求**：`evmedia fetch` 能自己取清单 |
| `53df596` | 手工构造请求，说明签名拟合与不拟合的边界 |
| `a1b648f` | 不依赖播放器，让 API 为一节课的分段签名 |
| `0192da3` | 分离捕获批次，记录 descriptor 来自网络 |
| `759ba9f` | 读取播放器解密的每个 API 响应，并命名它用的密钥 |
| `ad392a2` | 探测「API 响应用什么密钥解密」及其结论 |
| `46b74b7` | 活捉密钥推导，并修掉两个会骗人的观测工具 |
| `a135f43` | 文档：`key = MD5(tk + filename + "20220507")` |
| `c217ab3` | 从分段清单推导密钥，不需要播放器 |
| `1ac21ad` | 记录接口数组在哪里装配 |
| `829faee` | 汇总捕获的 API，说明捕获本身读不出什么 |
| `c3c5e55` | 文档：推导里的 `extra` 是障碍 |
| `ed39766` | 九个静态工具，用于不启进程读 DLL |
| `0ad9bb2` | 更新上下文 |
| `16b7829` | 提交 claude 会话记录 |
| `ff802bd` | 把 storm harness 纳入仓库并修正它的实测结论 |
| `b548860` | 文档：run 2 确认扫描能发现并瞄准，并指出什么让它停下 |
| `b4dcf2e` | 修写入监视（页数不对导致监视不到） |
| `44f7fd7` | `.gitignore` 用 `verify_out*/` 而不是 `verify_out/` |
| `758fce9` | 文档：串起 `.pdata` 调用链，说明 `0x20EC0` 到底收到什么 |

历史里**没有**「第二层被解开」的提交——这是当前最重要的空白。

---

## 12. 不要重犯的坑（操作层面）

1. **别用 TS 同步字节当成功标准。** 它只证明解密自检，画面要用 `ffmpeg -v error … -f null -` + 抽帧（§6）。
2. **别把捕获顺序当播放顺序。** 播放器并发下载；用 PTS（§3.6）。
3. **别在没有 ground truth 的情况下宣称「验证通过」。** 先问：这个判据能不能区分「对」和「看起来对」？
   本项目最大的损失就来自这一条。
4. **PowerShell 里 JSON 参数必须用文件传**：`curl.exe … --data-binary "@$env:TEMP\body.json"`。
   内联 JSON 会被 PowerShell 拆坏，服务器回 `Parse error`（-32700），看起来像服务端问题。
   `ffmpeg` 的滤镜参数同理——用数组传参 `& ffmpeg @('-v','error',…)`，避免逗号被吃掉。
5. **Ghidra 反编译地址必须带 `0x180000000` 基址**，否则报 `no function contains`（像函数不存在，其实是地址错）。
6. **写文件不要用 PowerShell 的 `Set-Content`**：它会加 BOM 或按 GBK 重新编码，本仓库的文件是 UTF-8 无 BOM。
   用 `write` 工具，或 `[System.IO.File]::WriteAllText($p, $s, [System.Text.UTF8Encoding]::new($false))`。
7. **Frida 不要反复 attach 同一进程**：会 `VirtualAllocEx returned 0x00000005`。用 `--follow` 等新进程，或重启播放器。
8. **MSVC `std::string` 的 size 在 `+0x10`、capacity 在 `+0x18`**（前 16 字节是 union/buffer）。按 C 字符串读会得到
   缓冲区指针的字节，表现为「密钥读不出来」而不是报错。
9. **`nghttp2_nv` 是 40 字节**（x64）。用 16 字节步长读会「钩子挂上但什么都不报」。
10. **`git commit -m` 多行在 PowerShell 里会被拆成 pathspec**：用 `git commit -F <msgfile>`。
11. **钩子要自证**：挂了钩子却没有输出时，先确认「这个地址真的是那个函数」——
    两条独立证据（结构体 RTTI + 调用链）胜过一条偏移推断（§5.5）。
12. **`0xBF26C8 + 0x78` 的 Bridge 是运行时常量与字符串的唯一来源**；任何「文件里搜不到」的常量都别再静态找。
13. 仓库根有一个**未跟踪**的 `ghidra-master/`（Ghidra 源码树，20,546 个文件，2026-09-14 的产物）。
    它与本项目无关，**不要 `git add`**；`.gitignore` 也没有覆盖它。

---

## 13. 2026-09-16 会话：字节对得上，解码器对不上

本节记录一次会话的实测，不重述前面已有的内容。它应当紧接 §7 阅读——本节执行的就是 §7 的计划——
它给前面正文里三处**没有加限定**的说法补上了适用范围：§7.1 的「播放器在送解码器前会再做一次还原」、
§7.3 里把「字节命中」读成「这个问题在所有课上都有定论」的那一支，以及 §2.3 / §4 的「画面是灰的」。
下面每条结论都写明是哪个工具产出的，这些工具列在 §13.10。

### 13.1 哪些是已证实的，哪些仍是假设

**PROVEN（已证实）——实测得到，用所列工具可复现。**

| 结论 | 实测 | 工具 |
| --- | --- | --- |
| 对课程 `119354`，播放器交给它 H.264 解码器的字节就是我们解密出来的字节 | 9 条流中每条里所有 ≥ 256 B 的 NAL 都逐字出现在捕获包中；3,290 个可测包里有 1,270 个逐字节出现在我们的流里 | `verify_packet_identity.py` |
| 播放器自己的解码器能把它们解出真画面 | 1920×1088 coded、stride 1920、format 0（yuv420p）、亮度 mean 221.5 sd 33.3 | `dump_player_frames.py` |
| 我们用同一文件、同一把播放器的密钥解码，解不出来 | 73–86 行错误，亮度 mean 130.0、sd 0.00–0.76，采样像素中 0.3% 与播放器的相同 | `compare_with_player.py` |
| 包缓冲在解码前没有被原地改写 | 499 次 entry/leave 比较，`changed=0` | `detect_inplace_transform.py` |
| 与线程、错误检测设置无关 | 三种 ffmpeg 设置，同样是灰的 | `decode_report.py` |
| 密钥推导不是问题所在 | 13 把推导密钥与播放器在 `hls_decode` 设置的密钥逐字节相同 | `check_key_derivation.py`，§3.2 |

**HYPOTHESIS（假设）——只是提出，尚未验证。**

1. 「能解码的课」与「一直是灰的课」之间的分界，是**逐课**由课程描述符里的一个保护标记决定的：
   即 §3.4 列在 `dkey`、`base_key`、`ext` 和 `evc_val` 旁边的 `dkey_ver` 字段。它与 §13.7 实测到的
   分界吻合。**没有做任何事去验证它。**
2. 剩余的差异来自解码器本身——播放器静态链接的 Lavc58.54.100 在补丁级别或构建上的差异。
   §13.8 就是判定它的实验，而这个实验**尚未运行**。

**适用范围。** 逐字节相同的结论是对课程 `119354` 证实的，也就是抓包时正在播放的那节课。它不是
关于其他课的结论，其他课仍然是灰的（§13.7）。

### 13.2 逐字节相同：做精确比对，而不是靠 NAL 长度对齐

工具：`tools/parser-tools/verify_packet_identity.py`，作用对象是 `tools/parser-tools/captured/pairing/`。
该目录装着同一时刻的两侧：播放器交给 `h264_decode_frame` 的每个包的 payload（`*.bin`），
以及我们从每把密钥所指分段里抽出的基本流（`*.h264`）。早先有一轮比对是**按长度**对齐 NAL 的，
那是启发式——长度相同的 NAL 可以对上，字节却可能不同。这一轮改为做精确比对，而且双向都做。

运行时该目录里有 **6,000 个捕获包和 9 条解密流**（流共 3,236,019 字节；包共 9,289,487 字节，
即 6,000 个 blob 用 4 字节起始码拼接：9,265,491 + 5,999 × 4）。

**正向——每个捕获包是不是我们字节里的一段？**

```
forward  packets checked 3290  ->  identical 1270, needle-only 4, absent 2016
```

2,016 个「不存在」是预期内的，不构成任何反证：来自「密钥在抓包开始前就已设置」的分段的包，
在我们手里的流里没有对应物。4 个 `needle-only` 命中是跨边界的包，这也是为什么除整包之外还要测
一个 64 字节的 needle。

**反向——更强的一侧，因为它按 NAL 而不是按包给答案。** 9 条流中每条里所有至少 256 字节的 NAL，
都逐字在捕获包里找到了。对 `119354-d3010742-508b-420b-8ba5-d7b351f02226`：

```
256-1k: 41/41   1k-4k: 5/5   4k-16k: 3/3   16k-64k: 1/1   64k+: 5/5
```

包括它那个 189,694 字节的 IDR。

结论：对课程 `119354`，播放器交给解码器的字节**就是**我们解密的字节。该课上，两者之间已经没有
变换可找了。

```powershell
& 'C:\Users\Yonjay\.conda\envs\subgen\python.exe' -u tools\parser-tools\verify_packet_identity.py
```

> pairing 目录在那次运行之后又长大了：现在有 21 条流（6,970,624 字节）。用同一工具在更大的集合上
> 重跑，报的是 `identical 2817, needle-only 4, absent 469`，反向一侧仍然覆盖全部 21 条流里每个
> ≥ 256 B 的 NAL。结论不依赖集合的大小，但正向的计数依赖——比较时要拿同一口径的数来比。

### 13.3 播放器自己的解码器输出的是真画面

工具：`tools/parser-tools/dump_player_frames.py`。它钩 RVA `0xB9648`（`h264_decode_frame`，§5.5），
在**离开时**读 `AVFrame`，因为那个函数是在返回途中才把帧填好的。

布局是用 `tools/parser-tools/probe_avframe.py` 从活体结构体上读出来的，不是猜的：

| 字段 | 偏移 |
| --- | --- |
| `data[8]` | `+0x00` |
| `linesize[8]` | `+0x40` |
| `extended_data` | `+0x60` |
| `width` | `+0x68` |
| `height` | `+0x6C` |
| `format` | `+0x74` |

在**进入时**帧还是空的（width 0、height 0、format −1）。该工具早先的版本就是在进入时读它，
结果什么都没 dump 出来，而这份沉默被误读成「播放器没有在解码」——与 §5.5 同一种失败形态：
钩子从不触发，看起来就像那个行为不存在。

抓到的帧是 1920×1088 coded、stride 1920、format 0（yuv420p）。
`tools/parser-tools/captured/decode_frames/` 里的 PNG 是一段真实的屏幕录制：一个标题为
`授权码模式 + PKCE` 的 Typora 窗口，上面横着 EVPlayer2 水印。dump 出的 Y 平面亮度 mean 221.5、
标准差 33.3——这正是 §13.4 里 `mean 220 sd 30+` 的特征。

### 13.4 我们用同一把密钥解同一文件，出来是平灰

工具：`tools/parser-tools/compare_with_player.py`。它把每个 dump 出的帧归属到「该帧**之前**播放器
最近一次设置的密钥」所指的分段，然后从我们自己对该文件的解码里取出同一个帧序号，解密用的是同一
把密钥（`build_videos.decrypt`）。

对 `119354-d2f057bd-008a-4200-97a1-bb343021bbcc.ts`：

```
73-86 decoder error lines, luma mean 130.0, standard deviation 0.00-0.76, 0.3% of sampled pixels equal to the player's frame
```

也就是说，同一批字节在播放器那里出画面，在我们这里是平灰。

它与线程、错误检测设置无关。工具：`tools/parser-tools/decode_report.py`，ffmpeg 8.1，
作用在那份文件上：

| 设置 | 错误行数 | 采样帧的亮度 |
| --- | --- | --- |
| 默认 | 86 | mean 130.0 sd 0.00 |
| `-threads 1` | 87 | mean 130.0 sd 0.39 |
| `-err_detect ignore_err+careful` | 85 | mean 130.0 sd 0.76 |

`decode_report.py` 存在的意义就是把这个判断变成机械的、而不是靠感觉：`mean 220 sd 30+` 是真实的
屏幕录制，`mean 128 sd 0` 是解码失败。

有一条过时的前提值得点名：`check_key_derivation.py` 的 docstring 说播放器设置的密钥
「produces the same kind of file with zero decode errors」——即「产出同一类文件、解码零错误」。
在这份文件上并不是——用播放器自己的密钥，我们解出来照样是灰的，而这正是 §13.1 的要点。

### 13.5 就算存在变换，它也不是原地改写包

工具：`tools/parser-tools/detect_inplace_transform.py`。它在进入 `0xB9648` 时给每个包的字节拍快照，
离开时再读一遍，逐字节比较，并报告哪些字节不同、异或值是多少。该轮 499 次比较全部 `changed=0`，
尺寸从 76 B 到 165,442 B。

这一结果留下了两种解释：工作在解码器内部的一份拷贝上做，或者根本不存在逐包变换。它排除掉的只是
该假设的一种具体形态——解码器在拿到的缓冲上原地还原一层——而不是这个假设本身。

### 13.6 一个灰文件在结构上长什么样

工具：`tools/parser-tools/inspect_ts.py`，作用在 `D:\ev-export\d3010742.ts`（861,040 字节）：

```
packets: 4,580  unusable/short: 0
PID 0x0100  pes=0xe0  payload=645,870  nal=502
    NAL types: non-IDR slice=249, IDR slice=1, SPS=1, PPS=1, AUD=250
    largest NALs: [(5, 189694), (1, 91120), (1, 82277), (1, 79669), (1, 66474)]
PID 0x0101  pes=0xc0  payload=163,438  nal=79
```

`ffprobe` 能干净地解析它：High profile、level 40、1920×1080、25 fps、yuv420p、start 551.48 s。
这正是 §5.3 的要点，也是为什么这个检查不能当证据。`ffmpeg` 在唯一那个 189,694 字节的 IDR 里失败：

```
top block unavailable for requested intra mode -1
error while decoding MB 1 0, bytestream 189594
```

之后每一帧都停留在灰色。容器本身没有任何问题；画面是在一个切片上丢掉的，然后再没恢复。
还要注意：这个分段里**只有**一个 IDR：它内部没有更晚的关键帧可供重新同步。

### 13.7 边界目前划在哪里

用原版 ffmpeg，三节课能解出真画面，其余仍是灰的：

| 能解出画面 | 仍是灰的 |
| --- | --- |
| `3428f5c3`、`4175a698`、`d99cf676` | `d5c3302f`、`4589d038`、`7410427e`、`63aac7ca`、`ab0833eb`、`91773801`、`cca0f596`、`73fc0389`、`e3508944`、`f76a7e5e`、`36340b6a`，以及课程 `119354` 的那些分段 |

所以「导出是灰的」不是这条流水线的性质，而是文件的性质。这是对 §2.3、§4 以及本文档开头那句
总结的细化——那三处说「画面是灰的」时都没有这个限定。

密钥推导没有问题。由 `MD5_hex(tk + filename + "20220507")` 推出的 13 把密钥，与播放器在
`hls_decode` 设置的密钥逐字节相同——这是 `tools/parser-tools/check_key_derivation.py` 测出来的，
只要一份文件两侧都已知（`captured/player_keys.jsonl` × 捕获的分段清单），它就把两把密钥并排报出来。
推导产出的文件，其字节被播放器的解码器逐字接受（§13.2），所以密钥错不可能是画面发灰的原因。

剩下的可能性——而且它只是假设——是一个逐课标记：描述符里的 `dkey_ver`，与 `dkey`、`base_key`、
`ext`、`evc_val` 并列，是目前找到的唯一一个可能把上面两个列表分开的逐课开关。

### 13.8 下一个实验——尚未运行

播放器静态链接的是 `Lavc58.54.100`，也就是 FFmpeg 4.2.x（DLL 里还带着字符串 `FFmpeg version 4.2.9`；
§7.2 与 §10 记录了这次读取）。我们用的是 ffmpeg 8.1。

这个版本差现在可以测了，而且播放器用的那个解码器就在磁盘上：

```
D:\DevelopmentTools\_downloads\ffmpeg-old\x\imageio_ffmpeg\binaries\ffmpeg-win64-v4.2.2.exe
```

它是用 `pip download imageio-ffmpeg==0.4.4 --no-deps` 取来的，那个 wheel 里就打包了这个二进制。

用它解码那个灰文件：

* **出画面** → 差异在解码器版本，也就是补丁级别或构建的差异，不是数据变换。导出流水线随后需要
  那个解码器（或等价的修法），而寻找第二层的工作到此停止。
* **仍是平灰** → 差异是播放器在自己解码器内部施加的一个变换，那个变换仍然必须找出来。

**这个实验尚未运行。** 它是紧接着的下一步。

> 这个实验后来跑了：八种解码器实现给出同样的平灰，解码器版本假设被推翻——见 §14.7。

### 13.9 操作层面的事实，以及验证纪律

* EVPlayer2 跑成一个小启动器进程加一个重量级渲染进程，**解码的是渲染进程**。按工作集挑它，
  而不是按 `tasklist` 先列出哪个 PID——`tools/parser-tools/count_decode.py` 做的就是这件事，
  它一边统计进入 `0xB9648` 的调用数，一边打印每个进程的模块基址。
* **每次启动 PID 都会变**，所以早先记下的 PID 毫无价值：对一个过期的 PID 调 `frida.attach` 会抛
  `ProcessNotFoundError`。（这是从另一面看 §12.7 的那条规则：问题不只是反复 attach，还包括
  attach 到一个已经不存在了的进程。）
* x64dbg 当时附加的那个进程（PID 18024）**随调试会话一起退出了**。早先「EVPlayer2 的两个 PID
  都在解码」这个假设是错的，`count_decode.py` 就是因此才有的：一个进程上可以设着密钥却从不产出
  帧，而挂在这种进程上的钩子只会给出沉默，读起来和「播放器没有在解码」一模一样（§13.3）。
* 这条验证纪律总要被重新学一遍：**同步字节、PNG 文件大小、端口在监听都不是证据。** 全解码错误数
  加上亲眼看抽出的帧才是——PNG 的字节数说明不了它里面有没有画面，而 §13.6 那份文件有 4,580 个
  完好的包和 100% 的同步率。

### 13.10 本次会话新增的工具

| 工具 | 它回答的问题 | 需要 |
| --- | --- | --- |
| `inspect_ts.py` | 哪些 PID 带数据、其中哪些带 H.264、出现了哪些 NAL 类型——也就是一个灰文件究竟装了什么 | 一个 `.ts` 文件 |
| `verify_packet_identity.py` | 播放器的解码器输入是否与我们解密的结果逐字节相同？精确比对，双向，不做长度对齐 | `captured/pairing/` |
| `dump_player_frames.py` | 播放器自己的解码器输出什么？写出每第 N 帧的 Y 平面，另存 PNG | 活着的播放器 |
| `compare_with_player.py` | 播放器的帧和我们的是否来自同一分段、同一帧序号，二者是否吻合？ | 一份帧 dump + 播放器的密钥 |
| `detect_inplace_transform.py` | 解码器是否在解码前原地改写包？ | 活着的播放器 |
| `decode_report.py` | 这次解码出画面了吗？错误行数加每帧亮度 mean/sd，让判断变成一个数 | 一个文件，以及 ffmpeg |
| `count_decode.py` | 这个进程到底有没有在解码，哪个 PID 是渲染进程？打印模块基址与调用计数 | 活着的播放器 |
| `probe_avframe.py` | 这个静态链接的 FFmpeg 里，`AVFrame` / `AVCodecContext` 的真实偏移是多少？dump 结构体的原始字 | 活着的播放器 |

八个都在 `tools/parser-tools/`。四个只靠文件就能跑（`inspect_ts.py`、`verify_packet_identity.py`、
`compare_with_player.py`、`decode_report.py`）；另外四个需要活着的播放器，这也是 §13.9 那些
进程规则重要的原因。

---

## 14. 2026-09-16 会话（续）：灰画面不是整节课的属性，而是某一类分段

本节是 §13 的续写，只记录 §13 之后新做的实测，不重述正文已有的内容。§13.8 那个「尚未运行」的实验
在本节跑完了（§14.7），它给出的答案不属于当初列出的两种：**八种解码器实现给出完全一样的平灰**，
所以「差异在解码器版本」被推翻；而灰的分界也不再落在「哪些课」上，而是落在**同一节课内部的两类分段**上。
下面每条都写明是哪个工具产出的，工具列在 §14.14。

### 14.1 哪些是已证实的，哪些仍是假设

**PROVEN（已证实）——实测得到，用所列工具可复现。**

| 结论 | 实测 | 工具 |
| --- | --- | --- |
| 「灰」是分段的属性，不是课的性质：课程 `119354` 里两类分段并存 | 120 个分段中，24 个 level 40（PTS 521 s–751 s）里 23 个解成平灰、1 个 sd 5.65；96 个 level 51（PTS 1993 s–2943 s）全部解出真画面 | `classify_segments.py` |
| 播放器此刻播放的分段，其 SPS/PPS 到达解码器时与文件里的逐字节相同 | 两个 level 42 分段（`119354-faeb8a21…`、`119354-e7880f0d…`）的 SPS/PPS 逐字节相同；我们解同一文件第 30 帧得到暗色 PyCharm 窗口，mean 38.8 sd 23.88 | `identify_playing.py` |
| 参数集确实随包带内到达 | 包形状只有两种：`{AUD:1, non-IDR:1}` ×788、`{AUD:1, SPS:1, PPS:1, IDR:1}` ×3 | `nal_inventory.py` |
| 每个包只解一次，不存在「用另一份字节再解一遍」 | 900 次解码调用带 900 个互不相同的 PTS | `decode_calls.py` |
| 解码器从包里拷出去的字节是普通重定位，不是变换 | 钩 `memcpy` thunk（RVA `0x7F6914`）后，8,339 B、14,299 B、10,960 B 三笔拷贝与来源窗口逐字节相同 | `trace_copies.py`、`analyze_copies.py` |
| level 40 的切片结构与载荷统计没有明显异常 | SPS/PPS 解析通过；250 个切片头全部解析（`first_mb_in_slice = 0`、slice type 5/6/7、头长 3 B、CABAC 开）；93–100% 的切片含防竞争字节 | `slice_probe.py` |
| level 40 与 level 51 的载荷用字节统计分不开 | level 40 的 IDR 载荷熵 7.998 bits、零字节 0.5%；level 51 的 IDR 熵 7.998 bits、零字节 0.6% | `payload_stats.py` |
| 八种解码器实现给出同样的平灰 | 一律 mean 130.0、sd ≈ 0，详见 §14.7 的表 | `decode_report.py` |
| 参数集不是诱饵 | 换上 level 42 文件的 SPS/PPS（同为 1920×1080）后解码，原文件 sd 0.05、替换后 sd 0.00，两边都是平灰 | `swap_params.py` |
| 播放器 DLL 的算术解码表就是规范那套 | `rangeTabLPS` 在 DLL 里按指纹搜不到，但按反编译给出的地址 `DAT_1809023b0` 读出来，是**按 QP 主序、每值重复两字节**存放的同一张表（第一块 `80 80 … 7b 7b …` = 第 0 列；偏移 128 起 `b0 b0 a7 a7 …` = 第 1 列） | `find_tables.py`（误判）、`dump_get_cabac_tables.py`（更正） |
| 播放器渲染了 level 40 分段（实测） | 播放器在播 119354 的 9:21–9:31（level 40 段），喂进解码器的 SPS level 就是 40，解出的帧为 1920×1088、mean 220.3 / sd 34.96 与 mean 222.2 / sd 31.90（真画面）；同一批字节我们解出来是 mean 130.0 / sd 0.00 | `whats_playing.py` |

**HYPOTHESIS（假设）——只是提出，尚未验证。**

1. level 40 这一类分段受一层保护，而它住在播放器自己的**解码器**里（不等于熵解码表被改——
   那些表已经证明是规范的）：载荷看起来是普通带转义的 H.264，没有任何 stock 解码器能读，
   而播放器读得出来。这是目前唯一同时装得下 §14.4–§14.10 全部观测的说法，但它是
   **按排除法得出的定性结论**，机制本身没有被验证。下一步是把范围收窄到
   **CABAC 上下文初始化表**（每片开始时写入 `cabac_state` 的那 1024 项）：算术表是规范的，
   而按课/按流选择一套不同的初始上下文，正好能同时解释「标准流人人可解」和
   「level 40 只有播放器可解」。

**适用范围。** 「逐字节相同」仍然只对课程 `119354` 在抓包那个时刻证实（§13.1、§13.2）。
本节新增的限定是：这节课内部还要再分成 level 40 与 level 51 两类，两类在同一个文件集合里并存，
所以 §13.7 那张「哪些课能出画面、哪些是灰的」的表必须按分段重读。

### 14.2 「灰」是分段的属性，不是课的性质

工具：`tools/parser-tools/classify_segments.py`。它对手里**每一把有密钥的分段**做四件事：
解密、读第一个视频 PTS、读基本流里的 SPS level 字节、解码一帧并报出亮度 mean 与标准差。
三个候选——课内位置、编码器参数、文件本身——就这样被一张表分开：哪一列能把灰的行与其余行分开，
就是要追的东西。

判据（这是**工具的判据**，不是普适真理）：真实屏幕录制 mean 约 40–240 且 sd > 20；
解码失败是 mean 130.0、sd ≈ 0。

```
120 segments
  level 40  ×24    PTS 521 s – 751 s     23 个判为 GREY（sd 0.00–0.38），1 个 sd 5.65
  level 51  ×96    PTS 1993 s – 2943 s   全部是真画面
```

三个 level 的几何：

| level（SPS level 字节，§13.6 同样写作 level 40） | 分辨率 | 本批里的判定 |
| --- | --- | --- |
| 40 | 1920×1080 | 平灰 |
| 51 | 2992×1682 | 真画面 |
| 42 | 1920×1080 | 真画面（播放器后来下载的分段） |

所以**同一节课 `119354` 里两类分段都有**：「画面是灰的」既不是这条流水线的性质，也不（只）是课的性质，
而是某几类分段的性质。§2.3、§4 那两处「画面是灰的」，到这里应当读成
「该课里 level 40 那一类分段是灰的」。

三条限定，免得这张表被读得比它本身更强：

1. 那 1 个 sd 5.65 的分段是例外，值得单独点名：在 `captured/classify_all.txt` 里它的**判定列写的是
   `picture`**，而 sd 5.65 远达不到「真画面」的判据（sd > 20）。两处不一致，**不要拿它当
   「level 40 里存在能解出来的分段」的证据**；按判据它和另外 23 个同属灰的一类。
2. 23 个 GREY 分段的 sd 也不是全零（0.00–0.38），逐个值见 `captured/classify_all.txt`。
   「平灰」在这一类内部仍有程度差别，只是都远低于判据。
3. **level 与课内时段在这批数据里是共变的**：level 40 集中在 521–751 s，level 51 在 1993–2943 s，
   两个区间不重叠。同样共变的还有分辨率（level 51 是 2992×1682，level 40 与 level 42 都是 1920×1080）。
   所以这张表本身分不开「是 level 决定」「是课内时段决定」，还是「是下载批次决定」；
   要分开它们，需要同一时段里的两类分段，或者更多课的数据——`level_timeline.py` 就是为此准备的。

### 14.3 播放器此刻在放的这一课，参数集与文件里的逐字节相同，我们也能解出画面

工具：`tools/parser-tools/identify_playing.py`。它在播放器正放同一节课时抓两样东西：
播放器设置的密钥（由它指认是哪个文件），以及离开包的 SPS/PPS 字节。

对播放器这一轮取的两个 level 42 分段（`119354-faeb8a21…`、`119354-e7880f0d…`）：
到达解码器的 SPS 与 PPS 与**文件里的那份逐字节相同**；我们对这两个文件第 30 帧的解码是**真画面**——
mean 38.8、sd 23.88，一个暗色的 PyCharm 窗口，里面是 `middleware/response.py`。
mean 只有 38.8 是因为深色编辑器本来就不亮，让它成为画面的是 sd 而不是 mean——这句要和 §14.2 的判据一起读。

工具：`tools/parser-tools/nal_inventory.py`。它把到达解码器的包一个一个拆开，记录 NAL 类型与顺序：

```
{AUD:1, non-IDR:1}                  ×788
{AUD:1, SPS:1, PPS:1, IDR:1}        ×3
```

**参数集确实走带内。** 这关掉了「播放器在带外另给一份 SPS、文件里那份是诱饵」这一支。

限定：这两个分段是 **level 42**，不是 level 40。「播放器给一个 level 40 分段送进去的是哪份 SPS」
这一步没有测到——那正是 `watch_segment.py` 要回答的问题，也是 §14.12 那条被干扰的路。

### 14.4 每个包只解一次

工具：`tools/parser-tools/decode_calls.py`。它记录每次解码调用的 PTS、尺寸和整包哈希，再按 PTS 分组。
这一步不需要解密。如果播放器对同一段解两次——一次用磁盘上的字节、一次用它自己产出的字节——
那么「我们算得清的灰帧」和「屏幕上的真画面」来自两次不同的调用，前面所有的字节比对都不成立。

实测：**900 次解码调用带 900 个互不相同的 PTS**，也就是每个包恰好解一次，不存在第二次用不同字节的解码。

限定：这是本次观测窗口内的结论（窗口有限，工具按 PTS 分组统计）。它排掉的是「两次解码」这一族解释，
不是「解码器内部读了别的字节」——后者由 §13.5 与 §14.5 各关掉一扇门。

### 14.5 解码器从包里拷出去的字节是普通重定位

工具：`tools/parser-tools/trace_copies.py` 与 `tools/parser-tools/analyze_copies.py`。
DLL 里的 `memcpy` 是一个六字节 thunk，所以编译器发出的每次调用都经过**同一个地址**：
钩 RVA `0x7F6914`，只留下**源地址落在当前正在解码的那个包内**的拷贝，把目标缓冲区写到磁盘，
再与源窗口逐字节比对。

三笔拷贝——8,339 B、14,299 B、10,960 B——**与它们来自的包窗口逐字节相同**。
这一层看到的是纯搬移：没有东西在入包的路上解密载荷。

方法论值得单独记一笔：这个工具**早先的版本**在拷贝发生**之前**读目标缓冲区，
于是报告「每一笔拷贝都不同」。那是工具的 bug，不是变换。它和 §13.3 是同一种失败形态——
**读的时机错了**，于是沉默或者差异被当成了证据。同类工具都得先证明自己读的是拷贝之后的字节。

限定：本节只拿到这三笔拷贝的比对结果。「从包里出去的拷贝全都是重定位」这句话的强度到此为止。

### 14.6 level 40 的载荷在统计上与能解码的载荷分不开

工具：`tools/parser-tools/slice_probe.py`，用 exp-Golomb 正经解析 SPS、PPS 和每一个切片头。
对 level 40 与 level 51 的文件一视同仁：SPS/PPS 解析通过，**250 个切片头全部解析**
（`first_mb_in_slice = 0`、slice type 5/6/7、头长 3 字节、CABAC 开），
93–100% 的切片里存在防竞争（emulation prevention）字节。

工具：`tools/parser-tools/payload_stats.py`，按位置取模分桶后看熵与零字节比例：

| 文件 | IDR 载荷熵 | 零字节比例 |
| --- | --- | --- |
| level 40（平灰） | 7.998 bits | 0.5% |
| level 51（真画面） | 7.998 bits | 0.6% |

**两者在字节统计上无法区分。**

限定（这一条必须写明，否则这几个数会被读成比它更强的东西）：`payload_stats.py` 的 docstring 假设
「真 CABAC 载荷强偏斜、熵远低于 8 bits」，而**能正常解码的 level 51 载荷也没有表现出这个偏斜**
（7.998 bits、0.6% 零字节）。所以这一步能证明的是「灰载荷与好载荷分不开」，
不能证明「灰载荷一定是合法 H.264」。统计上不像均匀随机密文，不等于语法上合法。

### 14.7 八种解码器实现，同样的平灰——§13.8 的假设被推翻

工具：`tools/parser-tools/decode_report.py`（解码器错误行数 + 每帧亮度 mean/sd）。
作用在一个 level 40 文件上：

| 解码器 / 设置 | 错误行数 | 采样帧亮度 |
| --- | --- | --- |
| ffmpeg 8.1，默认 | 86 | mean 130.0 sd 0.00 |
| ffmpeg 4.2.2（`pip download imageio-ffmpeg==0.4.4`，二进制在 `D:\DevelopmentTools\_downloads\ffmpeg-old\x\imageio_ffmpeg\binaries\ffmpeg-win64-v4.2.2.exe`） | 60–79 | mean 130.0 |
| ffmpeg 8.1，`-threads 1` | 不变 | mean 130.0（不变） |
| ffmpeg 8.1，`-err_detect ignore_err+careful` | 不变 | mean 130.0（不变） |
| `h264_mf`（Media Foundation） | — | mean 130.0 sd ≈ 0 |
| `h264_d3d11va` | — | mean 130.0 sd ≈ 0 |
| `h264_dxva2` | — | mean 130.0 sd ≈ 0 |
| `h264_qsv` | — | mean 130.0 sd ≈ 0 |
| `h264_amf` | — | mean 130.0 sd ≈ 0 |
| `h264_cuvid` | — | mean 130.0 sd ≈ 0 |

错误文本（ffmpeg 8.1；`bytestream 189594` 指向 §13.6 那个 189,694 字节的 IDR）：

```
top block unavailable for requested intra mode -1
error while decoding MB 1 0, bytestream 189594
concealing 8160 DC, 8160 AC, 8160 MV errors in I frame
```

8160 = 120×68，也就是这一帧**全部**宏块。这一点细化了 §13.6 的读法：不是「丢了一个切片然后再没恢复」，
而是这个 I 帧的每个宏块都被 concealment 填满，后面每一帧继续灰是它的直接后果。

**结论：§13.8 的解码器版本假设被推翻。** 换解码器不改变结果——两代 FFmpeg 加六种系统/硬件后端，
八个路径给出同一个 mean 130.0、sd ≈ 0。

限定两条：

1. 这八条是八个**解码路径**（两代 FFmpeg 软件解码器，加 Media Foundation、D3D11VA、DXVA2、QSV、AMF、CUVID），
   不是八份互相独立的源码。它排除的是「我们选错了解码器或后端」，不是「一切可能的解码器」。
2. 八个里没有一个是播放器自己的解码器。所以「播放器能解」这件事仍然是间接推断（§14.13），
   不是这一节的实测。

### 14.8 参数集不是诱饵

工具：`tools/parser-tools/swap_params.py`。如果 level 40 的载荷是普通 H.264，只是 SPS/PPS 被换成了一份
「仍然能解析」的诱饵，那么换进一个能解码的分段的参数集就该把画面换回来。供体特意选 level 42：
它与 level 40 同为 1920×1080，所以分辨率不是那个变量。

实测：原文件 sd 0.05，替换后 sd 0.00。

**两边都是平灰，参数集不是那一层。** 这一步只能排掉「SPS/PPS 是诱饵」，不能反过来证明载荷是好的。

### 14.9 标准 CABAC 表在，只是换了布局（**本节更正 §14.9 最初的说法**）

> **更正（同一天，更晚一次测量）**：本节最初写的是「播放器的 DLL 里没有标准 CABAC 表」。
> 那是**指纹搜索的误判**，不是事实。从反编译拿到真正的表地址之后，把表读出来对比，
> 结论反过来了——见下面的「更正后的测量」。

工具：`tools/parser-tools/find_tables.py`，用已知 FFmpeg H.264 表的字节做指纹。
H.264 spec 的 `rangeTabLPS`（FFmpeg 里叫 `lps_range`，第 0–8 行）在
`D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll` 里**按这个指纹搜不到**——
既没有 12 字节的多行序列，也没有单独任何一行 4 字节出现。本机两份 stock 二进制里都有：

| 二进制 | 命中地址 |
| --- | --- |
| ffmpeg 8.1 | `0xce5e760`、`0xce76bc0`、`0xce8af60` |
| ffmpeg 4.2.2 | `0x39af0e0`、`0x39c4380` |

**更正后的测量。** Ghidra 反编译算术解码器 `FUN_1803C4140`（FFmpeg 的 `get_cabac`）直接给出了
它索引的表：`RangeLPS = DAT_1809023b0[state + (range & 0xC0) * 2]`，状态转移在 `DAT_180902630`，
重归一化移位在 `DAT_1809021b0`。按 PE 节表把 RVA 换成文件偏移（`dump_get_cabac_tables.py`）读出来：

* `DAT_1809021b0`（文件偏移 `0x9015b0`）是标准的重归一化移位表：`09 08 07 07 06 06 06 06 05 …`。
* `DAT_1809023b0`（文件偏移 `0x9017b0`）**就是规范的 rangeTabLPS**，只是排布不同：
  **按 QP 主序存放，且每个值重复两字节**。第一块（偏移 128–191）逐字节等于规范的第 1 列
  （`b0 b0 a7 a7 9e 9e 96 96 8e 8e 87 87 80 80 7a 7a …` = 176、167、158、150、142、135、128、122 …），
  第一块（偏移 0–127）等于第 0 列（`80 80 … 7b 7b 74 74 6f 6f …` = 128、128、128、123、116、111 …）。
* 因为转置 + 重复，state-major 的连续指纹当然搜不到——**指纹没命中不等于数不存在**。

所以 §14.9 原来的结论作废：**播放器的熵解码器用的是规范那套数**，`get_cabac` 的算术表没有改。

工具：`tools/parser-tools/probe_tables.py`。它把 stock 表**周围**的字节也拿来当指纹：
stock 表附近那十三个 32 字节窗口，在 DLL 里同样一个都不出现。这一条与上面的更正一致——
这个 build 的表是另一个排布，自然也不带 stock 的邻居字节。

工具：`tools/parser-tools/find_stock_cabac.py`。它扫的是**运行中的**播放器：全部 119 个已加载模块，
加上 544 段私有内存（472 MB）。按 state-major 指纹，整个进程里只有一处命中——NVIDIA 的
`nvd3dumx.dll` 里 `0x7ffe71d47230`；另外有一个堆块含有 `lps_range` 第 4 行四次。这两处现在都只是
「按错误指纹搜出来的东西」，不指向任何结论。

**这一节现在剩下的是一个否命题**：算术解码器的表是规范的，所以保护不在那里。§14.13 的排除法结论要按
这一点重读——被排除的机制多了一个。

### 14.10 DLL 其余部分是 stock FFmpeg 4.2.x，只有表的布局不同

按字符串证据，这个 DLL 的这部分就是 FFmpeg 4.2.x：

* 断言串是 `libavcodec/h264dec.c:0x3ed`（`buf_index <= buf_size`）与
  `:0x406`（`pict->buf[0] || !got_frame`）。
* 它的 H.264 源文件名集合与 ffmpeg 4.2.2 **完全相同**：`h264_cavlc.c`、`h264_direct.c`、
  `h264_picture.c`、`h264_refs.c`、`h264_slice.c`。
* 一条反过来的提醒：两份 stock 构建里**都没有** `libavcodec/h264_cabac.c` 这个字符串，
  所以「DLL 里没有 `h264_cabac.c`」什么也证明不了。**这条不能当证据用。**

Ghidra 里的形状：

| 位置 | 是什么 |
| --- | --- |
| `h264_decode_frame` `0x1800B9648` | 函数体 `0x1800B9648`–`0x1800B9939`，也就是 §13 一直钩的 RVA `0xB9648` |
| `decode_nal_units` `0x1800BA9AC4` | 由上面那个函数调用 |
| `FUN_1803BF228` | CABAC 切片解码；引用字符串 `cabac decode of qscale diff failed at %d %d`（`0x180a440b0`） |
| `DAT_1809691d0`、`DAT_1809691f0`、`DAT_180969258`、`DAT_180969268`、`DAT_1809692a0` | `FUN_1803BF228` 索引的五张表 |

stock FFmpeg 把表放在**一整块连续区域**里，表紧挨在 `lps_range` 的前后；
上面那五个分散的地址**不是**那个布局。所以能确证的是**布局不同**——这个 DLL 的熵解码路径被单独动过。
不能确证的是「表里的数被换成了什么」，以及「这一改动就是 level 40 解不出来的原因」。
后者仍然只是 §14.1 的假设 1。

### 14.11 一个不该重犯的测量错误：PE 文件偏移不是 RVA

拿 `disk[0xB9648]` 与 `module_base + 0xB9648` 比会得到不同的字节，看起来像
「内存里的映像不是磁盘上这个文件」。把 RVA 过一遍节表再比，就会发现**是同一个文件**。
`tools/parser-tools/dump_cabac_tables.py` 做的就是这件事。

该 DLL 的节表：

| 节 | RVA |
| --- | --- |
| `.text` | `0x1000` |
| `.rdata` | `0x7fc000` |
| `.data` | `0xbb5000` |
| `.pdata` | `0x12a8000` |
| `.rsrc` | `0x12e2000` |
| `.reloc` | `0x12e3000` |

RVA `0xB9648` 落在 `.text` 里（`0x1000` ≤ `0xB9648` < `0x7fc000`），所以它的文件偏移根本不是 `0xB9648`。
规则：**任何「文件里的字节 vs 内存里的字节」的比较，先把 RVA 过一遍节表。**
它与 §13.9 那条纪律同源——一个看起来像证据的差异，先怀疑自己的坐标变换，再怀疑目标。

### 14.12 注入实验：跑了，但被干扰——本节不从中得出任何结论

工具：`tools/parser-tools/inject_packet.py` 与 `tools/parser-tools/inject_segment.py`。
它们把我们自己解密出来的文件做成包，交给**播放器自己的解码器**：
`inject_segment.py` 按每个 AUD 把基本流切成访问单元，一次解码调用换进一个访问单元，
之后不做恢复——播放器下一个 IDR 最多一个分段远。

两个工具都真的跑了：解码器**吃下了每一个注入单元**（60 个单元的返回值都等于注入的字节数）。
但在这些调用上读到的 `AVFrame` 是空的；**同一个窗口里，播放器自己的调用也没有产出可读帧**：
帧结构体读出来是 width 1936、height 1088、format 53、stride 0、data `0x0`。
原因是当时播放器窗口根本不在屏幕上（机器在跑游戏）。

所以这条路线**被混淆（confounded）**：解码器在消耗字节不等于它在出帧，
返回值等于注入尺寸也只说明「它接受了这个尺寸」。**本节不从这里得出任何结论。**
它必须在播放器真的在渲染时重跑——这是 §14.13 的第一件待办。

### 14.13 结论、能用的部分、下一步

站得住的结论：**level 40 这一类分段受一层保护，而它住在播放器自己的解码器里**——
载荷看起来是普通带转义的 H.264，没有任何 stock 解码器（八种实现）能读，而播放器读得出来。
熵解码的**算术表已经排除**（§14.9 的更正：`rangeTabLPS` 就是规范那套，只是换了排布），
所以范围要往「每片开始时的 CABAC 上下文初始化」收——这是按排除法得到的定性结论，
机制本身未验证（§14.1 假设 1）。

**已经变成实测的那个问题**（本节写作时还是推断）：播放器确实渲染了 level 40 分段。
`whats_playing.py` 在它播放 119354 的 9:21–9:31 时读到：喂进解码器的 SPS level 是 **40**，
解出的帧是 1920×1088、mean 220.3 / sd 34.96 与 mean 222.2 / sd 31.90 —— 真画面；
同一批字节我们解出来是 mean 130.0 / sd 0.00。§14.12 那个注入实验因此不必再重跑。

实用的那部分，现在就可以用：**level 51 与 level 42 的分段解密正确、解码正确，可以直接导出；
只有 level 40 这一类被挡住。** 于是 §13.7 那张「哪些课能出画面、哪些是灰的」的表要按分段重读：
一节课可以是两类分段的混合，`119354` 就是。

下一步（本节结束时都还没做）：

1. 在播放器真的渲染时重跑 `inject_segment.py`，把「播放器能不能解 level 40」从推断变成实测（§14.12）。
2. 用 `dump_cabac_tables.py` 把 DLL 的五张表与 stock 的表逐项对齐，回答「是同一组数的重排，
   还是另一组数」——前者意味着可以照抄回去，后者意味着表被换成了别的东西。
3. 若第 2 步的答案是重排，再找谁在初始化这几张表；`FUN_1803BF228` 的五个索引点已经是入口。

### 14.14 本次会话新增的工具

| 工具 | 它回答的问题 | 需要 |
| --- | --- | --- |
| `classify_segments.py` | 每个分段在课内的位置、它的 SPS level 字节、它到底解不解得出来——也就是「灰」是分段的属性还是课的性质 | 密钥清单 + 分段文件 + ffmpeg |
| `identify_playing.py` | 播放器此刻在放哪个文件？它交给解码器的 SPS/PPS 与文件里的逐字节相同吗？我们解它出画面吗？ | 活着的播放器 |
| `nal_inventory.py` | 到达解码器的包一个一个长什么样：哪些 NAL 类型、参数集有没有随包走带内 | 活着的播放器 |
| `decode_calls.py` | 每个包被解一次、两次，还是两次但字节不同？ | 活着的播放器 |
| `trace_copies.py` | 解码器从包里拷出去的字节是什么？钩 `memcpy` thunk（RVA `0x7F6914`），只留下源地址在当前包内的拷贝 | 活着的播放器 |
| `analyze_copies.py` | 那些拷贝与来源窗口逐字节相同，还是有结构差异？ | `captured/copies/` |
| `slice_probe.py` | 切片载荷里有没有第二层：防竞争字节的密度、16 字节块对齐 | 两个 `.ts` 文件 |
| `payload_stats.py` | 载荷按位置取模分桶后，熵与零字节比例像真 CABAC、像密文，还是像稀疏加扰 | 两个 `.ts` 文件 |
| `decode_report.py`（§13.10 已有） | 这次解码出画面了吗？错误行数加每帧亮度 mean/sd | 一个文件，以及 ffmpeg |
| `swap_params.py` | 用另一个能解码的分段的 SPS/PPS 去解 level 40，画面能不能回来？ | 两个 `.ts` 文件 + ffmpeg |
| `find_tables.py` | DLL 里还有没有 FFmpeg 的 H.264 已知表（spec 的 `rangeTabLPS` / `lps_range`）？ | 一个二进制 |
| `probe_tables.py` | stock 表**周围**的字节在不在 DLL 里？把「表被换掉」与「整片区域被换掉」分开 | 两份二进制 |
| `find_stock_cabac.py` | 运行中的播放器里，哪个模块含标准表——如果有的话 | 活着的播放器 |
| `dump_cabac_tables.py` | 播放器的 CABAC 表在哪、长什么样；顺带按节表把 RVA 映射成文件偏移（§14.11） | 播放器 DLL + stock ffmpeg |
| `scan_module_tables.py` | 在**内存里**的模块字节中找 stock 表，而不是在磁盘上的文件里找 | 活着的播放器 + DLL 路径 |
| `inject_packet.py` | 把我们的一个 level 40 访问单元塞进播放器解码器的一次调用，看它返回什么帧 | 活着的播放器 |
| `inject_segment.py` | 把整个 level 40 分段的每个访问单元依次换进解码调用，看它能不能解出这节课的画面 | 活着的播放器 |
| `read_watch.py` | 打印 `watch_segment.py` 写下的记录，让 level 与画质的对应关系可读 | `captured/watch/watch.jsonl` |
| `watch_segment.py` | 播放器对 level 40 分段做什么：它送进去的 SPS 是不是文件里那一个，出来是不是画面 | 活着的播放器 |
| `level_timeline.py` | 把手里的每个分段排成一行：课内位置、SPS level、下载时间 | `captured/classify/` |
| `summarize_classify.py` | 把 `classify_segments.py` 的表按 level 与判定汇总 | `captured/classify_all.txt` |
| `count_decode.py`（§13.10 已有） | 这个进程到底有没有在解码，哪个 PID 是渲染进程？ | 活着的播放器 |
| `probe_avframe.py`（§13.10 已有） | 这个静态链接的 FFmpeg 里 `AVFrame` / `AVCodecContext` 的真实偏移 | 活着的播放器 |

二十三个都在 `tools/parser-tools/`。十二个只靠文件就能跑（`classify_segments.py`、`analyze_copies.py`、
`slice_probe.py`、`payload_stats.py`、`decode_report.py`、`swap_params.py`、`find_tables.py`、
`probe_tables.py`、`dump_cabac_tables.py`、`read_watch.py`、`level_timeline.py`、`summarize_classify.py`），
其余十一个需要活着的播放器；其中 `classify_segments.py`、`decode_report.py`、`swap_params.py`
还需要 ffmpeg（§14.7 用到了两个版本）。
