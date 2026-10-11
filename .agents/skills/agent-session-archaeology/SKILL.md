---
name: agent-session-archaeology
description: Find, verify, and mine every local coding-agent transcript that belongs to one project — DSH, Claude Code, Codex, VS Code Chat (Copilot/Cursor/Windsurf/Trae) — and export them as readable Markdown. Portable across Windows, macOS and Linux, for any project, with zero dependencies. Use when the user asks to find all past agent conversations about this repository, to recover how an earlier problem was solved, to audit what agents changed, or to summarize lessons from previous sessions. Handles multi-frame zstd, per-tool path-slug encodings, and authoritative session registries.
---

# Agent Session Archaeology

Round up the agent conversations scattered across a machine and mine them for experience. The hard part is not reading files — it is deciding **whether a session belongs to this project at all**. Directory names lie.

## Ground rules

1. **Decide ownership from the transcript, never from the directory name.** One project path can appear under several directory names: `C:\work\demo` becomes `C--work-demo` under Claude Code and `--C-work-demo--` under DSH. The encodings differ per tool and neither is reversible. The only reliable signals are the `cwd` recorded inside the session and the tool's own registry.
2. **Never trust a single decompress pass to give you everything.** DSH session files are multi-frame zstd; decompressing one shot **silently** returns only the first frame (a single metadata line) with no error. See [references/transcript-formats.md](references/transcript-formats.md).
3. **Reconcile against the authoritative registry before concluding.** If the session count does not match the tool's own registry, you missed something.
4. **An export is verbatim, so it inherits whatever was pasted into the conversation** — session cookies, bearer tokens, API keys. `export` redacts credential values as it writes (see [scripts/redact-exports.mjs](scripts/redact-exports.mjs)); for an export directory produced another way, or as a pre-commit guard, run that script directly. It replaces values and keeps key names, so the transcript still reads as evidence.

## Environment and installation

- **Portable**: Windows, macOS, Linux. Application-data directories are probed per platform convention (`%APPDATA%`, `~/Library/Application Support`, `$XDG_CONFIG_HOME`, `~/.config`); a missing source is skipped without affecting the others.
- **Zero dependencies**, Node only. Node ≥ 23.8 is required to decode DSH's zstd sessions; older runtimes simply skip the `dsh` source and keep working.
- **Any project**: pass `--project <dir>`, defaulting to the current working directory.

To install, copy the whole directory into any client's skills folder (standard [agentskills.io](https://agentskills.io/specification) layout):

```bash
cp -r agent-session-archaeology <project>/.agents/skills/   # one project
cp -r agent-session-archaeology ~/.agents/skills/           # every project for this user
cp -r agent-session-archaeology ~/.claude/skills/           # Claude Code, user level
```

## Workflow

`$SKILL` below is wherever this skill is installed — project-local (`.agents/skills/…`) or user-global (`~/.agents/skills/…`, `~/.claude/skills/…`):

```bash
SKILL=.agents/skills/agent-session-archaeology   # or ~/.agents/skills/agent-session-archaeology

# 1. Probe first: which stores exist, which are missing, what Node can do
node "$SKILL/scripts/find-agent-sessions.mjs" doctor

# 2. List sessions belonging to this project (source, time, message count, why it matched)
node "$SKILL/scripts/find-agent-sessions.mjs" scan

# 3. Fallback: also catch sessions whose cwd is elsewhere but whose text names this project
node "$SKILL/scripts/find-agent-sessions.mjs" scan --content

# 4. Export readable Markdown (defaults to <project>/.agent-sessions/)
node "$SKILL/scripts/find-agent-sessions.mjs" export          # conversation text
node "$SKILL/scripts/find-agent-sessions.mjs" export --full   # include tool calls and results

# 5. Credentials are stripped as part of export. Verify, or clean an older export directory:
node "$SKILL/scripts/redact-exports.mjs" --check              # exit 1 if anything remains
node "$SKILL/scripts/redact-exports.mjs"                      # redact in place
```

Options: `--project <dir>`, `--json` (JSON scan output), `--out <dir>` (export destination).

The `match` column in `scan` means: `cwd` = the recorded working directory is inside this project; `registry` = the tool's own registry names it; `content` = the text mentions this project's absolute path.

After export, `index.md` is the overview and `<source>--<id>.md` is one session. **Exports contain local paths and personal context — always gitignore them** (`.agent-sessions/`), and keep the ignore rule even though values are redacted: the redactor removes credentials, not the personal context around them.

## Supported sources

| Tool | Location | Format | Ownership signal |
| --- | --- | --- | --- |
| DSH | `~/.dsh/sessions/<slug>/<session>/session.v<N>.jsonl.zstd` | **multi-frame** zstd over JSONL | `cwd` on the first line |
| DSH (authoritative) | `~/.dsh/storages/workspace.json` | JSON | `tables.workspaces[*].sessionIds` |
| Claude Code | `~/.claude/projects/<slug>/<uuid>.jsonl` | JSONL | `cwd` on every message record |
| Claude Code | `~/.claude/history.jsonl` | JSONL | prompt history only, not a transcript |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | JSONL | `session_meta.payload.cwd` on line 1 |
| VS Code family | `<appData>/<product>/User/workspaceStorage/<hash>/` | patch stream | `workspace.json` folder URI |

`<product>` covers `Code`, `Code - Insiders`, `VSCodium`, `Cursor`, `Windsurf` and `Trae`, which share one layout.

Field-level detail and the slug-encoding table live in [references/transcript-formats.md](references/transcript-formats.md).

## Extending: adding a tool

1. Write a reader with the signature `() => session[]`, reusing the script's `makeSession` / `stamp` / `parseJsonl` / `flattenBlocks` / `safeRead`. Every session needs a `cwd`, or some other provable ownership signal.
2. Add one entry to the `SOURCES` array: `{ id, read, available, probe }`. `probe()` returns the paths the reader actually opens; `doctor` reports on them.
3. Run `doctor` to confirm the probe, then `scan` to confirm the parse.

**Never guess a format from memory.** Find a file the tool actually wrote before writing the parser — invented schemas are the single biggest source of bugs in this kind of script.

## Pitfalls (all hit in practice)

1. **Multi-frame zstd truncates silently** — `zstdDecompressSync(wholeFile)` returns only the first frame and throws nothing; the streaming API behaves the same. Split on the frame magic `28 B5 2F FD` and decode frame by frame. An 8 MB session held 5582 frames.
2. **Directory slugs are not reversible** — `:` becomes `-` under Claude but is dropped under DSH. Decoding a slug back to a project path is a dead end.
3. **Several format versions coexist** — when `session.v3` and `session.v4` both exist, take the highest version or the session is counted twice.
4. **Session ids come with and without a prefix** — `abc` and `session-abc` both appear in directory names and registries; strip the prefix before comparing.
5. **An empty session is not a session** — VS Code family `chatSessions/*.jsonl` often holds metadata only. Filter on "message count > 0".
6. **A backslash needle never matches** — searching transcript text with a normalized forward-slash path always misses; try both separators and lowercase.
7. **`cwd` may be a subdirectory** — a session started inside a subdirectory records that subdirectory, so match by prefix rather than equality.
8. **Files may be unreadable** — not every machine has every tool installed. Guard every read; one missing `history.jsonl` must not take down the whole scan.

## Verification (do this)

- **Reconcile with the authoritative registry**: the number of `sessionIds` in `~/.dsh/storages/workspace.json` for this project should equal the number of DSH sessions found.
- **Sample in reverse**: pick a session that did *not* match and confirm its `cwd` really is elsewhere.
- **Run it on another project**: `--project` pointed at a different repository should give sensible results (including sessions from subdirectories), proving the logic is not tuned to one repo.
- **Build a fake HOME**: point `HOME`/`USERPROFILE` at a temporary directory, lay out macOS/Linux directory conventions, and run `scan` — this validates cross-platform path handling on a single machine.

## Mining the transcripts

- Exports run to several MB — **do not read them whole**. Read the first ~150 lines for context, then grep for signal words (`root cause|actually|misjudged|failed|rollback|turned out|I was wrong`), and read around the hits with offset/limit.
- Separate three kinds: product bugs, **the agent's own mistakes** (misjudgement, wrong verification method, premature success claims), and local tooling traps. The second kind is usually the most valuable.
- Cite the session id and timestamp with every conclusion so it can be checked against the original.

## Turning findings into a pitfalls guide

Findings default to `docs/avoiding-pitfalls-guide/` (use `--guide` to point elsewhere):

```text
docs/avoiding-pitfalls-guide/
├── README.md            overview + auto-generated index (do not hand-edit the marked region)
├── 01-verification.md   ← 8 fixed topics, which do NOT grow with each batch
├── 02-data-contracts.md
├── …
└── evidence.gz       ONE archive holding every trimmed excerpt
```

**One archive, not one file per pitfall.** Dozens of small `.gz` files are harder to manage, harder to browse, and compress worse than a single one (every file pays its own header). At a few tens of KB total, one archive is the right shape; `show --id` still prints a single section without unpacking anything.

**Classify by topic, not by date or by tool.** Readers look for "how do I avoid this class of problem", not "which day or which tool said it". Keep the batch as per-pitfall metadata: the topic set stays fixed, the index does not bloat, and appending a new batch never reshuffles the directory.

Each pitfall has a heading `### <ID> · <one-line title>`, a metadata line (`` `batch` `` / `` `severity` `` / `` `status` `` / `` `source` ``), then seven fixed sections: **where it bit / how it surfaced / how it was diagnosed / what was proposed / how it was resolved / impact / evidence / lesson**.

ID prefixes are per topic: `VER-` `DATA-` `DB-` `AUTH-` `MEDIA-` `DEPLOY-` `AGENT-` `TOOL-`.

**Language**: everything in this skill is English. The guide *body* may be written in the project's own language, but the metadata labels stay English because they are machine-read keys rather than prose. `build-guide-index.mjs` resolves them through a small alias table — add your language's spellings there if you want localized labels.

### Evidence must be trimmed per pitfall

Users append in batches over time, and one session is megabytes — archiving whole sessions will bloat the repository within a few batches.

- Short pitfall, the inline excerpt is enough → **inline only**, no 📎 line.
- Needs long context to be understood → inline summary + archive **only the contiguous line range relevant to that pitfall** (typically 20–80 lines), and add a line like ``- 📎 excerpt: [evidence.gz](../evidence.gz) → ID`` to the body.

```bash
SKILL=.agents/skills/agent-session-archaeology   # wherever the skill is installed

node "$SKILL/scripts/pack-evidence.mjs" add    --guide docs/avoiding-pitfalls-guide \
  --id VER-02 --title "cross-machine clock skew" \
  --segment <exported-session.md>:7210-7320 \
  --segment <another-session.md>:640-688        # repeatable; multi-range and cross-session are fine
node "$SKILL/scripts/pack-evidence.mjs" show   --guide docs/avoiding-pitfalls-guide --id VER-02
node "$SKILL/scripts/pack-evidence.mjs" list   --guide docs/avoiding-pitfalls-guide
node "$SKILL/scripts/pack-evidence.mjs" remove --guide docs/avoiding-pitfalls-guide --id VER-02
node "$SKILL/scripts/pack-evidence.mjs" unpack --guide docs/avoiding-pitfalls-guide   # readable copy, outside the repo
node "$SKILL/scripts/pack-evidence.mjs" check  --guide docs/avoiding-pitfalls-guide   # size audit; exit 2 over budget
node "$SKILL/scripts/build-guide-index.mjs"    --guide docs/avoiding-pitfalls-guide   # rebuild the README index
```

Two guards exist because both mistakes were made for real:

- **Oversized excerpt** — warns above 512 KB uncompressed. A wrong `--lines` once made a "36 line" archive hold 1.2 MB of the entire session while still reporting 36 lines.
- **Personal paths** — warns when an excerpt contains `C:\Users\<name>` or `/Users/<name>`. The archive travels with the repository, so such a path is a dead reference for everyone else and a privacy leak for the author, while the exported session directory it points at is gitignored and not present in any clone. Record the **session name and line range** instead; rewrite any incidental path in the excerpt as `~/…`.

## Boundaries

- Formats change between tool versions; `doctor` reports whether a location exists, not whether the format still matches. When parsing fails, fall back to grepping the raw session files instead of declaring there were no conversations.
- Session files can be deleted by the tools' own cleanup. **Export as soon as you find them** rather than relying on them being there later.
- `doctor` lists known-but-unsupported tool directories. If one exists, there are sessions outside this script's reach — add an adapter using the steps above.
