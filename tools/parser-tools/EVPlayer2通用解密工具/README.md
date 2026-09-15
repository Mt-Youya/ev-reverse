# EVPlayer2 5.0.5 分片解密工具

本工具适用于本机已验证的 **EVPlayer2 5.0.5、64 位 Windows**。它针对当前正在播放且由当前账号有权播放的视频生成一次性的分片清单，再将对应 ZIP 解密、合并和无损转封装为 MP4。

它不是跨播放器或跨版本的万能破解器。EVPlayer2 更新播放器布局、分片格式或密钥派生方式后，采集步骤会停止并提示不兼容；不会输出未经验证的视频。

## 使用方法

1. 将工具目录放在 `EVPlayer2_download.zip` 同级目录；也可把 ZIP 放入工具目录。文件名必须是 `EVPlayer2_download.zip`。
2. 在同一个 Windows 会话中启动 EVPlayer2，登录后打开这份 ZIP 对应的视频，等待开始播放。
3. 在这个播放会话内双击 `生成参数.cmd`。若使用 Windows Sandbox，请在宿主机运行 `在沙盒中生成参数.ps1`；它会自动共享目录并在当前 Sandbox 中执行采集。
4. 回到有 Python 和 FFmpeg 的环境，双击 `解密并转MP4.cmd`。它会生成 `decoded.ts` 和 `decoded.mp4`。

Python 依赖：`python -m pip install pycryptodome`。需要 `ffmpeg` 在 PATH 中，才会生成 MP4；`decoded.ts` 仍是标准 MPEG-TS。

## 结果检查

工具为每个分片保存 SHA-256，并在解密时检查输入完整性、AES 输出尾部、每个 188 字节 TS 包的同步字节与包头。任一检查失败会停止，输出只会保留为 `.partial`，不会误报成功。

`evplayer2_manifest.json` 包含本次视频的分片密钥和校验值，应按课程资料妥善保存；它不包含登录密码或会话令牌。要处理另一节视频，应重新播放该视频并重新运行“生成参数”。
