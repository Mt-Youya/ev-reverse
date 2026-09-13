# Spec: close the offline key path, and finish the bench

Status: ready-for-agent

The three experiments marked **bounded** below are the exception, and are the reason this line is
not the whole story: they need a live player and a human at the keyboard, so when this spec is cut
into tickets those three carry `ready-for-human` and the rest carry `ready-for-agent`.

Vocabulary is `CONTEXT.md`'s: *lesson*, *segment*, *lesson index*, *segment key*, *playback
context*, *schedule*, *key window*, *gap*, *harvest*, *sweep*, *stall*, *live key form*, *stored
key form*, *manifest*, *capture*, *merge*, *complete merge*.

## Problem Statement

The tool captures a lesson reliably, but only by playing it first. A segment key exists in the
player's memory only after that segment has been decrypted for playback, so capturing a lesson
means driving the playhead across it — roughly 12 segments per second against about 1.35 while
watching, but still a pass over every lesson. Capturing a whole course therefore costs one pass
per lesson, which is the thing the work was supposed to avoid.

Three lines of attack on that limit were opened and left unfinished:

- The derivation of a *segment key* from values visible on the wire was never recovered. About 11
  million local hypotheses have been ruled out, so the remaining question is not "guess harder"
  but "watch where the player writes the key and read the derivation off the code".
- The plaintext of the `getDownEVSKey` response was never read. It is the one response that might
  make a lesson playable end to end without the player.
- The API's own AES was never turned into an oracle, so a captured request body cannot be read
  after the fact.

Separately, the repository carries the research bench that produced all of this: 31 Python scripts
in several overlapping states of usefulness, dead ones among them, and leftover artifacts on disk.
Nobody has decided which of them the product still needs.

And one verification was never done: `--sweep` claims to notice a *gap* behind the playhead and
walk back over it, but the code path has only ever run against a fixture. The last gaps in real
use were filled by hand.

## Solution

From the user's perspective:

- Either lesson keys become obtainable without a full pass, or the reason they cannot be is stated
  with evidence and the limit is accepted explicitly instead of being rediscovered each time.
- Any captured request body can be read, because the player's own AES answers questions.
- The `getDownEVSKey` response is read once, and what it contains is known.
- `--sweep`'s gap handling is demonstrated on a real gap, not only on a fixture.
- The repository contains no research script the product does not need, and no artifacts of runs
  that are over.
- The branch is merged through a pull request, with the sensitive files handled deliberately.

## User Stories

1. As the operator, I want to know where in the player's code a *segment key* is written into a
   *playback context*'s schedule, so that I can read the derivation instead of guessing it.
2. As the operator, I want the derivation traced back to its inputs, so that I can tell whether it
   uses anything visible on the wire.
3. As the operator, I want a clear statement when the derivation cannot be recovered, naming what
   was tried and what ruled it out, so that the limit is documented rather than open-ended.
4. As the operator, I want the player's own AES exposed as callable, so that a captured request
   body can be decrypted after the fact without a live capture.
5. As the operator, I want the oracle to work on any ciphertext, not just one captured message,
   so that it stays useful as new traffic is captured.
6. As the operator, I want the `getDownEVSKey` response plaintext read at least once, so that I
   know whether it contains anything that removes the need to play a lesson.
7. As the operator, I want the capture to happen in one sitting together with the derivation
   experiment, so that I open a lesson once rather than twice.
8. As the operator, I want the record of the whole-courses question settled — either the
   no-playback path exists or it does not — so that the goal can be restated honestly.
9. As the operator, I want `--sweep` to detect a real gap behind the playhead and walk back over it
   with no help, so that I can leave a long capture unattended.
10. As the operator, I want the sweep's gap handling covered by a fixture test that runs without a
    player, so that a regression is caught by `cargo test` rather than by a wasted capture.
11. As the operator, I want a deliberate gap produced by jumping the playhead forward, so that the
    test is a real one rather than a simulation.
12. As a maintainer, I want every script in the research bench classified as "already covered by
    the Rust product", "must be ported", or "dead", so that the bench stops being ambiguous.
13. As a maintainer, I want the scripts that are dead deleted rather than described, so that the
    next reader is not misled by tools that cannot work.
14. As a maintainer, I want the instruments that are still in use left alone until they are no
    longer needed, so that an experiment is not interrupted by a rewrite of its own apparatus.
15. As a maintainer, I want the single test seam to be the stand-in for a live player, so that the
    harvest loop and the seek are both testable without Windows or a player.
16. As a maintainer, I want `Harvester` to *be* the playhead rather than delegate to one, so that
    there is one seam instead of two that mean the same thing.
17. As a maintainer, I want the seek asserted by the position it reaches rather than the calls it
    makes, so that the test survives a change of stepping strategy.
18. As the operator, I want `HANDOFF.md` in the repository with its tokens replaced by
    placeholders, so that the record of the early rounds survives without carrying live
    credentials.
19. As the operator, I want the third-party key dump to stay out of the repository, so that
    somebody else's captured keys are not published under my name.
20. As the operator, I want intermediate artifacts of finished runs removed, so that disk use
    reflects what is still being worked on.
21. As the operator, I want the finished videos kept, so that the work's output is not thrown away
    with its scratch space.
22. As a reviewer, I want the branch merged through a pull request describing the change and the
    correction it contains, so that the history records why the key source was restored.
23. As the operator, I want the outstanding "is it all done" question answered with a list, so that
    nothing is left implied.
24. As the operator, I want each experiment to end in either a result or a named cause of death,
    so that no item remains in a "still trying" state.

## Implementation Decisions

### The test seam becomes one

`Harvester` gains `Playhead` as a supertrait and loses its own `seek_to`. The loop passes the
harvester itself to the seek. `Playhead` keeps its two methods — `window`, returning the extent of
the *key window*, and `step`, moving the playhead. What changes is who implements them: the
fixture in the harvest tests, rather than a separate fake in the seek tests. The seek tests keep
their own minimal playhead because they test the algorithm alone; that is a unit under test, not a
second product seam.

Consequence for the existing sweep tests: they assert that the playhead arrives where it was
aimed at, not that `seek_to` was called with a particular index. That is a stronger assertion and
it is the reason for the change.

### Where research output lands

The three bounded experiments (derivation, oracle, response plaintext) produce observations, not
code paths, and get no seam. What they can leave behind is durable and does have a home:

- A newly understood struct offset or field goes into `evmedia-win` behind the existing `Player`
  API, next to the ones already there, each with a note saying what it is and how it was confirmed.
- A newly understood derivation goes into `evmedia-core`'s crypto module, covered by the test that
  already pins a recorded `(schedule, key)` pair.
- Anything that only ever runs once, during the experiment, stays in the research bench and is
  deleted with it.

### The oracle

The oracle is a Frida RPC export: the player's AES routine is wrapped so that a caller outside the
process can hand it a buffer and get the transformed buffer back. The key is not recovered and is
not needed — the point is to stop needing it. It lives in the research bench, because it is an
instrument, not a product capability; if it turns out to be needed repeatedly it is a candidate for
the porting pass.

### Classification of the research bench

Every script gets exactly one of three verdicts, recorded in the spec's issue rather than in a file
that can go stale:

- **Covered** — the Rust product already does this, so the script is deleted.
- **Instrument** — needed for the bounded experiments; kept until they conclude, then re-judged.
- **Dead** — cannot work against this build; deleted.

A script is never "ported" merely because it is Python. Porting is reserved for capabilities the
product will still need after the experiments end.

### Sensitive material

`HANDOFF.md` is sanitized — tokens and the complete request sample replaced with placeholders — and
committed, because it is the only record of the early rounds. The third-party key dump stays
untracked. This is the same rule that keeps the capture directory out of the repository.

### Verification boundary

An experiment that needs the live player is run once, with the human at the keyboard opening the
lesson and triggering the download, and the tooling driven from here. The result is recorded as an
observation with the command and its output. Nothing is declared confirmed on the strength of a
plausible argument.

## Testing Decisions

A good test here observes behaviour at a seam, not the shape of the code behind it. The codebase
already does this: the harvest loop is exercised end to end against a fixture that stands in for
the player, so resume, retry, the completeness verdict and the merge are all checked with no
player, no Windows and no network. The seek was recently extracted for the same reason — it used to
live in the Windows crate where nothing could reach it.

Three places get tests:

1. **The harvest loop**, through the merged `Harvester`/`Playhead` seam, using the existing fixture.
   The sweep's gap handling is already covered by three fixture tests that enable the sweep and
   drive a gap; the work here is to re-point them at the merged seam and keep every assertion, not
   to invent coverage. Assertions worth keeping: the seek is aimed at the earliest missing index
   and the merge afterwards is complete; a source that reports it cannot move the playhead is asked
   once and then left alone; a playhead that answers yes and changes nothing still terminates at
   the attempt cap.
2. **The seek algorithm**, through its minimal playhead, which already covers arrival, walking
   forward and back, convergence over several rounds, correcting an overshoot, an unbound playhead
   reporting once rather than retrying, and an empty window.
3. **The command-line surface**, through the existing argv round-trip, for anything the porting pass
   adds. `describe()` and `to_argv` are held to each other, so a new subcommand is covered by
   construction rather than by a new test.

The one thing no fixture can cover is the real-gap run, which needs a live player and a human; that
is a verification, not a test, and it is recorded as an observation.

Prior art to follow: the fixture in the harvest tests, the `Playhead` fake in the seek tests, and
the recorded-pair test in crypto that pins a derivation against bytes captured from a real run
rather than against a round trip of the author's own code.

Every new test is to be mutation-checked — revert the behaviour it claims to pin and confirm the
test fails. A test that passes with the fix removed is not evidence.

## Out of Scope

- Making a *segment key* derivable from the wire, if the derivation experiment says it is not. The
  point of the experiment is to settle the question; accepting a negative is a valid outcome.
- Porting the research bench wholesale. Porting is decided per script, after the experiments.
- Any change to the capture pipeline's correctness. It works; this spec verifies one untested
  branch of it and does not redesign it.
- The player's user interface. The only interaction with it is posted key events and the human
  opening a lesson.

## Further Notes

The correction this work contains is worth recording where a reviewer will see it. The player's
schedule slot was declared to hold no key, the code that read it was deleted on that basis, and the
declaration was wrong: it came from a test that was itself broken by a probe length that was not
AES-block aligned, and the conclusion outlived the bug. Measured directly, 333 of 333 filled slots
yielded keys that opened their segments. The derivation is back and is now the sweep's fast path —
826 milliseconds for a round against 32.2 seconds for the candidate-scan fallback.

The lesson generalises, and applies to the experiments in this spec: a negative result is only
worth as much as the tool that produced it, and a tool that fails silently produces a negative
result that looks exactly like a real one.
