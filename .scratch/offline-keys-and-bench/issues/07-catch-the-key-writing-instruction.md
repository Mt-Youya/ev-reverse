# 07 — Catch the instruction that writes a segment key into a playback context

**What to build:** A hardware write breakpoint on one empty *schedule*, caught the moment the player fills it. What comes out is the writing instruction and its backtrace — the trail to the derivation. If it cannot be caught, the reason is named rather than left open: a per-thread breakpoint across sixteen threads may simply miss the writer, and that is a result too.

**Blocked by:** None — can start immediately

**Status:** ready-for-human

- [ ] An empty schedule is found and a write breakpoint is armed on it, or the reason no empty one exists is stated.
- [ ] A write is caught, with the instruction pointer and a backtrace — or a named reason it was not caught.
- [ ] The command used and its output are recorded as the evidence.
- [ ] The player is left running, or is restarted and the already-recovered keys are confirmed intact.

## Progress: the first attempt ran, caught nothing, and the cause is now known

Two runs of the first version of `probe_write.py` armed successfully and reported no write at all:

```
found 491 context(s), 438 with an empty key field
empty context: 119354-5c18a0bd-40a8-4402-bf21-15d17e38cb87.ts at 0x1ad4fb504e0 (ready=0)
armed on 0x1ad4fb50000 (... field 0x1ad4fb50600)
done.                                  <- nothing in between, 240 seconds
```

A second run found 938 contexts with 872 empty, armed, and again reported nothing.

The guard had fired. Frida's `MemoryAccessMonitor` works with `PAGE_GUARD`, and **the guard is consumed
by the first access — read or write — and is not restored.** The player reads these context objects
constantly. The first read of the guarded page hit the callback, the callback saw
`d.operation !== 'write'` and returned, and the page was unguarded from that moment on. The run was
blind for the remaining 240 seconds. It was never "the player did not write"; it was "nothing was
reported".

The first version also guarded an arbitrary empty context, chosen by scan order, which is very likely
one the player will never touch again.

## What was measured, and how

The mechanism was then tested against stand-in target processes written for the purpose, because
guessing about `PAGE_GUARD` semantics is what produced the silent failure in the first place.

| Question | Measurement |
| --- | --- |
| Does a target-thread write reach the callback at all? | Yes. A stand-in process's own stores reported normally. |
| How often does the callback fire? | Once. 27 target memory operations produced 1 report. The guard is consumed and not restored. |
| Is re-arming from inside the callback safe? | **No.** 200 reports and the target performed *zero* operations for 8 seconds — it wedged. |
| Is re-arming from a timer cheap? | Yes. A target hammering guarded pages kept 100.4% of its unguarded throughput at 64 and 128 pages, 100 ms re-arm. |
| Is a page guard free of a *store* of a different width? | The reported address is the **faulting byte**, not the write's first byte. A 32-byte store at page+0x120 reported as page+0x130. |
| Is there a hardware watchpoint to fall back on? | **No.** `typeof Thread.setHardwareWatchpoint` and `...Breakpoint` are both `undefined` on frida 17.18.0; `Thread` exposes only `backtrace`. |
| Does a guard fault before or after the store runs? | **Before.** Reading the field inside the callback returns the old contents; the value has to be read on a later turn. |
| Does `Thread.backtrace(..., ACCURATE)` work? | It raises `invalid operation` where unwind data is unusable, and did on a plain Rust target. FUZZY returned an empty list there. |
| `NativePointer.toNumber()` | Does not exist — only `UInt64` has it. Calling it inside the callback raised once per access. |

**This also corrects what the bench believed about `watch_key.py`.** It is not the 96 pages that hang
the player; 128 guarded pages measured free. It hangs because it *also* hooks the AES site and takes an
`ACCURATE` backtrace on every hit, and that site fires once per AES round. The ticket's own fallback —
"a per-thread breakpoint across sixteen threads may simply miss the writer" — describes a mechanism
this frida build does not have at all.

## The instrument now

`tools/parser-tools/probe_write.py` was rewritten around those measurements: it aims at the contexts
just past the highest decrypted index (the ones about to be filled, rather than an arbitrary one),
guards up to 24 pages, re-arms from a 100 ms timer and never from inside the callback, reports every
access with the offset it faulted at, reads the value after the store has completed, and prints a
call chain assembled from module pointers found on the stack when the unwind-based backtrace comes back
empty.

It was run end to end against a stand-in target: six reads of the guarded page (the exact sequence that
silenced the first version), then a 32-byte store into the watched field. It survived the reads, caught
the store, named the instruction, dumped the registers, read `3df51fd02753d5605cc3139e66a3561e` back
correctly, and produced a call chain after both backtracers returned nothing.

**It still has not been run against EVPlayer2 itself.** The stand-in proves the mechanism; only the real
player can answer this ticket.

### How to run it

```
C:\Users\Yonjay\.conda\envs\subgen\python.exe -u tools\parser-tools\probe_write.py 300
```

Preconditions, all load-bearing:

- EVPlayer2 running, logged in through `tools\device_launcher\run-evplayer.cmd`.
- A lesson open **that still has segments the player has not decrypted on this machine.** The writer
  only runs while the player is decrypting something for the first time. Open a lesson never opened
  here, and start the script *before* the segment is reached.
- Keep it playing. Do not pause.

The PATH `python` is a Microsoft Store placeholder and does not work; use the conda path above.

**Success** is `WRITE into a key field` naming the instruction, followed by `the writer, with context`.
**A named negative is also a result** for this ticket, and both likely ones now have a diagnosis:

- `no context with an empty key field` — every context on this machine has been decrypted, so there is
  no store left to catch. The remedy is a lesson that has not been played here.
- `still watching N page(s); 0 access(es) seen so far` repeating — the guarded pages are genuinely not
  being touched, which would mean the contexts chosen are not the ones being decrypted. Say so, and say
  which indexes were being watched.
