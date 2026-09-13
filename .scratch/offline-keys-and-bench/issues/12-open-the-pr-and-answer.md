# 12 — Open the pull request and answer the outstanding question

**What to build:** The branch merged through a pull request against `main`, describing the change and — prominently — the correction it contains: the player’s *schedule* was declared to hold no key, the code reading it was deleted on that basis, and the declaration rested on a test that was itself broken. Plus a direct answer to "is it all done", as a list rather than an implication.

**Blocked by:** 01, 03, 05, 11

**Status:** ready-for-agent

- [x] A pull request is open against `main`.
- [x] Its description covers the change, the correction, and the tests added for each fix.
- [x] The outstanding "is it all done" question is answered with a list of what is and is not finished.
- [x] The working tree holds nothing untracked that should have been decided either way.

**Pull request:** https://github.com/Mt-Youya/ev-reverse/pull/1

## This ticket was opened with 11 still outstanding, deliberately

Ticket 11 is blocked by 10, which is blocked by 06, 08 and 09 — all of which need a live player on the
machine and cannot be done from a checkout. Waiting for 11 would have left the branch, and the
correction it carries, unreviewed for as long as that takes.

So the pull request went up now. Nothing about it is a one-way door: it is against `main`, it can be
closed unmerged, and 06–11 push to the same branch and update the same pull request when they land.
The description is written to be re-read at that point rather than re-written — the "is it all done"
section says plainly that five of the items below are unfinished, so it reads as true whether or not
11 has happened yet.

If that judgement is wrong, the fix is cheap: close the pull request and re-open it once 11 lands.

## The repository has no CI

`gh` reports 0 passing, 0 failing, 0 pending, and there is no `.github/workflows/`. Nothing runs on a
push here, so this pull request's only gate is a human reading it. Worth knowing before merging:
`cargo test --workspace` → 45 pass, 0 fail, and `cargo check --all-targets --workspace` is
warning-free, but both were run locally and nothing enforces them on the remote.

Related finding, not fixed here: `cargo fmt --all --check` reports diffs in 29 files, including ones
this branch never touched (`main.rs`, `process.rs`, `scan.rs`, `sweep.rs`). There is no `rustfmt.toml`
and no CI, so formatting is simply unenforced and the tree has drifted. Running `cargo fmt` would fix
it in one command, but it would bury this branch's diff under a repo-wide reformat, so it belongs in
its own change.
