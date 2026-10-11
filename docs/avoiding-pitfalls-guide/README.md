# 避坑指南 · ev-reverse

本仓库历史上跑过 56 个代理会话（Claude Code 4 · DSH 21 · Codex 31，2026-09-13 → 2026-10-11），
跨三个工具、近十种会话格式。这份指南把这些会话里**真正咬过人的坑**按主题收拢，每条都带会话出处与行号，
关键几条还把原文切片存进 `evidence.gz`。

它和 [`docs/RETROSPECTIVE.md`](../RETROSPECTIVE.md) 的分工是明确的，两者不重复：

| | RETROSPECTIVE.md | 本指南 |
| --- | --- | --- |
| 组织方式 | 按时间线的一次项目复盘 | 按**主题**分类，批次追加 |
| 视角 | 单条主线的技术结论与地址表 | **跨会话、跨工具**的重复踩坑模式 |
| 判据 | 可复现的命令 | 会话 id + 行号 + 原文切片 |
| 回答的问题 | 「这个项目现在到哪了」 | 「这类问题怎么不再犯」 |

已经写进 RETROSPECTIVE §6（验证方法学）和 §12（操作层面的坑）的条目，本指南**不再重复**；
这里收录的是那两节没覆盖、或只在别的会话里出现过一次就再没人记下来的东西。

## 怎么读

- 只想知道「我现在该防什么」→ 看下面的索引表，按 severity 挑。
- 想复盘某条 → 点条目里的 `📎`，或 `pack-evidence.mjs show --id <ID>`。
- 想在对话里直接引用 → 读对应的主题文件，正文是中文，元数据字段保持英文（脚本按英文键解析）。

## 分类是固定的 8 个主题

主题集合**不随批次增长**——新增的坑一律归入已有主题，读者找的是「这类问题怎么防」，
不是「哪一天、哪个工具说的」。每条坑的 `batch` 字段记录它属于哪一批。

| 前缀 | 主题 |
| --- | --- |
| `VER-` | 验证方法与证据强度 |
| `DATA-` | 数据契约、编码与格式 |
| `DB-` | 持久化与状态存储 |
| `AUTH-` | 凭证、令牌与权限 |
| `MEDIA-` | 音视频解码与抓取 |
| `DEPLOY-` | 上传、存储与发布 |
| `AGENT-` | 代理行为规范与协作 |
| `TOOL-` | 本地工具链 |

批次的来源会话在 `.agent-sessions/`（**已 gitignore，不进仓库**）。要重新导出：

```powershell
node .agents\skills\agent-session-archaeology\scripts\find-agent-sessions.mjs export --full --project .
```

## 证据存档

一个存档，不是每条坑一个文件——几十个小 `.gz` 既难管、压缩率也更差。

```powershell
$S = '.agents\skills\agent-session-archaeology\scripts\pack-evidence.mjs'
node $S list   --guide docs/avoiding-pitfalls-guide
node $S show   --guide docs/avoiding-pitfalls-guide --id VER-01
node $S check  --guide docs/avoiding-pitfalls-guide      # 体积审计，超预算退出码 2
node $S unpack --guide docs/avoiding-pitfalls-guide       # 解开一份可读副本（放到仓库外）
```

切片规则：**只存支撑这一条坑的那一段连续行**（通常 20–80 行）。整条会话动辄几 MB，存进来几个批次仓库就废了。
正文里能说清的直接内联，不挂 `📎`。

<!-- BEGIN:auto-index -->
## Topic index

| Topic | Pitfalls | Coverage |
| --- | --- | --- |
| [验证方法与证据强度](./01-verification.md) | 16 | 判据本身错了，比不做验证更糟：它会给出一个可以被引用、被写进文档、被下游信任的结论。 |
| [数据契约、编码与格式](./02-data-contracts.md) | 13 | 两个系统对同一个东西的表示不一致时，错误不会停在边界上，它会一路传播到最后的产物里，并以 |
| [持久化与状态存储](./03-persistence.md) | 3 | 缓存是「上次的答案」，而问题会变。 |
| [凭证、令牌与权限](./04-credentials.md) | 3 | 短期凭证决定了「什么时候必须抓、抓不到就没有第二次」。 |
| [音视频解码与抓取](./05-media-pipeline.md) | 6 | 「解密成功」和「画面能看」是两件事，中间隔着一个解码器。 |
| [上传、存储与发布](./06-deploy-and-storage.md) | 6 | 远端存储的失败方式和本地相反：它不会报错，它返回空。 |
| [代理行为规范与协作](./07-agent-conduct.md) | 7 | 这一节最贵。它记的不是代码错，是**代理的判断错**：过早宣布成功、把没做完的清单递给用户、 |
| [本地工具链](./08-local-tooling.md) | 10 | Windows + PowerShell + 一堆原生工具，组合出的失败模式比代码里的多。 |

**64 pitfalls across 8 topics.**

## All pitfalls

| ID | Pitfall | Topic | Severity | Status |
| --- | --- | --- | --- | --- |
| [AGENT-01](./07-agent-conduct.md#agent-01) | commit message 不合仓库约定 | 代理行为规范与协作 | high | resolved |
| [AGENT-02](./07-agent-conduct.md#agent-02) | 用户说「全部做完」时，递回去一份「还没做」的清单 | 代理行为规范与协作 | high | resolved |
| [AGENT-03](./07-agent-conduct.md#agent-03) | 宣称导出成功，而产物没有声音 | 代理行为规范与协作 | high | resolved |
| [AGENT-04](./07-agent-conduct.md#agent-04) | 已经被推翻的结论，继续留在文档和卡片上 | 代理行为规范与协作 | medium | resolved |
| [AGENT-05](./07-agent-conduct.md#agent-05) | 用 `git reset` 重做提交，把工作区退回旧基线，GUI 代码当场从磁盘消失 | 代理行为规范与协作 | high | resolved |
| [AGENT-06](./07-agent-conduct.md#agent-06) | 一个控制进程把单项失败放大成整批失败，而且丢掉了日志 | 代理行为规范与协作 | high | resolved |
| [AGENT-07](./07-agent-conduct.md#agent-07) | 代理与复核的断言，必须自己按行号复验 | 代理行为规范与协作 | high | resolved |
| [AUTH-01](./04-credentials.md#auth-01) | 含会话令牌的文件不得提交（用户明令，写进 `.gitignore` 的理由） | 凭证、令牌与权限 | high | resolved |
| [AUTH-02](./04-credentials.md#auth-02) | 把「密钥可以离线推导」误读成「什么都不需要播放器」 | 凭证、令牌与权限 | medium | resolved |
| [AUTH-03](./04-credentials.md#auth-03) | 善后动作把真实密钥和运行产物又带回仓库 | 凭证、令牌与权限 | high | resolved |
| [DATA-01](./02-data-contracts.md#data-01) | 目录名在每一层重复，最长前缀匹配把 220 集全判成「不在 B 站」 | 数据契约、编码与格式 | high | resolved |
| [DATA-02](./02-data-contracts.md#data-02) | 章节化课程里的同名标题，被 `{title: duration}` 折叠成了一个 | 数据契约、编码与格式 | high | resolved |
| [DATA-03](./02-data-contracts.md#data-03) | 远端对象按裸文件名做键，覆盖掉另一章的 0.4 GB | 数据契约、编码与格式 | high | resolved |
| [DATA-04](./02-data-contracts.md#data-04) | 密钥的两种表示：32 个十六进制字符**直接当 32 字节**用 | 数据契约、编码与格式 | high | resolved |
| [DATA-05](./02-data-contracts.md#data-05) | 「清除已完成」用「保留未跑完」实现，失败与取消的行被一起删掉 | 数据契约、编码与格式 | high | resolved |
| [DATA-06](./02-data-contracts.md#data-06) | 一个字段漏改名：`duration_seconds` 过 IPC 仍是 snake_case，1910 行时长全空 | 数据契约、编码与格式 | medium | resolved |
| [DATA-07](./02-data-contracts.md#data-07) | 同一棵目录树两套遍历：界面那条带路径、导出那条不带 | 数据契约、编码与格式 | high | resolved |
| [DATA-08](./02-data-contracts.md#data-08) | `export-evs` 成功退出却不发 `finished` 事件，按契约每一行成功都被判失败 | 数据契约、编码与格式 | high | resolved |
| [DATA-09](./02-data-contracts.md#data-09) | 把「不在我的对照表里」读成「没上传过」，差点据此删掉 255 GB | 数据契约、编码与格式 | high | resolved |
| [DATA-10](./02-data-contracts.md#data-10) | 中断导出目录里的 `.ts` 分片，被当成成片视频统计 | 数据契约、编码与格式 | medium | resolved |
| [DATA-11](./02-data-contracts.md#data-11) | 上游字段的语义要实测：`order_num` 本身就是降序 | 数据契约、编码与格式 | low | resolved |
| [DATA-12](./02-data-contracts.md#data-12) | `evs_playkey` 与 `descriptor.cache_key` 只在**一条**流程里相等 | 数据契约、编码与格式 | medium | resolved |
| [DATA-13](./02-data-contracts.md#data-13) | 密钥来源的模型错了两次：不走 OpenSSL，且捕获是**陈旧**的 | 数据契约、编码与格式 | high | resolved |
| [DB-01](./03-persistence.md#db-01) | 分P 缓存只按时间戳判新旧，而文档承诺它「不可能凭空造出一个分P」 | 持久化与状态存储 | high | resolved |
| [DB-02](./03-persistence.md#db-02) | 陈旧缓存把「全部不在 B 站」当成了答案 | 持久化与状态存储 | high | resolved |
| [DB-03](./03-persistence.md#db-03) | 静默回退到过期缓存，让「19 秒前的清单」当了两周的事实 | 持久化与状态存储 | high | open |
| [DEPLOY-01](./06-deploy-and-storage.md#deploy-01) | R2 的 `CopyObject` 会改写 ETag，ETag 不能当拷贝/回滚的等价判据 | 上传、存储与发布 | high | resolved |
| [DEPLOY-02](./06-deploy-and-storage.md#deploy-02) | R2 列举会静默截断，而护栏只拦得住「完全空」 | 上传、存储与发布 | medium | resolved |
| [DEPLOY-03](./06-deploy-and-storage.md#deploy-03) | 桶名/前缀写错时，R2 的表现和「真的没有对象」完全一样 | 上传、存储与发布 | high | resolved |
| [DEPLOY-04](./06-deploy-and-storage.md#deploy-04) | 每个脚本各拉一次全量稿件列表，触发 B 站 412 让整条上传链路停摆 | 上传、存储与发布 | medium | resolved |
| [DEPLOY-05](./06-deploy-and-storage.md#deploy-05) | 平台「拒绝回答」被读成「平台说没有」 | 上传、存储与发布 | high | resolved |
| [DEPLOY-06](./06-deploy-and-storage.md#deploy-06) | 哈希证明与删除之间没有二次校验：删错文件的唯一真实窗口 | 上传、存储与发布 | high | open |
| [MEDIA-01](./05-media-pipeline.md#media-01) | 播放器渲染在合成 GPU 表面上，`PrintWindow` 和 `BitBlt` 都读不到 | 音视频解码与抓取 | medium | resolved |
| [MEDIA-02](./05-media-pipeline.md#media-02) | `BitBlt` 抓到的是**遮住**播放器的那层窗口 | 音视频解码与抓取 | medium | resolved |
| [MEDIA-03](./05-media-pipeline.md#media-03) | 注入解码器的实验被「窗口不在屏幕上」污染，明确不许从中记结论 | 音视频解码与抓取 | high | open |
| [MEDIA-04](./05-media-pipeline.md#media-04) | 播放上下文会被回收，工具只能看到「活着的窗口」 | 音视频解码与抓取 | high | open |
| [MEDIA-05](./05-media-pipeline.md#media-05) | 八个独立解码器实现表现一致，证伪「解码器版本」假设 | 音视频解码与抓取 | medium | resolved |
| [MEDIA-06](./05-media-pipeline.md#media-06) | 要播放器解密，就得给它**真实的前台输入** | 音视频解码与抓取 | medium | resolved |
| [TOOL-01](./08-local-tooling.md#tool-01) | `tail` 吞掉退出码，于是构建失败看起来像成功 | 本地工具链 | high | resolved |
| [TOOL-02](./08-local-tooling.md#tool-02) | PowerShell 5.1 按 ANSI 读 `.ps1`，中文变成了 `灏侀溃` 目录名 | 本地工具链 | medium | resolved |
| [TOOL-03](./08-local-tooling.md#tool-03) | `-Key a,b` 的逗号被拆坏：匹配不到任何卡片，却报告成功 | 本地工具链 | medium | resolved |
| [TOOL-04](./08-local-tooling.md#tool-04) | Codex CLI 只认 `HTTP(S)_PROXY`，忽略 Windows 系统代理 | 本地工具链 | medium | resolved |
| [TOOL-05](./08-local-tooling.md#tool-05) | 播放器每次启动都换 PID，而「两个 EVPlayer2 进程都在解码」是错的 | 本地工具链 | medium | resolved |
| [TOOL-06](./08-local-tooling.md#tool-06) | 磁盘写满，而报错出现在一个不相关的写文件上 | 本地工具链 | medium | resolved |
| [TOOL-07](./08-local-tooling.md#tool-07) | 用 PowerShell 批量改 UTF-8 源码：13 个文件的中文注释全毁，且**自动回滚不可靠** | 本地工具链 | high | resolved |
| [TOOL-08](./08-local-tooling.md#tool-08) | 本地工具链的四种静默少报：管道、解码器、反汇编器、解释器 | 本地工具链 | high | resolved |
| [TOOL-09](./08-local-tooling.md#tool-09) | 并发 + 重试的流水线，必查四件事 | 本地工具链 | high | resolved |
| [TOOL-10](./08-local-tooling.md#tool-10) | 配置文件写着「开启」，注册表里其实是关的 | 本地工具链 | medium | resolved |
| [VER-01](./01-verification.md#ver-01) | 探针的读取长度不满足所有对齐要求，于是「全部 0 命中」 | 验证方法与证据强度 | high | resolved |
| [VER-02](./01-verification.md#ver-02) | 一个本身坏掉的测试，删掉了一条真正在工作的代码路径 | 验证方法与证据强度 | high | resolved |
| [VER-03](./01-verification.md#ver-03) | 仪器的静默被读成「目标没有发生」——三个独立机制都产生同一种静默 | 验证方法与证据强度 | high | resolved |
| [VER-04](./01-verification.md#ver-04) | 探针的命中上限与去重，把热指令伪装成「只命中一次」 | 验证方法与证据强度 | medium | resolved |
| [VER-05](./01-verification.md#ver-05) | 两个仪器同时挂在同一个进程上，观测互相污染 | 验证方法与证据强度 | medium | resolved |
| [VER-06](./01-verification.md#ver-06) | 看门狗把「本地下载齐」当成「已上传」，宣布完成后仍缺 126 集 | 验证方法与证据强度 | high | resolved |
| [VER-07](./01-verification.md#ver-07) | 用「集号 Jaccard」配对课程，把已上传的视频大量误报成未上传 | 验证方法与证据强度 | high | resolved |
| [VER-08](./01-verification.md#ver-08) | 把 `exit 0` 当成功：空匹配、漏传、过期缓存都报「通过」 | 验证方法与证据强度 | medium | resolved |
| [VER-09](./01-verification.md#ver-09) | 「静默 N 分钟 = 导出完成」不成立，流水线会回头补号 | 验证方法与证据强度 | high | resolved |
| [VER-10](./01-verification.md#ver-10) | 审计报告把「计划」写成「实际」：删失败的文件也报「已删除」 | 验证方法与证据强度 | medium | resolved |
| [VER-11](./01-verification.md#ver-11) | PE 文件的**偏移**不是它的 **RVA** | 验证方法与证据强度 | medium | resolved |
| [VER-12](./01-verification.md#ver-12) | 那份「不是证据」的清单，和「两个播放器进程都在解码」 | 验证方法与证据强度 | medium | resolved |
| [VER-13](./01-verification.md#ver-13) | 子代理的审查报告自带误报，结论必须回代码复现再动手 | 验证方法与证据强度 | medium | resolved |
| [VER-14](./01-verification.md#ver-14) | 自己设计的对照实验错了两次，两次都产出「干净」的假结论 | 验证方法与证据强度 | high | resolved |
| [VER-15](./01-verification.md#ver-15) | 命中数正好等于随机期望值，差点宣布攻破 | 验证方法与证据强度 | high | resolved |
| [VER-16](./01-verification.md#ver-16) | 坏仪器凭空造出「确实存在变换」的证据 | 验证方法与证据强度 | high | resolved |

## Statistics

- Total: **64**
- Batches: `2026-10`
- Severity: high 39 · medium 24 · low 1
- Status: resolved 60 · open 4
- With archived evidence: 61
<!-- END:auto-index -->

## 追加一个批次

1. 跑 `find-agent-sessions.mjs scan --project .`，与 `~/.dsh/storages/workspace.json` 的 `sessionIds` **对账**。
2. `export --full` 导出，然后按主题把坑写进对应主题文件，沿用 `### <ID> · <标题>` + 元数据行 + 七个小节。
3. 需要长上下文的，`pack-evidence.mjs add --id <ID> --segment <会话.md>:<起>-<止>`。
4. `node scripts/build-guide-index.mjs --guide docs/avoiding-pitfalls-guide` 重建上面的索引。
5. `pack-evidence.mjs check` 与 `build-guide-index.mjs --check` 都干净后再提交。
