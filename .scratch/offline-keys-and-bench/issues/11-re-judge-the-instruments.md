# 11 — Re-judge the instruments

**What to build:** The second pass over the research bench. The scripts kept as instruments for tickets 06–10 are judged again now the experiments are over: the ones with no further use are deleted, and only a capability the product will still need is ported into Rust. A script is never ported merely for being Python.

**Blocked by:** 10

**Status:** ready-for-agent

- [ ] Every instrument is judged again, with a verdict and a one-line reason.
- [ ] Instruments with no further use are deleted.
- [ ] Any capability the product still needs is implemented in Rust and arrives behind the existing test seam.
- [ ] The bench holds nothing whose purpose is not stated.
