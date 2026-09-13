# 07 — Catch the instruction that writes a segment key into a playback context

**What to build:** A hardware write breakpoint on one empty *schedule*, caught the moment the player fills it. What comes out is the writing instruction and its backtrace — the trail to the derivation. If it cannot be caught, the reason is named rather than left open: a per-thread breakpoint across sixteen threads may simply miss the writer, and that is a result too.

**Blocked by:** None — can start immediately

**Status:** ready-for-human

- [ ] An empty schedule is found and a write breakpoint is armed on it, or the reason no empty one exists is stated.
- [ ] A write is caught, with the instruction pointer and a backtrace — or a named reason it was not caught.
- [ ] The command used and its output are recorded as the evidence.
- [ ] The player is left running, or is restarted and the already-recovered keys are confirmed intact.

## The instrument had to be replaced before this ticket could be run

Found while preparing the human session, and fixed here so the session is not spent on a script that
cannot work.

**`watch_key.py` was the only surviving instrument that could arm a memory write watch, and it is the
one the bench marks "do not run this directly"** — it watches up to 96 pages, re-arms the monitor every
1.2 s, and attaches an `Interceptor` from inside the access callback. On a live player that is enough
to hang it. Handing this ticket that script would have cost the session it exists for.

**The ticket's own fallback is not available either.** It names "a per-thread breakpoint across
sixteen threads" as the thing that might miss the writer. Frida 17.18.0 has no hardware watchpoints:
`typeof Thread.setHardwareWatchpoint` is `undefined`, checked by loading a probe script against a live
process. `MemoryAccessMonitor` — page guards — is the only mechanism that exists on this build, so the
miss to worry about is a guard-page one, not a hardware-slot one.

**So this ticket now runs on a new, deliberately light instrument:** `tools/parser-tools/probe_write.py`.
It finds the playback contexts, picks **one** whose key field still holds the heap fill (`0xBAADF00D`
at `+0x120`, i.e. not yet decrypted), guards that single page, reports the first write to the field,
and disables the monitor immediately — one page, no re-arm loop, no `Interceptor` inside the callback.
The instruction it names is then hooked once, after the fact, to dump registers, stack strings and a
backtrace the next time that instruction runs.

The script is syntax-checked (the JS loads cleanly; the only error is the expected missing-DLL one in a
throwaway process). **It has not been run against the player** — that is what this ticket's session is
for. It is a small script precisely so that it can be corrected live if the mechanism behaves
differently than expected.

### How to run it

```
C:\Users\Yonjay\.conda\envs\subgen\python.exe -u tools\parser-tools\probe_write.py 240
```

Preconditions, all load-bearing:

- EVPlayer2 running, logged in through `tools/device_launcher/run-evplayer.cmd`.
- A lesson open **that still has segments the player has not decrypted on this machine.** The writer
  only runs while the player is decrypting something for the first time, so a lesson already played to
  the end produces nothing. Open a lesson never opened here, or jump the playhead somewhere it has not
  been.
- Keep it playing. Do not pause.

The PATH `python` is a Microsoft Store placeholder and does not work; use the conda path above.

**Success** is a `WRITE to the key field of a context` line naming the writing instruction as a module
offset or as heap code, followed by `the writer, with context`.

**A named negative is also a success** for this ticket, and the two most likely ones both have a
diagnosis attached:

- `no context with an empty key field` — every context on this machine has been decrypted, so there is
  no store left to catch. The remedy is a lesson that has not been played here.
- The monitor arms and nothing is ever written — the store lands outside the guarded page's window, or
  the field is filled by a bulk copy whose instruction touches the page before the field itself.
