# 06 · 上传、存储与发布

> 远端存储的失败方式和本地相反：它不会报错，它返回空。

桶名写错、前缀写错、缓存过期，在 S3/R2 上表现得和「真的没有这个对象」一模一样；
B 站侧的「没有这个稿件」也可能是本地匹配规则错了。本节记的是**如何不让一个静默的空回答变成删除决定**。

贯穿本节的一条原则：**远端回答说「没有」时，先证明你问对了地方**——
问对地方的证据是「另一次已知必中的查询在同一路径上成功」。

---

<a id="deploy-01"></a>
### DEPLOY-01 · R2 的 `CopyObject` 会改写 ETag，ETag 不能当拷贝/回滚的等价判据

`batch` 2026-10 · `severity` high · `status` resolved · `source` dsh--session-cfbf09e4:5199

**在哪里咬人**：R2 目录迁移脚本与它的回滚守卫——用「源对象与目标对象的 ETag 相等」证明拷贝完成。

**怎么暴露的**：cesium 那次迁移把 **4 个视频判为「验证不通过」并拒绝删除源文件**。
把两边 `head_object` 的关注字段打印出来才看清原因。

**怎么诊断的**：

| | ETag | 形态 | 分片数 |
| --- | --- | --- | --- |
| 源 | `f5a949ae…-14` | multipart | 14 |
| 目标 | `39dc6ebe…` | 单 PUT（内容 MD5） | — |

两边的 `len` 完全一致，只有 ETag 不同。机制是：**R2 的 `CopyObject` 会把 multipart 对象重写成单 PUT**，
所以内容不变而 ETag 必然改变。

**当时提了什么**：回滚守卫改用 `name + size`，放弃 ETag 相等。

**最后怎么解决**：换判据后复核 4 个真实对象都存在，删除目标副本、重建源目录标记，28 个视频完好。
特别注意作者对这次「误判」的评价：**它恰好是 fail-safe，等于这个错误判据救了场**。

**影响面**：所有跨 R2 的拷贝、迁移与回滚。最阴的一点是它在早先的 gsap 迁移上
（88.27 MB 单 PUT 对象，ETag 无 `-N` 后缀）**看起来完全正确**，所以判据被信任了——
直到遇到 multipart 对象才暴露。反向使用（用「ETag 不同」证明「这是另一个对象，可以覆盖/删除」）
会直接导致误删。

**证据**：两边 ETag 与分片数的对照表见存档。

**经验**：ETag **不是内容指纹**：multipart 上传带 `-N` 后缀，而 `CopyObject` 会把它变成单 PUT 的 MD5。
跨对象的等价性只比 `name + size`；需要内容证明就自己算哈希。
判据在一个样本上「看起来对」不构成证据——**要问它在什么输入下会变性**。

- 📎 excerpt: [evidence.gz](evidence.gz) → DEPLOY-01

---

<a id="deploy-02"></a>
### DEPLOY-02 · R2 列举会静默截断，而护栏只拦得住「完全空」

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-d7528d99:3960

**在哪里咬人**：`backends.py` 的 R2 `list_objects` 分页，以及技能文档承诺的
「bucket 列不出东西就拒绝运行」这道护栏。

**怎么暴露的**：安全审查指出翻页判据用的是**输出文本的精确匹配**。

**怎么诊断的**：实测 `IsTruncated` 只认严格等于 `"true"`：

- `"1"` 和 `"true\n"`（带空白）都会让 `list_objects` **直接返回第一页且没有任何警告**；
- 同一段代码在 `Contents` 缺 `ETag` 元素时抛
  `AttributeError: 'NoneType' object has no attribute 'text'`（已实测），整体崩溃而不是跳过。

R2 当前确实回小写 `true`，所以这是**可复现的脆弱性而不是当前故障**——
这句话本身值得记：一个「现在没坏」的缺陷仍然要被修，因为它只在数据量或实现措辞变化时才发作。

**当时提了什么**：去掉严格相等判断（按真值判定、忽略空白），ETag 缺失时跳过；
并在文档里补一条——整批读作 `R2 has no object with this name` 时，
**先怀疑 `r2.prefix` 再怀疑上传**。

**最后怎么解决**：列入审查后修复清单并修复（「R2 分页不再可能静默截断」）。

**影响面**：桶内对象超过单页 1000 条时，第 1000 条之后的对象变成「不存在」——
单项判据方向安全（keep），但**批量清理会静默漏读**。
而护栏本身只覆盖「列出空」这一种配置错误：「列出来的是别的东西」照样像一次成功运行。

**证据**：实测的三种返回值与 `AttributeError` 见存档。

**经验**：协议判据不能拿**返回文本的精确匹配**当依据（`"true"` / `"True"` / `"1"` / 带空白）。
分页与空结果的护栏必须区分三种情况：**真的没有**、**没读全**、**读错了前缀**。
只防住「空」的护栏，防不住「读错地方」。

- 📎 excerpt: [evidence.gz](evidence.gz) → DEPLOY-02

---

<a id="deploy-03"></a>
### DEPLOY-03 · 桶名/前缀写错时，R2 的表现和「真的没有对象」完全一样

`batch` 2026-10 · `severity` high · `status` resolved · `source` dsh--91b63c57:1628

**在哪里咬人**：purge / 清理类工具的第一道检查——列一遍 R2。

**怎么暴露的**：这不是事故，而是**测试里刻意跑出来的一条路径**。
在 `empty-root` 场景下工具打印：

```
R2          : listing s3://stub-bucket/videos/ ...
SystemExit: R2 returned no object under s3://stub-bucket/videos/; refusing to run
            (a wrong bucket or prefix would look like 'R2 has nothing')
```

**怎么诊断的**：这条设计判断是整个工具里最重要的一个：
「R2 里什么都没有」与「我问错了桶/前缀」在 API 层面**不可区分**，
而两者的正确反应完全相反——前者可能意味着可以清理，后者必须停下。

**当时提了什么**：把不可区分性写进**报错信息本身**，让运行者当场看到这个歧义；
并选择**拒绝运行**而不是继续。

**最后怎么解决**：`--apply` 之前的干跑与空根场景都会触发这道闸；同一批场景测试里
`root-is-file` 报「roots that do not exist」、`vanishing-video` 正常列到 1 个对象。

**影响面**：任何以「远端没有」为前提的删除动作。这是本仓库里少见的**主动设闸**的成功案例，
值得作为其他清理工具的模板。

**证据**：`empty-root` 场景的完整输出见存档。

**经验**：当两种完全不同的原因会产生**同一个观测结果**时，不要在代码里猜，
把它变成一条**带歧义说明的硬失败**。「列不到东西就拒绝运行」比「列不到东西就当作没有」多花的
只是一次人工确认，省下的是一次不可逆的删除。

- 📎 excerpt: [evidence.gz](evidence.gz) → DEPLOY-03

---

<a id="deploy-04"></a>
### DEPLOY-04 · 每个脚本各拉一次全量稿件列表，触发 B 站 412 让整条上传链路停摆

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-a48ac823:43292

**在哪里咬人**：`biliup list --max-pages 60` 对创作者稿件列表接口的调用；
稿件 id 全部来自这一次列举，而 id 是上传器的输入。

**怎么暴露的**：核验过程中 `biliup list` 突然报 `Unknown Error / error decoding response body`。

**怎么诊断的**：用 cookies 直接打那个接口得到 **HTTP 412 Precondition Failed**（B 站风控），
而同期 `biliup show` 完全正常——所以**「show 能用」不代表会话没问题，风控只打在列表端点**。
原因是 `--max-pages 60` 在 493 个稿件下要连打几十页，而一个小时里三轮 pass
加好几个核验脚本各跑一次列表，短时密集调用直接被挡。
412 一来，上传器的稿件 id 来源断掉，`bili_upload` 直接拒绝运行。

**当时提了什么**：稿件 id 早已记录在 `clean_work.BILI_BY_COLLECTION`——
**已知 id 的合集改用 `biliup show` 只读其分 P，只有无 id 的合集才列举**；
列举统一走 `tools/bili_listing.py`（一次取、缓存 10 分钟、取不到用旧缓存并提示、连缓存都没有就报错拒绝运行）。

**最后怎么解决**：已修并实测——列表接口 412 期间干跑 20 秒完成，
一个「追加 3 个分 P」的 pass 从 50 次请求降到 2 次。
顺带修好了一个**从未生效过**的兜底：它传 `-1` 表示「不限年龄」，而读取函数遇到负数直接返回 `None`，
于是这个本该在 412 时救场的分支从来没生效过（改用 `None`）。

**影响面**：整条上传流水线停摆。更普遍的教训是架构性的：
**同一接口被多个脚本各自重复拉取**，在有限流的服务上是必炸的。

**证据**：412 现场、「50 次请求降到 2 次」以及那个从未生效的兜底见存档。

**经验**：外部接口的限流是**架构约束**，加重试解决不了。
已被记录在案的稳定标识（稿件 id）应当**替代**每次重新列举；
列举要收敛到一个有缓存、有明确兜底语义的入口。另外，
**兜底分支必须被真的跑过一次**——一个从没执行过的 `catch` 与没有它没有区别。

- 📎 excerpt: [evidence.gz](evidence.gz) → DEPLOY-04

---

<a id="deploy-05"></a>
### DEPLOY-05 · 平台「拒绝回答」被读成「平台说没有」

`batch` 2026-10 · `severity` high · `status` resolved · `source` dsh--91b63c57:547

**在哪里咬人**：删除安全工具的双平台举证链——只有「B 站确实持有」才能给出 `decision="delete"`。

**怎么暴露的**：审计对象**自己的代码注释**就记着那一天：

```python
# Bilibili answers 412 (risk control) or -702 (too many requests) when it is asked too
# often. Both mean "no answer", never "nothing there"; docs/BILIBILI-UPLOAD.md records
# the day a verification script read an empty listing as an empty account.
HTTP_RISK = {412, 429, 503}
```

那次「空清单」曾被当成「账号里什么都没有」。

**怎么诊断的**：把「平台返回码 / 传输失败」与「查询结果为空」拆成**两个语义域**：
新增 `BackendError`，文档串写死为

```python
class BackendError(RuntimeError):
    """A back end could not answer. Never means "the answer is no"."""
```

**当时提了什么 / 怎么解决**：把「没有答案」一律当**致命错误中止**，
绝不落成「未上传」这个安全但错误的事实；`HTTP_RISK = {412, 429, 503}` 触发重试。
审计逐分支走完 `main()` 确认：`decision="delete"` 只在 B 站命中为真时赋值一次，
两条「平台没有答案」的路径都是 `raise SystemExit`。

**影响面**：反了就会删掉本地唯一副本。**这一条正是整个删除工具的安全地基**，
也是 DEPLOY-03（R2 侧）的同族：**「问不到」与「问到了，是空」必须走两条代码路径。**

**证据**：代码注释、`BackendError` 定义与 `HTTP_RISK` 集合见存档。

**经验**：把「没得到答案」显式建模成**异常**，而不是一个空结果值。
空结果会顺着类型系统一路流下去，被下游当成事实；
异常则强制调用点做出决定。设计上多花一个类，安全上少一个类别的事故。

- 📎 excerpt: [evidence.gz](evidence.gz) → DEPLOY-05

---

<a id="deploy-06"></a>
### DEPLOY-06 · 哈希证明与删除之间没有二次校验：删错文件的唯一真实窗口

`batch` 2026-10 · `severity` high · `status` open · `source` dsh--91b63c57:2062

**在哪里咬人**：`purge_uploaded.py` 里产出 `file_digests` 的那一步与 `unlink`/`rmtree` 之间。

**怎么暴露的**：审计用 stub 掉两个平台后实测——
在 `file_digests` 返回**之后**，把已被证明的 **144 B 文件替换成 5 MB 新内容**，
`--apply` **照样把它删掉**，回收字节仍按旧的 144 B 上报。工作区同理：目录被换掉就删错整棵树。

**怎么诊断的**：R2 证明的是「**哈希那一刻**磁盘上那份字节」，
而 `unlink`/`rmtree` 之前不再核对 size/mtime/inode。
`--min-age-minutes`（默认 60）基于**扫描时刻**的 mtime，
提供的是**时序保护而不是内容校验**，给不了这道保证。
派生场景很现实：68 GB 的哈希阶段可能持续数十分钟，
而仓库里本身就有 `tools/watch_and_upload.py` 这类并发看守进程。

**当时提了什么**：删除前复核 `size`/`mtime`/`inode`（本次审计只报告，未给出实现）。

**最后怎么解决**：未解决——审计为只读，未改任何文件。本条状态记为 `open`。

**影响面**：这是**「删错文件的唯一真实窗口」**。批量删除几十 GB 时，窗口以十分钟计，
而且**证据链本身看起来是完整的**（确实哈希过、确实比对过）。

**证据**：144 B → 5 MB 的替换实测与「回收字节仍按旧的 144 B 上报」见存档。

**经验**：删除类工具必须在 `unlink` 之前**对同一 inode 重核 size 与 mtime**——
证明的时效只在哈希那一瞬。更一般地：
一个「先证明、后执行」的流程，中间的时间差就是攻击面；
要么缩短它（证明紧接着执行），要么在执行前重新验证。

- 📎 excerpt: [evidence.gz](evidence.gz) → DEPLOY-06
