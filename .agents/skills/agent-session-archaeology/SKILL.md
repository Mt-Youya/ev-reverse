---
name: agent-session-archaeology
description: Find, verify, and mine every local coding-agent transcript that belongs to one project — DSH, Claude Code, Codex, VS Code Chat (Copilot/Cursor/Windsurf/Trae) — and turn the lessons into a human-readable pitfalls guide. Portable across Windows, macOS and Linux, for any project, with zero dependencies. Use when the user asks to find all past agent conversations about this repository, to recover how an earlier problem was solved, to audit what agents changed, to record a pitfall discovered in the current conversation, or to incrementally update an existing avoiding-pitfalls guide. Handles multi-frame zstd, per-tool path-slug encodings, authoritative registries, deduplicated incremental updates, and output written in the user's own language.
---

# Agent Session Archaeology

Round up the agent conversations scattered across a machine and mine them for experience. The hard part is not reading files — it is deciding **whether a session belongs to this project at all**. Directory names lie.

## Ground rules

1. **Decide ownership from the transcript, never from the directory name.** One project path can appear under several directory names: `C:\work\demo` becomes `C--work-demo` under Claude Code and `--C-work-demo--` under DSH. The encodings differ per tool and neither is reversible. The only reliable signals are the `cwd` recorded inside the session and the tool's own registry.
2. **Never trust a single decompress pass to give you everything.** DSH session files are multi-frame zstd; decompressing one shot **silently** returns only the first frame (a single metadata line) with no error. See [references/transcript-formats.md](references/transcript-formats.md).
3. **Reconcile against the authoritative registry before concluding.** If the session count does not match the tool's own registry, you missed something.
4. **An export is verbatim, so it inherits whatever was pasted into the conversation** — session cookies, bearer tokens, API keys. `export` redacts credential values as it writes (see [scripts/redact-exports.mjs](scripts/redact-exports.mjs)); for an export directory produced another way, or as a pre-commit guard, run that script directly. It replaces values and keeps key names, so the transcript still reads as evidence. This matters twice over for the guide: `evidence.gz` is cut *from* those exports, so an unredacted export is how a credential reaches a file that ships with the repository.

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

### Updating an existing guide (incremental, never destructive)

A repository may already carry a guide from an earlier run. **Never regenerate it** — it holds hand-written prose and a stable index that other documents link to.

```bash
node "$SKILL/scripts/build-guide-index.mjs" --guide <guide-dir> --json
```

That prints the inventory: every existing `id`, its `source`, and `nextIds` — the next free id per prefix. Use it to:

1. **Deduplicate before appending.** A finding is already recorded when its session and line range appear in an existing `source`, or when its subject matches an existing entry. In that case **update that entry** — status, resolution, a sharper diagnosis — rather than adding a near-duplicate. Two entries for one bug are worse than one.
2. **Continue the numbering** from `nextIds`. Never reuse an id.
3. **Append to the matching topic file**, at the end. Do not reorder, renumber or rewrite existing records.
4. **Rebuild the index** (`build-guide-index.mjs` without `--json`); it rewrites only the region between the README's marker comments and leaves the prose alone.

Stamp each addition with its own `batch` date, so the next increment stays traceable.

### Capturing a pitfall you just hit

You do not need a session export. When a pitfall surfaces **in the conversation you are in**, that conversation is the source:

1. Extract the same facts you would from a transcript: what broke, how it surfaced, how it was diagnosed, what fixed it, what to do differently. Quote the **real** error text, command and path from the conversation — not a paraphrase of them.
2. Pick the topic and take the id from `nextIds`.
3. Write the entry into that topic file with the evidence inline (the actual output, quoted). Archive a range only when the surrounding context is genuinely needed *and* the session has already been exported — otherwise inline is the correct choice.
4. Set `source` to the session id when you know it, or `current session <YYYY-MM-DD>` when you do not.
5. Rebuild the index.

Do not wait for a batch. A pitfall written down while it is fresh is worth more than one reconstructed a month later from a transcript nobody re-reads.

### Write for humans

The guide is read by people, not by the agent that produced it.

**1. Write in the user's language.** Whatever language the conversation is in, the guide body uses it — titles, the seven sections, the README prose. Only the metadata labels (`batch`, `severity`, `status`, `source`) stay English, because scripts parse them.

**2. Humanize before finishing.** Resolve a humanizer nearest-first, and stop at the first hit — the same way configuration lookup works, a narrower scope wins:

1. **This repository.** `<project>/.agents/skills/`, `<project>/.claude/skills/`. A project-local humanizer is the most relevant one that can exist: someone chose it for *this* codebase's voice and language.
2. **This machine.** `~/.agents/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, plus the skill list the client injects into the session. Look for a name containing `humaniz` (or the equivalent in the user's language).
3. **The open ecosystem** — [skills.sh](https://skills.sh) and GitHub — only once 1 and 2 both came up empty. Run the CLI **with the package manager the project declares** — `npx` inside a pnpm repo dies with `EBADDEVENGINES`, which is a mismatch to respect, not to work around by leaving the directory:
   ```bash
   pnpm dlx skills find "humanizer <language>"   # project pins pnpm
   npx      skills find "humanizer <language>"   # npm, or no pin
   yarn dlx skills find "humanizer <language>"   # yarn
   bunx     skills find "humanizer <language>"   # bun
   ```
   If the CLI cannot start at all — a sandbox that denies writes to the package-manager cache (`ERR_PNPM_CLI_DLX_CACHE`, npm `EPERM`), no network, or the tool is missing — do not stop there: search the web for `skills.sh humanizer <language>`. The `skills.sh` search page is client-rendered and its API requires a Vercel OIDC token, so a plain web search is the route that works when the CLI is unavailable.

If one turns up at any level, **use it as-is** — do not keep searching for a "better" one. If all three come up empty, do not stall and do not install anything without asking; apply the rules below.

Naming convention in the ecosystem: plain `humanizer` is the English one; localized versions carry a language suffix (`humanizer-zh`, `humanizer-ja`, …). Pick the one matching **the language the user is writing in** — not the language of this skill.

The rules to apply when no humanizer is available:

- **Concrete beats abstract.** "The local clock ran 12 s ahead of Supabase" — not "a time-synchronisation issue was present".
- **One idea per paragraph.** Cut the pile-up of parallel clauses that machine prose defaults to.
- **No throat-clearing.** Delete openers like "It is worth noting that", "In conclusion", "Furthermore", and the closing paragraph that repeats what was just said.
- **Name the actor.** "The agent read the exit code and declared success" beats "success was declared".
- **A heading states the conclusion**, not the topic: "Migration 2 can never apply", not "Migration ordering".
- **Leave code, paths and commands exactly as they are.** Do not smooth them out for the sake of readability.
- **Keep quotes verbatim.** They are evidence; rewriting them destroys their value.

Read the result back once as if a colleague had written it. If it sounds like a status report produced by a machine, rewrite it.

## Boundaries

- Formats change between tool versions; `doctor` reports whether a location exists, not whether the format still matches. When parsing fails, fall back to grepping the raw session files instead of declaring there were no conversations.
- Session files can be deleted by the tools' own cleanup. **Export as soon as you find them** rather than relying on them being there later.
- `doctor` lists known-but-unsupported tool directories. If one exists, there are sessions outside this script's reach — add an adapter using the steps above.
