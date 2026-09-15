# EVPlayer2 CLI 实机问题与修复记录

本文记录 2026-09-13 至 2026-09-14 在真实 EVPlayer2 5.0.5 播放器上发现的问题。
每一项都来自实际运行、复现测试或两者；它不是离线解密方案，也不改变前提：segment key
和 lesson index 只在播放器完成该 segment 的播放解密后才会出现在内存中。

## 1. `grab` 在首次扫描前崩溃

**现象**：命令在启动后报错：`Cannot drop a runtime in a context where blocking is not allowed`。

**根因**：`main` 使用 `#[tokio::main]`，而 `grab` 内部使用 `reqwest::blocking`。阻塞客户端在
Tokio 运行时中析构会触发该 panic。

**修复**：CLI 入口改为同步；只有 `download` 子命令在自己的 Tokio runtime 中执行异步下载。
`grab`、`recover` 和媒体命令均在 runtime 外运行。

**验证**：`crates/evmedia/tests/grab_cli.rs` 的
`grab_runs_without_panicking_inside_an_async_runtime` 会启动真实 CLI，并确认空上下文进程可以
正常结束。

## 2. 多个 lesson 共存时发生串课

**现象**：同一玩家进程先后/同时保留两个 lesson 的 playback context。lesson index 都从 0
开始，旧实现按 index 聚合会把不同文件覆盖或跳过，最终可能得到时长看似合理、内容却混合的
TS/MP4。

**根因**：随机 segment 文件名不携带顺序；URL 查询参数 `sid` 是学员标识，不是 lesson 标识。
按 `sid` 或全局 index 都不能划分 lesson。

**修复**：

- 从已签名 segment URL 的父目录读取 lesson UUID；
- 新增 `grab --lesson <UUID>`。同时看到多个 lesson 时命令列出可选 UUID 并拒绝猜测；
- `capture-lesson.txt` 绑定输出目录。续跑只接受同一个 UUID；没有此标识的旧输出目录拒绝
  直接续跑；
- `WinSource` 在将 key 插入 index map 前按已知文件集合过滤，避免别的 lesson 覆盖相同
  index；
- `recover` 按 lesson 分组输出，避免不同 lesson 共享 `dec/<index>.ts`。

**验证**：CLI 测试覆盖“多个 lesson 必须显式选择”和“输出目录不能改绑 lesson”；Windows
扫描测试覆盖同一 `sid` 下不同 URL 父目录仍是不同 lesson。实际抓取中也观察到 106 个 key
对应 86 个唯一 index 的冲突，修复后 Koa lesson 得到连续的 0–342 共 343 段。

## 3. 播放 context 或 URL 消失后无法续跑

**现象**：播放器会释放旧 context 和已过播放窗口的 URL。此前已获得的 filename、key 或
index 只存在进程内存，重跑后无法再归位或解密已缓存的密文。

**根因**：抓取状态仅存活于一次轮询循环；代码错误地把 URL 当成解密的必要条件。

**修复**：新增输出目录内的 `grab-state.json`，原子保存 filename → key、filename → index
和已见 index。每次网络请求前先落盘。后续只要密文仍在 `enc/`，即使 URL 已释放也可以解密。
状态文件含 key，已加入 `.gitignore`，不得公开或提交。

**验证**：抓取循环测试覆盖“context 释放后、密文稍后出现仍可解密”和“URL 释放后仍从缓存
解密”。

## 4. 已下载的播放器缓存没有被复用

**现象**：播放器本地目录已经有密文，但抓取仍请求短期签名 URL；URL 过期时会遗漏 segment，
也会不必要地下载同一份数据。

**根因**：`grab` 只检查自己的 `enc/` 目录。

**修复**：新增 `grab --cache <播放器下载目录>`。每轮将属于当前 lesson、长度合理且 AES
块对齐的密文复制到 `enc/`，再进行密钥验证和解密；不会修改播放器的原缓存，也不建立会受
源目录变化影响的硬链接。

**验证**：抓取循环测试确认过期 URL 不会被访问，并校验源缓存字节保持不变。实际 Koa 导出
使用 `D:\EVPlayer2_download` 补齐了 sweep 期间获得 key 的 segment。

## 5. 已知尾段缺失仍可能被标为完整

**现象**：旧合并逻辑只看已成功解密的 index。例如已见 0–5、其中 5 没有 key 时，0–4 会被
误写为 `lesson.ts`。

**根因**：完整性判定忽略“播放器已经暴露、但尚未解密成功”的最高 index。

**修复**：合并函数接受最高已见 index。只有实际解密文件精确覆盖连续的 `0..=最高已见
index` 时才写 `lesson.ts`；否则写 `lesson.partial.ts` 并拒绝 `--mp4`。

**验证**：`a_known_tail_without_keys_must_not_be_reported_as_complete` 先制造缺尾，再移除该
段 URL 后重跑，确认两次都不会出现 `lesson.ts`。

## 6. `recover` 在缓存缺段时中断

**现象**：`recover` 的 key 库可以包含尾段，但播放器缓存尚未落盘对应密文。旧实现仍把该
index 传给合并器，打开不存在的 `dec/<index>.ts` 时直接报“系统找不到指定的文件”。

**根因**：恢复路径将“key 已知”误认为“明文已经成功写入”。

**修复**：`recover` 只合并 `valid_dec_file` 验证通过的分段，并将 key 库中的最高 index 交给
完整性判定。缺段结果为 `lesson.partial.ts`，不会导出 MP4；待缓存补齐后可续跑。

**验证**：`recover_with_a_missing_cached_tail_keeps_a_partial_merge` 用缺尾缓存复现该场景，
确认命令正常结束、保留 partial TS 且不产生 MP4。

## 7. MP4 可能不可播放或失败后覆盖旧成品

**现象**：直接将任意 TS copy 到 MP4，不能保证普通播放器接受其视频像素格式、视频编码或
音频编码；只检查 FFmpeg 退出码也无法发现某些解码错误。失败时若直接写目标文件，还可能损坏
已有成品。

**根因**：旧路径没有事前探测、没有全程解码验证，也没有临时发布策略。

**修复**：

- 使用 `ffprobe` 检查流；H.264/yuv420p + AAC 直接转封装，其他视频转 H.264/yuv420p、音频
  转 AAC；
- 写入 PID 专属 `.pending.mp4`，并要求 FFmpeg 在 error 级日志、`-xerror` 和
  `-err_detect explode` 下成功；
- 对临时 MP4 的完整视频和可选音频流执行全程解码；
- 只有验证成功才 rename 为 `lesson.mp4` 并发出 MP4 artifact；失败删除临时文件，保留 TS
  和既有 MP4。

**验证**：三个显式媒体集成测试覆盖真实音视频端到端解密/转封装、MPEG-2/MP2 转 H.264/AAC，
以及无效输入不能覆盖已有 MP4。Koa 实机成品为 3427.033107 秒、H.264/yuv420p + AAC，
全程解码验证通过。

## 8. Windows 权限与扫描工具互相干扰

**现象**：当 EVPlayer2 以管理员权限运行时，普通权限 CLI 无法读取其内存，Windows 返回
`ERROR_ACCESS_DENIED`。同时运行写页守卫探针会改变内存页行为，干扰只读扫描。

**处理**：这不是可以在 CLI 内绕过的限制。以管理员 PowerShell 运行 CLI；同一 PID 上不要
同时运行 `probe_write.py` 与 `grab`/`recover`。该要求已写入 CLI 导出指南。

## 仍然存在的边界

- 不播放就没有对应的 segment key 和 lesson index；离线密文、随机文件名和密文内容都不能
  推导顺序。
- `complete` 表示已暴露的 lesson index 连续且已经解密，不表示程序能从尚未暴露的尾段推断
  课程总长度。实际交付仍应让播放器到结尾，最好再以课程目录总时长交叉核对。
- `recover` 对修复前生成、没有 lesson UUID 的旧 key 库只能做保守分组；它不能可靠地补回
  当时丢失的 lesson 归属。
- 输出目标是 H.264/AAC MP4，适用于具备这些解码器的常规播放器；无法保证缺少相关解码器的
  软件也能播放。

## 验证汇总

2026-09-14 已运行：

```powershell
cargo test -p evmedia-core -p evmedia-contract -p evmedia-win -p evmedia
cargo test -p evmedia-core --test media_remux -- --ignored
cargo build --release -p evmedia
```

常规测试 53 项和媒体集成测试 3 项均通过。实机导出和文件参数见
[CLI-EXPORT.md](CLI-EXPORT.md)。
