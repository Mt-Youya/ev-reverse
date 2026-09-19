# Collection posters

One poster per course collection, in two aspect ratios, used as the cover art for the
exported videos.

The poster is an HTML page photographed by a headless browser. The words are real text, so
they cannot come out misspelled the way a generated image's lettering can; the art behind
them comes from an image model and is required to contain no text at all. Two ratios get two
layouts rather than one scaled one, because 4:3 has the same height as 16:9 but a narrower
measure for the title to fit into.

## Pieces

| File | What it does |
| --- | --- |
| `cards.json` | Every card's words, output folder and art prompt. The single source of truth. |
| `render.mjs` | Lays a card out at both ratios and screenshots it with Chrome or Edge. |
| `backgrounds.ps1` | Generates `<root>/bg/<key>.png` for each card through the Codex CLI. |
| `deliver.ps1` | Copies finished posters into `<export root>/<collection>/封面/`. |
| `sheet.ps1` | Contact sheet per ratio, for judging the set rather than one card. |

Scratch space is `build/covers/` (`bg/`, `out/`, `html/`, `logs/`, `qc/`), overridden with
`COVERS_ROOT`. It is all build output and is not tracked.

## Running it

```powershell
node tools/covers/render.mjs                     # all cards, both ratios -> build/covers/out
powershell -ExecutionPolicy Bypass -File tools/covers/backgrounds.ps1   # art, skips what exists
powershell -ExecutionPolicy Bypass -File tools/covers/deliver.ps1       # copy beside the courses
powershell -ExecutionPolicy Bypass -File tools/covers/sheet.ps1 -Ratio 4x3
```

`render.mjs` also accepts card keys (`node tools/covers/render.mjs db rbac`) to redo one card.
With no art present it draws a vector graph instead, so layout changes can be checked offline.

## Adding a collection

Append a card to `cards.json` and run the three steps. Nothing else knows the collection list.

Two rules the file exists to enforce. Words and art live in the same entry, because when they
did not, a second collection's poster came out titled with the first one's name. And the art
prompt must keep the left half of the frame dark and empty, because that is where the title
goes.

## Three things that will waste your afternoon

**The Codex CLI needs the proxy in the environment.** It is a Rust binary: it reads
`HTTP_PROXY`/`HTTPS_PROXY` and ignores the Windows system proxy setting in the registry. On a
machine whose only route out is a local VPN client, it connects direct, resolves a poisoned
name and fails with `stream disconnected before completion` -- which looks like an outage and
is not one. `backgrounds.ps1` mirrors the system proxy into the environment before calling it.

**Concurrent runs must be attributed by session id.** Codex writes each run's image to
`$CODEX_HOME/generated_images/<session id>/` and prints the session id, so runs can be
parallel. Diffing the directory instead will eventually hand one worker another worker's
picture, which is worse than a failure because it looks like success.

**The PowerShell scripts here are ASCII-only, and have to stay that way.** Windows PowerShell
5.1 decodes a `.ps1` without a BOM as ANSI. A Chinese literal in a script therefore arrives as
mojibake: `封面` became `灏侀潰`, and `deliver.ps1` cheerfully created that directory and copied
twelve posters into it. The folder name and the file suffix live in `cards.json`, which is
opened with an explicit `-Encoding UTF8`, and nothing else here spells Chinese in a script.

Chrome is what renders the page, so a machine without either browser fails in `findBrowser()`
rather than producing a half-drawn poster.
