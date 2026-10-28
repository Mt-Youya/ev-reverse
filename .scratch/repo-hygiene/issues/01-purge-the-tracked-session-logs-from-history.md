# Purge the tracked session logs from history

Status: ready-for-human
Type: task

## What is wrong

Two machine-local agent session logs were committed in `9fe7b0d feat: add tools` and have been
carried by every clone and every push since:

| path | blob | note |
| --- | --- | --- |
| `.claude/sessions/1b8d953d-0f50-4ca5-b7e8-a788815de64c.jsonl` | 8.54 MB | four full HTTP request dumps; one carries `authorization: Bearer <247-char JWT>` |
| `.codex/sessions/rollout-2026-09-13T10-18-36-01a0988f-….jsonl` | 1.06 MB | session ids only |

The token is an EVPlayer2 catalog bearer token captured on 2026-09-13. By this project's own
notes (`CONTEXT.md`) that credential is short-lived, so it is long expired and needs no
rotation — the reason to act is repository hygiene (9.6 MB of a 26.9 MB object store), not a
live secret.

## What is already done

`9bdfb78 chore(gitignore): stop tracking the raw agent session logs` removed both files from
the index (they stay on disk) and added `/.claude/sessions/` and `/.codex/sessions/` to
`.gitignore`, next to the existing `.agent-sessions/` rule. Nothing new can enter the index
this way, and the exports the archaeology skill writes are redacted by
`redact-exports.mjs` before they are written.

## Why it is deferred

The blobs are in 175 commits after `9fe7b0d`, across 3 local and 5 remote branches. Removing
them rewrites every one of those commits, so every clone is invalidated and the mirror needs a
force push. That must not happen while another session is committing to this repository — one
was, when this was written (`tools/*.py` had uncommitted work). The trade is 9.6 MB against a
coordination cost, for a credential that is already dead.

## What to do

Run this only when every session working in this checkout has stopped and pushed.

```powershell
# 0. A bundle of the current state: the only rollback that survives a bad rewrite.
git bundle create ..\ev-reverse-before-purge.bundle --all

# 1. Rewrite every ref, dropping the two paths. git-filter-repo is not installed here;
#    the built-in filter-branch is slower but does the job for 175 commits.
git filter-branch --force --index-filter `
  "git rm -r --cached --ignore-unmatch .claude/sessions .codex/sessions" `
  --prune-empty --tag-name-filter cat -- --all

# 2. Drop the backup refs and the now-orphaned objects, or the size does not move.
git for-each-ref --format="%(refname)" refs/original/ | ForEach-Object { git update-ref -d $_ }
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 3. Verify before pushing anything.
git rev-list --objects --all | Select-String 'sessions/(1b8d953d|rollout-2026-09-13)'  # must be empty
git count-objects -vH                                                                # expect ~17 MB

# 4. Force-push every branch. --force-with-lease first: if it refuses, someone pushed since
#    the rewrite started, and that is exactly what you want to find out before overwriting.
git push --force-with-lease origin --all
```

## Acceptance

- `git rev-list --objects --all` no longer lists either path, and `git count-objects -vH`
  shows the object store down from 26.9 MB to about 17 MB.
- `9fe7b0d` no longer exists under that hash; the branch history is the rewritten one.
- Every other clone has re-synced — a plain `git pull` on a rewritten branch is not enough:
  `git fetch origin` then `git reset --hard origin/<branch>` (or a fresh clone).
- The local files are still on disk and still ignored, so re-running a session does not put
  them back. Only delete the bundle once every clone has been re-synced.

## Comments

Written 2026-10-11 after a repo-wide credential scan (the skill's own rule table over
`git ls-files`, 337 files) found the JWT in the `.claude` log. The same scan cleared the other
matches: they are test fixtures (`session.rs`, `make_fixture.rs`, `argv_roundtrip.rs`,
`evs_manifest.rs`), documentation of this project's own subject matter
(`docs/RETROSPECTIVE.md`), or false positives (`TavilyClient(api_key)` in
`.scratch/ev-posters/`, `token = remove_…` in the `.codex` log, an RVA constant next to
`nRVA_SET_ENCRYPT_KEY`).
