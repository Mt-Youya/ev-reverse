# 04 · 凭证、令牌与权限

> 短期凭证决定了「什么时候必须抓、抓不到就没有第二次」。

这个项目只有一类凭证：播放器运行时才有的一枚短期 bearer token 加课程动态密钥。
它不可推导、不可离线补算，因此围绕它的每一个决定都会变成流程约束。

这里的坑不多，但每一条都同时是**安全约束**——所以下面同时记了「哪条是用户明令的」。

---

<a id="auth-01"></a>
### AUTH-01 · 含会话令牌的文件不得提交（用户明令，写进 `.gitignore` 的理由）

`batch` 2026-10 · `severity` high · `status` resolved · `source` claude-code--1b8d953d:2343

**在哪里咬人**：`tools/parser-tools/captured/` 下的抓包产物——
里面是完整的请求与响应，含 JWT、`tk`、`sign`。

**怎么暴露的**：这不是踩坑，而是用户在会话中途明确下达的**长期约束**，原话被逐字记录：

> 根据 .gitignore 排除 captured/ 的理由，包含会话令牌（JWT/tk/sign）的文件不得提交

**怎么诊断的**：`.gitignore` 里那几条规则的解释本身就是理由书：

```
# capture_all.py output (contains session tokens, JWT, tk, sign - do not commit)
tools/parser-tools/captured/
tools/parser-tools/ev2_out/
tools/parser-tools/urls.log
tools/parser-tools/ctx_dump.json
tools/parser-tools/triple.json
```

**当时提了什么**：把这些目录排除在版本控制之外，并在规则上方写明**为什么**。

**最后怎么解决**：规则带着理由留在 `.gitignore` 里；后续新增的同类产物
（如 `dump_contexts.json`——一份内存转储，「不是我们分发的东西」）继续沿用同一形状。

**影响面**：仓库会公开。令牌泄漏是**不可撤回**的：改写历史也清不掉已经被克隆的副本。
这也是为什么 `.gitignore` 在这个仓库里是一条条带理由的，而不是一串模式。

**证据**：用户原话与 `.gitignore` 对应条目见存档。

**经验**：`.gitignore` 的注释在这个仓库里是**规则的一部分**（「一条规则只在它的产出方还会写出
不该提交的东西时才有资格留在这里」）。新增排除规则时照写理由；
反过来，删掉一个产出脚本时，要把它的规则一起删掉。判断新产物能不能提交，
只看一条：**它里面有没有会话级凭证**。

- 📎 excerpt: [evidence.gz](evidence.gz) → AUTH-01

---

<a id="auth-02"></a>
### AUTH-02 · 把「密钥可以离线推导」误读成「什么都不需要播放器」

`batch` 2026-10 · `severity` medium · `status` resolved · `source` dsh--session-79bdf5ca:19456

**在哪里咬人**：导出流水线的准入判断——什么时候必须去嗅探运行中的播放器。

**怎么暴露的**：技能推导完成之后（`key = MD5_hex(tk + filename + "20220507")`），
「不需要播放器」的结论容易一路滑到「凭证也不需要播放器」，
设计出的流程会在真正运行时卡在第一步。

**怎么诊断的**：把两个东西分开命名之后才清楚：

- **密钥**：`MD5_hex(tk + 文件名 + "20220507")`，输入全部来自 API，**可以完全离线推导**；
- **凭证**：短期 bearer token + 清单的动态密钥，**只在播放器运行时存在**，没有任何离线来源。

**当时提了什么**：把「需要播放器」的范围**收窄到凭证这一件事**，
并用一个按钮触发的 Frida 探针（`tools/parser-tools/probe_catalog_constants.py`）把它取出来。

**最后怎么解决**：写进 [`CONTEXT.md`](../../CONTEXT.md)：

> **What still needs the player** is only the *credential*: a short-lived bearer token plus the
> catalog's dynamic keys.

**影响面**：如果按「什么都不需要播放器」去设计，整个流水线会在用户那里失败一次，
而失败点（取不到 token）与原因（以为令牌是离线可得的）相距很远。

**证据**：这段结论所在的交接总结见存档。

**经验**：给「可离线」划边界时，把**推导所需的输入**和**获取输入的权限**分开写。
「算法是离线的」不代表「数据是离线的」——本项目里前者成立、后者不成立，
两句话必须同时写在文档里，否则读者只会记住比较顺耳的那一句。

- 📎 excerpt: [evidence.gz](evidence.gz) → AUTH-02

---

<a id="auth-03"></a>
### AUTH-03 · 善后动作把真实密钥和运行产物又带回仓库

`batch` 2026-10 · `severity` high · `status` resolved · `source` claude-code--bc2b4335:115

**在哪里咬人**：工单 05 的第一版——**记录「删除密钥」这件事**的那份文档本身。

**怎么暴露的**：它把**六个真实密钥值逐字写进了自己的脱敏表**，
而 `.scratch/` 是**被跟踪**的目录。于是「清理动作」重新引入了要清理的东西。
由 Spec review 代理发现。

**怎么诊断的**：判据不是肉眼检查，而是 `git grep --cached` ——
它看的是**暂存区**，也就是「如果现在提交，会进去什么」。

**当时提了什么 / 怎么解决**：改为**只写位置描述、不写值**，
并用 `git grep --cached` 验证。同一轮还发现第二处：`git add -A`
把 `verify_out/`（11 MB）收进了提交，最后一次提交又把 `verify_out3/` 收进去——
而这条线索**差点被漏掉**。

**影响面**：与 AUTH-01 同族但更隐蔽：AUTH-01 是「产物含令牌，所以不提交」，
这条是「**关于令牌的文档**含令牌」。后者的危险在于写它的人正在做安全的事，
因此不会怀疑自己。

**经验**：敏感数据的「记录/清理」本身要按**写入操作**审查。
三条可执行的：只写**位置**不写**值**；用 `git grep --cached` 而不是肉眼；
`git add -A` 在会产生运行产物的仓库里**默认有害**（用显式路径或先看 `git status`）。

- 📎 excerpt: [evidence.gz](evidence.gz) → AUTH-03
