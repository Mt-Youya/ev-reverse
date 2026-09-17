# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists — it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. In multi-context repos, also check `crates/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (this one — no `CONTEXT-MAP.md` at the root):

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/            ← numbered decisions, created lazily; empty today
│   ├── ARCHITECTURE.md
│   ├── CLI-CONTRACT.md
│   └── agents/
└── crates/
    ├── evmedia-contract/
    ├── evmedia-core/
    ├── evmedia-win/
    ├── evmedia/
    └── evmedia-gui/
```

`docs/` holds prose that is not an ADR and not a glossary: `ARCHITECTURE.md` (how the pieces fit
and why) and `CLI-CONTRACT.md` (the argv/event/exit-code contract).

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a
test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly
avoids — the avoid-list is there because those words have been a source of confusion here.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing
language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (…the decision…) — but worth reopening because…_

This repo has already been burnt by the opposite: a conclusion that the player's schedule slot holds
no key was recorded, the code that read it was deleted on that basis, and the conclusion turned out
to rest on a broken test. A contradicting observation is a signal to re-measure, not to quietly
proceed.
