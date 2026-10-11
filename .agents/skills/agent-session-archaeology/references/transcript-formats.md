# Transcript formats (field level)

SKILL.md covers *how to use* the tooling; this file covers *what the formats look like*, for when a parser needs writing or debugging.

## Contents

- [Platform path conventions](#platform-path-conventions)
- [Path slug encodings](#path-slug-encodings)
- [DSH](#dsh)
- [Claude Code](#claude-code)
- [Codex](#codex)
- [VS Code family](#vs-code-family)
- [Ownership resolution order](#ownership-resolution-order)

---

## Platform path conventions

Command-line tools keep sessions in `~/` dot-directories, identical on every platform. GUI clients move theirs around, so those locations **must be probed, never hard-coded**.

| Purpose | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Home | `%USERPROFILE%` | `/Users/<name>` | `/home/<name>` |
| App config/data | `%APPDATA%` (Roaming) | `~/Library/Application Support` | `$XDG_CONFIG_HOME` or `~/.config` |
| Local data | `%LOCALAPPDATA%` | `~/Library/Application Support` | `$XDG_DATA_HOME` or `~/.local/share` |
| CLI dot-dirs | `~/.claude`, `~/.codex`, `~/.dsh` | same | same |
| VS Code family | `%APPDATA%/Code/User/...` | `~/Library/Application Support/Code/User/...` | `~/.config/Code/User/...` |

The script prefers the current platform's convention but probes **all four**. WSL, containers and CI images routinely keep data somewhere other than the "native" location, and probing a missing directory is nearly free.

One more platform trap: on Windows, `path.resolve()` prepends the current drive letter to a POSIX-absolute path, turning `/home/x/proj` into `C:\home\x\proj`. Cross-platform paths (shared configs, WSL, transcripts copied from elsewhere) are already absolute and should be left as-is.

---

## Path slug encodings

For a given project path, each tool encodes a different directory name. The rules differ and **neither is reversible**:

| Tool | Input path | Encoded as | Rule |
| --- | --- | --- | --- |
| Claude Code | `C:\work\demo` | `C--work-demo` | `:` → `-`, `\` → `-` (no collapsing) |
| DSH | `C:\work\demo` | `--C-work-demo--` | `:` dropped, `\` → `-`, wrapped in `--` |
| Claude Code | `/home/dev/demo` | `-home-dev-demo` | `/` → `-` as well |

Because a path can itself contain `-`, decoding is ambiguous. **Never reconstruct a project path from a slug.**

---

## DSH

### Layout

```
~/.dsh/sessions/<slug>/<session-dir>/session.v<N>.jsonl.zstd
~/.dsh/storages/workspace.json          # authoritative registry
```

A session directory may hold both `session.v3` and `session.v4`. Measured: **v4 = v3 plus one `session/end-seed` record**, identical otherwise. Take the highest version.

### Multi-frame zstd (the easiest trap to fall into)

The file is **several independent zstd frames concatenated**, one written per append:

- `zlib.zstdDecompressSync(wholeFile)` returns only the first frame (208–283 bytes, the session header) and **throws nothing**
- `zlib.createZstdDecompress()` streaming behaves the same way
- Measured: a 103 KB file held 58 frames; an 8.3 MB file held 5582

Correct approach: scan for the frame magic `28 B5 2F FD`, split by offset, decompress each frame, then concatenate. If the magic occurs inside compressed data that frame's decode throws — skip it, since every occurrence was enumerated, no frame is lost.

### Event types

The first line is always `{"type":"session", "version":N, "id":"...", "cwd":"...", "createdAt":<ms>}`.

| `type` | Key fields | Use |
| --- | --- | --- |
| `session` | `id`, `cwd`, `createdAt`, `agentPreset` | **ownership signal**, session metadata |
| `user/message` | `data.content[]` (block array), `data.role` | user input |
| `assistant/message` | `data.message.content[]`, `data.turn`, `data.step` | assistant output (includes `reasoning` blocks) |
| `tool/call` | `data.name`, `data.arguments` (JSON string) | tool invocation |
| `tool/result` | `data.message.content[].text`, `data.toolCallId` | tool result |
| `session/title` | `data.title` | session title |
| `compaction/summary` | `data.summary` | context-compaction summary |
| `turn/start`, `step/start`, `step/end` | `data.turn`, `data.step` | turn boundaries |
| `session/end-seed` | — | new in v4, session end marker |

Timestamps: top-level `time` (ms epoch); the `session` line uses `createdAt`.

---

## Claude Code

### Layout

```
~/.claude/projects/<slug>/<uuid>.jsonl
~/.claude/history.jsonl                 # global prompt history, carries `project`
```

### Line types

| `type` | Notes |
| --- | --- |
| `user` / `assistant` | the conversation; `message.content` is a string or a block array, **every record carries `cwd`** |
| `mode`, `permission-mode` | session mode |
| `file-history-snapshot`, `file-history-delta` | file snapshots |
| `attachment` | attachments |
| `system` | system events |
| `ai-title` | session title |
| `cost-state`, `queue-operation`, `last-prompt`, `atis-latch` | metadata |

### Content block types

`text` (body), `tool_use` (`name`/`input`), `tool_result` (`content`), `thinking`.

Tool results appear as `tool_result` blocks inside **`user` records** — do not treat them as user input.

---

## Codex

### Layout

```
~/.codex/sessions/YYYY/MM/DD/rollout-<ISO time>-<uuid>.jsonl
```

### Line types

The first line is `type=session_meta`; `payload.cwd` is the **ownership signal** and `payload.id` is the session id.

| `type` | Notes |
| --- | --- |
| `session_meta` | first line: cwd, id, cli_version, base_instructions |
| `response_item` | `payload.type=message`, `payload.role` in developer/user/assistant, `content[]` of `input_text`/`output_text` |
| `event_msg` | `payload.type` such as `task_started`, `item_completed` (`item.type` = `UserMessage`/`AgentMessage`) |
| `turn_context`, `world_state`, `token_usage_record`, `compacted` | metadata |

Prefer `event_msg.item_completed` when extracting conversation — it yields one complete message per event. `response_item` splits a turn apart and carries a great deal of developer instruction.

---

## VS Code family

VS Code and its forks (Copilot Chat / Cursor / Windsurf / Trae / VSCodium) share one `workspaceStorage` layout, so a single adapter covers all of them.

### Layout

```
%APPDATA%/<product>/User/workspaceStorage/<hash>/workspace.json
%APPDATA%/<product>/User/workspaceStorage/<hash>/chatSessions/<uuid>.jsonl
```

`workspace.json`'s `folder` looks like `file:///c%3A/work/demo` and needs URL-decoding.

### Patch stream

Each line is one JSON object:

- `{"kind":0,"v":{...}}` — the initial full object (`v.requests[]` is the conversation)
- `{"kind":1,"k":[path...],"v":value}` — patch by path
- any other `kind` — treat as unknown and skip

**Many sessions have an empty `requests` array** — the file exists but holds no conversation. Always filter on "message count > 0".

---

## Ownership resolution order

1. **`cwd` recorded inside the session** (DSH first line / every Claude message / Codex `session_meta` / Copilot `workspace.json`) — highest authority, and match by **prefix**, not equality, because a session may have started in a subdirectory of it.
2. **The tool's own registry** (`~/.dsh/storages/workspace.json`, `tables.workspaces[*].sessionIds`) — for reconciliation and fallback.
3. **Content matching** — last resort only, and **both separators must be tried**: `norm()` produces forward slashes while transcripts quote backslashes.

Session ids appear with and without a `session-` prefix in both directory names and registries; strip it before comparing.
