# EVPlayer2 5.0.5 下载视频解密结果

已将当前沙盒正在播放的 **1. 安装redis.mp4** 对应的 118 个分片解密，按播放器清单中的序号 0—117 合并，并无损转封装为普通 MP4。

成品：`1. 安装redis.mp4`。时长 19 分 42.035 秒，H.264 / AAC，1918 × 1080，yuv420p，视频共 35,460 帧。仅解密和转封装，没有重新编码。

验证：FFmpeg 对成品的完整视频与音频流进行了全程解码，退出码 0、无解码错误；另抽取第 60 秒画面，确认内容为 Redis 安装课程。

成品大小：43,516,215 字节。SHA-256：`A3AE54016001B83B9CE4D35599CBEF846DD6F9877AE3191DFD2E5451B62D2CF5`。

## 已验证的算法

分析对象为本机 `D:\StudyApps\EVPlayer2\PlayerLibRender56_vs.dll` 以及工作副本中解包后的 EVPlayer2.exe。以下偏移为播放库的 RVA，并非文件偏移。

| 环节 | 验证结果 | 主要位置 |
|---|---|---|
| 分片标识 | 播放清单中的 `119354-UUID.ts` 字符串 | 上下文 +0x18 |
| XOR 掩码 | 分片标识 MD5 小写十六进制文本的前 16 个 ASCII 字节 | 构造函数 0x3e930；上下文 +0x268 |
| 分片密钥 | `MD5(tk + 分片标识 + 运行时附加参数)` 的 32 个小写十六进制 ASCII 字节 | 0x42660、0x1fd60 |
| 解密顺序 | 密文先按 16 字节掩码循环 XOR，再 AES-256-ECB 解密；不使用 IV | 0x39080、0x3f4c0、0x40af0 |
| AES 实现 | 使用人工构造的已知输入调用原播放库，与标准 AES-256-ECB 对比一致 | 0x20ec0、0x20ea0 |
| 尾部填充 | 使用 `0x23`（`#`）填充到 16 字节边界；已对齐时无需额外填充 | 118 个真实分片逐一验证 |
| 原始封装 | 去除填充后为 188 字节一包的 MPEG-TS | 118 个分片的全部 TS 同步字节检查通过 |

特别注意：MD5 的 **32 字节 ASCII 十六进制文本**用于 AES 密钥；把它转换成 16 字节二进制 MD5 摘要会得到错误结果。

## 密钥来源与适用范围

只看最初的密文 ZIP 无法取得对应的运行时分片参数。本次通过 Windows Sandbox 的现有会话，只读检查 EVPlayer2 进程中与视频相关的分片上下文，取得分片标识、序号和参数；再用已经解密成功的分片验证密钥派生关系。没有修改原安装程序或播放进程代码。

工具附带的 `redis_segments.json` 仅保存这节视频的分片解密密钥、掩码、序号和输入 SHA-256。没有附带账户登录数据、原始会话令牌或运行时公共附加参数。

新版 `EVPlayer2_download.zip` 有 188 个文件。其中 118 个完整 TS 对应本节视频；另有 65 个不在当前播放清单中的 TS、1 个 `.part` 和 4 个 `.evtemp`。这些额外文件不属于本次成品的已验证清单，尚未解密。最初下载包中的那组 65 个 TS 也未取得对应课程的播放上下文。

## 可重复运行

安装 Python 3 与依赖：

```powershell
python -m pip install pycryptodome
```

将脚本、参数文件和新版 ZIP 放在同一目录，运行：

```powershell
python EVPlayer2_decode.py --input EVPlayer2_download.zip --manifest redis_segments.json --output redis_decoded.ts
ffmpeg -i redis_decoded.ts -map 0:v:0 -map 0:a:0 -c copy -movflags +faststart "1. 安装redis.mp4"
```

脚本也支持将 `--input` 指向已解压的分片目录。每片都会检查 SHA-256、填充、全部 TS 同步字节和包头；失败会保留 `.partial` 文件并报错，不会将未经验证的数据标记为成品。输出文件已存在时会停止，以免覆盖。

参数文件适用于本次对应的密文文件，不能作为其他课程或其他播放器版本的通用密钥。普通 MP4 可由支持 H.264/AAC 的主流播放器播放；无法保证没有这些解码器的播放器也兼容。

工具参考：[UPX 官方发布](https://github.com/upx/upx/releases)、[微软 Windows Sandbox 命令行文档](https://learn.microsoft.com/zh-cn/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-cli)。解密算法结论来自本地反汇编、原库对照实验和真实分片验证。
