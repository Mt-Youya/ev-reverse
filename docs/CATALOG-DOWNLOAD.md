# 目录、EVS 下载与稳定导出

这条链已经在本机用课程 `315187`、视频 `903780`（“2-2. React和Vue描述页面的区别”）跑通。它不依赖播放器进程，也不需要从播放器内存读取分片密钥；只需要一份当前登录会话的 JSON。

## 会话文件

会话文件由现有的播放器探针生成，包含当前账号的 bearer token、目录接口的动态 AES key、签名密钥、设备号和业务号。它是短期凭据，不要提交到公开仓库。播放器重新登录后重新生成即可。

重新生成用 `tools/refresh_session.py`：它按进程名找到播放器、跑探针，**先用目录接口校验新凭据再覆盖旧文件**，不通过就保留原文件并报出服务器原话。播放器重新登录会让旧凭据立即失效，此后每次导出都报 `-123: "长时间未登录，请退出重新登录"`——看起来像会话过期，实际是"重新登录"这个动作本身作废了旧文件，本机 2026-09-24 实测如此。

探针必须与播放器同为管理员权限：`EVPlayer2.exe` 的清单声明 `requireAdministrator`，它永远运行在高完整性级别，中完整性的 Frida 附加会被拒绝（`PermissionDeniedError: unable to access process … from the current user account`）。这个拒绝对该进程是**永久的，不是"目标正忙"**，重试无效。所以从普通终端运行 `refresh_session.py` 只会打印提权提示；要真正执行得用管理员终端，或加 `--elevate` 让它自己弹 UAC：

```powershell
# 在管理员终端里
& 'D:\DevelopmentTools\Anaconda\python.exe' -u tools\refresh_session.py
# 或者由脚本请求提权（需要有人能点 UAC 提示）
& 'D:\DevelopmentTools\Anaconda\python.exe' -u tools\refresh_session.py --elevate
```

结果同时写入 `verify_fresh/session.json.status.json`：只有是否成功、pid、字段名和 token 长度，不含任何凭据，供无人值守的循环读取。

## 一键导出

在仓库根目录运行：

```powershell
$env:PATH = "C:\Users\dd257\.cargo\bin;" + $env:PATH
cargo run -p evmedia -- export-evs `
  --session tools/parser-tools/captured/catalog/20260917-054033/api-session.json `
  --account 119354 --course 315187 --file 903780 `
  --output ev-videos/2-2-React和Vue描述页面的区别.mp4 `
  --work tools/parser-tools/captured/catalog/20260917-054033/rust-evs-export `
  --jobs 8
```

命令会依次完成：

1. 读取授权课程详情，确认 `file_id` 属于该课程；
2. 调用 `getEvsSignUrl` 下载 EVS 文件，调用 `getDownEVSKey` 解出下载描述符；
3. 用描述符中的文件名、`base_key` 和文件名 MD5 掩码解出完整 M3U8；
4. 用描述符中的 `req` 和 `cache_key` 请求每个分片的签名 URL 与 `tk`；
5. 并发下载分片，按 `MD5(tk + 文件名 + 固定后缀)` 推导每段密钥，AES-256-ECB 解密并按清单顺序合并；
6. 用 ffmpeg remux 为 MP4/MKV，并用 ffprobe 检查视频轨道。

中间文件写入 `--work`：`original.m3u8`、`list.json`、`enc/`、`manifest.json`、`lesson.ts` 和 `report.json`。分片下载是可恢复的，已有文件会复用。

重跑同一节课：删掉成品再跑一次即可。`lesson.ts` 是每次重新生成的中间产物，重跑时会自动清掉；`enc/` 里的分段会复用，不会重新下载。真正被保护的是 `--output`：它已存在时默认拒绝，要覆盖得显式加 `--force`。

## 已验证的协议边界

- 目录接口（`getEvsAuthorityCourse`、`getEVSCourseDetail`、`getEvsSignUrl`、`getDownEVSKey`）使用会话里的动态目录 key，外层协议版本为 `200`。
- 可下载 EVS 的分片接口由下载描述符指定；本课返回 `/student/getPlayTimeKeySignEVS20231103`，外层协议版本为 `202`。签名字段包含 `type=0`，并以服务器签名密钥作为尾部 secret。
- EVS 文件本身不是直接的 M3U8：先对文件字节按 `MD5(文件名)[:16]` 的 ASCII 十六进制字符串循环 XOR，再用描述符 `base_key` 的 32 个 ASCII 字节做 AES-256-ECB，去掉末尾 `#` 填充即可得到完整清单。
- 本机实测目录为 77 门课程、2747 个视频；目标视频清单有 191 段。返回的 191 个分片名与 EVS 内嵌 M3U8 逐项、逐序一致，191 个分片全部下载、解密和完整解码通过。

## 分步调试命令

只下载并解出 EVS 与 M3U8：

```powershell
cargo run -p evmedia -- download-evs `
  --session <session.json> --account 119354 --course 315187 --file 903780 `
  --output <work>\lesson.evs
```

只刷新完整课程目录：

```powershell
cargo run -p evmedia -- catalog `
  --session <session.json> --account 119354 `
  --output <work>\catalog
```

如果接口返回“当前账号已经在其他设备上登录”或 token 过期，应在 EVPlayer2 中重新登录并重新生成会话文件；导出程序本身不需要以管理员权限附加播放器。
