# 02 — Verify gap detection and refill on a real gap

**What to build:** A real capture in which the playhead is deliberately jumped forward — the way gaps actually arise — leaving a *gap* behind it, and the sweep finds that gap and walks back over it with no help. Ends in a *complete merge*. The fixture tests prove the logic; this proves the logic against a player that is actually rendering.

**Blocked by:** 01

**Status:** ready-for-human

- [ ] A lesson is open and the playhead is jumped forward far enough to leave a gap behind it.
- [ ] The sweep reports detecting the gap and aiming a seek at it.
- [ ] The merge at the end is complete, with no *lesson index* left missing.
- [ ] The command used and its output are recorded as the evidence.
