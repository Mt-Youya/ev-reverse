# CLI 导出与验证

需要 Rust、FFmpeg 和 ffprobe；两个媒体工具均需位于 PATH。

```powershell
cargo build --release -p evmedia
Get-Process EVPlayer2 | Select-Object Id
```

在播放器打开目标课时并保持播放，每个课时使用独立输出目录：

```powershell
.\target\release\evmedia.exe grab --pid <PID> --output verify_out_lesson --mp4 --json-events
```

内存里若存在多个课时，命令会列出课程目录 UUID 并停止；使用 `--lesson <UUID>` 明确选择。
`capture-lesson.txt` 固定该输出目录所属的课时，续跑时自动采用，防止 index 重复导致串课。
课程标识取自 URL 的目录；查询参数 `sid` 是学员标识，不能用来区分课时。
没有课时标识的旧输出不能直接续跑，应使用新目录并复制旧 `enc/` 缓存。

如果播放器以管理员身份运行，CLI 也需要在管理员 PowerShell 中运行，否则 Windows
会拒绝读取内存。不要同时对该 PID 运行 `probe_write.py`；页守卫仪器与内存扫描会互相干扰。

已下载的加密 TS 可以通过 `--cache D:\EVPlayer2_download` 直接复用；CLI 在每轮扫描后
将匹配当前课时的完整缓存复制到 `enc/`，保留原文件名，不修改播放器原始缓存。
也可以手动复制到输出目录的 `enc/`。
`grab-state.json` 保存已取得的密钥、文件名、index 和已知缺口；同一课时重跑时恢复。
该文件含解密密钥，不应提交或公开。不要把不同课时写进同一个输出目录。

`--mp4` 的处理过程：

1. 检查已知 index 是否从 0 连续排列，包括已经看到但尚未取得密钥的尾段。
   有缺口时输出 `lesson.partial.ts`，不转 MP4。
2. H.264 / yuv420p 视频和 AAC 音频直接转封装；其他编码转换成这些格式。
   转码有耗时和有损压缩成本，无音轨的视频允许导出。
3. 对临时 MP4 的音视频全程解码，出现错误就返回失败并保留 TS。
4. 验证通过才发布 `lesson.mp4` 和 MP4 artifact 事件。验证失败不覆盖已有 MP4。

`recover` 同样只合并实际解密成功的分段；已知尾段缺失时保留 `lesson.partial.ts`，
不会因为缓存文件缺失而中断合并，也不会将缺尾的结果转成 MP4。

完整性边界：内存尚未暴露的尾段无法计数。`complete` 仅证明已知 index 连续、已知缺口补齐，
不能单凭等待超时证明整节课已播放完。实际交付还需要让播放器到达结尾，并核对课程时长。
输出目标是支持 H.264/AAC 的常规播放器，不能保证缺少相应解码器的所有软件均能播放。

本地验证命令：

```powershell
cargo test -p evmedia-core -p evmedia-contract -p evmedia-win -p evmedia
cargo test -p evmedia-core --test media_remux -- --ignored
ffprobe -v error -show_streams -show_format -of json verify_out_lesson/lesson.mp4
ffmpeg -v error -xerror -err_detect explode -i verify_out_lesson/lesson.mp4 -map 0:v:0 -map '0:a:0?' -f null -
```

媒体集成测试使用生成的音视频验证加密、解密、顺序合并和转 MP4；它不替代真实播放器抓取验收。
FFmpeg 参数语义见 [官方文档](https://ffmpeg.org/ffmpeg.html)。

## 实机验收记录（2026-09-14）

- `verify_out_live_20260913/lesson.mp4`：画面标题为《GSAP 基础入门》，861.251338 秒，
  1920×1080，H.264/yuv420p + AAC，92,562,625 字节，全程解码通过。
- `verify_out_scoped_20260914/lesson.mp4`：画面内容为 Koa API，343 段，index 为连续的
  0–342；3427.033107 秒，1918×1080、30 fps，H.264/yuv420p + AAC，167,552,834 字节。
  抓取程序完成转封装和全程音视频解码验证，最终事件为 `complete`，退出码为 0。
  SHA-256：`c3cead7235032c6ce974c2b77f1e47f5d0a5dcb760ae9b97f0644a3d7a8922cf`。
- 以上名称根据画面识别，尚未与课程目录总时长核对，完整性含义仍受前述边界限制。
- 四个 CLI 相关 crate 共 53 项测试通过；另外显式运行的 3 项媒体集成测试通过。
  `cargo build --release -p evmedia` 成功，输出为 `target/release/evmedia.exe`。
