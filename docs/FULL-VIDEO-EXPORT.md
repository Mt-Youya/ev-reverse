# 播放后自动导出完整视频

运行导出监听，再在 EVPlayer2 中打开任意一节已授权课程。工具读取当前视频的完整 VOD
播放清单，一次申请全部分段的签名 URL 和密钥，独立下载、解密并按原始顺序合成。
不需要把视频播放到结尾，也不依赖播放器已经下载完整课程。
若视频已经开始播放，工具也会尝试读取仍在播放器内存中的当前完整清单和会话凭据；
存在多份不明确的清单或令牌时，等待实际播放请求来确认归属。

```powershell
python -u tools/export_video.py --cache D:\EVPlayer2_download
```

也可双击 `tools/自动导出整课.cmd`。默认输出在仓库的 `ev-videos/`；监听继续运行，
切换课程后识别并导出下一节，已完成的视频不会在同一次监听中重复导出。
工作清单、会话凭据和可续用的下载缓存位于 Git 忽略的
`tools/parser-tools/captured/full-video/`。

导出 MKV：

```powershell
python -u tools/export_video.py --container mkv --cache D:\EVPlayer2_download
```

只导出下一节并指定名称：

```powershell
python -u tools/export_video.py --output D:\Videos\课程.mp4
```

前置依赖是 64 位 Windows、Python、Frida、当前源码构建的 `target/release/evmedia.exe`，
以及 PATH 中的 FFmpeg/ffprobe。Python 会自动使用本项目已安装的
`tools/parser-tools/captured/runtime/`，其他机器可安装 `python -m pip install frida`。
构建 CLI：`cargo build --release -p evmedia`。

### 受保护 H.264 的兼容解码器

部分课程的 H.264 CABAC 上下文被播放器改写，标准 FFmpeg 能读到流参数却会花屏或在首帧报错。
导出器会在完整解码校验失败时自动探测兼容上下文，用 `target/release/evmedia-ffmpeg.exe`
恢复视频像素，再用普通 FFmpeg 无损编码并复用原始音轨。桌面端和 CLI 必须与该文件放在同一
`target/release` 目录；也可以用 `EVMEDIA_EVC_DECODER` 指定路径。构建它需要已安装的
FFmpeg 源码构建工具链：

```powershell
powershell -ExecutionPolicy Bypass -File tools/evc-decoder/build.ps1
```

发布前会对视频、音频执行 `ffmpeg -v error -xerror` 全量校验；探测不到唯一上下文时保留
日志并不发布成品。
分段边界的 AAC 音轨可能出现重复或回退的 DTS。最终流复制封装允许 FFmpeg 修正时间戳，
并记录警告；视频解码、编码以及成品完整音视频解码校验仍使用严格错误检查。
兼容参数从已解密并按清单合并的 `lesson.ts` 首帧探测，不能使用 `enc/` 中仍为密文的下载缓存。
本地回归测试会在工作目录放入密文缓存，覆盖桌面端实际导出的目录结构，避免误报“0 个候选”。

当前进程探针地址适用于本机已核验的 EVPlayer2 5.0.5 / `PlayerLibRender56_vs.dll` 构建。
不同播放器构建或不使用这种 TS/VOD 清单的媒体格式需要另行适配，不能用这两节课程的结果
宣称所有历史加密格式已支持。存在多个播放器时默认选择驻留内存最大的实例，也可指定 `--pid`。

## 完整性判据

* 原始清单从分段 0 开始，并有 `#EXT-X-ENDLIST`；播放窗口的全部文件名必须唯一匹配该清单。
* 服务器返回的全部文件名和顺序必须等于原始清单，不能把五段窗口重新编号后冒充整课。
* 下载、密钥推导和解密必须覆盖清单中全部分段。视频和可选音轨一起合成，保留课程原画面。
* 输出时长必须接近原始 `EXTINF` 总时长，且完整视频和音频解码通过 `-xerror` 检查。
* 校验前的 `.partial.mp4` / `.partial.mkv` 只放在工作目录；全部检查成功后才向输出目录发布最终文件和 `report.json`（报告位于工作目录）。
* 桌面端显示视频转换百分比和完整校验阶段。下载达到 100% 并不代表导出完成；以任务成功和最终文件为准。

断开监听可按 Ctrl+C；下载缓存保留。失败会保留日志，不会把失败输出报告为完整视频。

## 本机已捕获会话的复用

还可指定 `--from-capture <capture_local_decode 目录>`，并配合 `--pid` 从仍在运行的播放器
读取完整清单，或配合 `--playlist <完整原始 M3U8>`。这仅用于复用本机捕获，常规使用无需手动找清单。
会话过期时应重新播放获取新凭据。
