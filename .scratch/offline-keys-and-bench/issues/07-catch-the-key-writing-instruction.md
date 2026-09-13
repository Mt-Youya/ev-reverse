# 07 — Catch the instruction that writes a segment key into a playback context

**What to build:** A hardware write breakpoint on one empty *schedule*, caught the moment the player fills it. What comes out is the writing instruction and its backtrace — the trail to the derivation. If it cannot be caught, the reason is named rather than left open: a per-thread breakpoint across sixteen threads may simply miss the writer, and that is a result too.

**Blocked by:** None — can start immediately

**Status:** ready-for-human

- [ ] An empty schedule is found and a write breakpoint is armed on it, or the reason no empty one exists is stated.
- [ ] A write is caught, with the instruction pointer and a backtrace — or a named reason it was not caught.
- [ ] The command used and its output are recorded as the evidence.
- [ ] The player is left running, or is restarted and the already-recovered keys are confirmed intact.
