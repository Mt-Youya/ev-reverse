# 01 — Merge the two player stand-ins into one, and keep the sweep’s gap handling covered

**What to build:** One seam instead of two. `Harvester` becomes the single stand-in for a live player: it gains `Playhead` as a supertrait and loses its own `seek_to`, so the harvest loop hands the harvester itself to the seek. The sweep’s three existing tests keep every assertion but are re-pointed at that seam — they assert where the playhead arrived, not which index a seek was called with, which is the stronger claim.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [x] `Harvester` extends `Playhead`, and `Harvester::seek_to` no longer exists.
- [x] The harvest fixture implements the window/step pair, and no test implements `seek_to`.
- [x] The three sweep tests still assert: the seek targeted the earliest missing index and the merge afterwards completed; a source that cannot move the playhead is asked once and then left alone; a playhead that answers yes and changes nothing still terminates at the attempt cap.
- [x] The seek tests keep their own minimal playhead — that one is a unit under test, not a second product seam.
- [x] `cargo test --workspace` passes with no new warnings.
- [x] Mutation check: reverting the behaviour each sweep test pins makes that test fail.

## Notes

The claim the sweep tests make is now about where the playhead *arrived* plus the presses it took to
get there — the old assertion was about which index a seek was asked for, which a rewrite of the
calibration could satisfy while the playhead still went nowhere. `seek_window_to` is generic over
`P: Playhead + ?Sized` so `&dyn Harvester` passes straight through, and `WinSource` implements both
traits on the same object — nothing a test has to stand in for twice.

**Corrected after the fact:** the first version of this note said the arrival assertion "survives any
change of stepping strategy". It does not. The tests still pin `fixture.steps() == vec![(false, 3)]`
and `steps().len() == 1`, so a change of batching breaks them. What actually went away is the
target-*index* assertion, which is the part a strategy change always forced. Found while
mutation-checking the tests, and corrected here and in the spec rather than left standing.

**Mutation check** (revert the behaviour, confirm the test fails):

| Mutation | Result |
| --- | --- |
| A — the gap stops being visible to the loop (drop the `seen.extend` over `places`) | 0 passed, 3 failed |
| B — an unbound seek is retried instead of believed (drop `sweeps_left = 0`) | 2 passed, 1 failed (`a_source_that_cannot_seek_is_asked_once_and_then_left_alone`) |
| C — the sweep attempt cap is removed (drop `sweeps_left > 0`) | 1 passed, 2 failed |

All three sweep tests are genuinely pinned. Tree restored and re-verified: `cargo test --workspace`
→ **45 passed, 0 failed**, `cargo check --all-targets --workspace` clean with no warnings.
