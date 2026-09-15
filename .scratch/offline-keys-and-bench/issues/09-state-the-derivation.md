# 09 — State the derivation, or state why it cannot be had

**What to build:** The answer to the question ticket 07 opened. Either the derivation of a *segment key*, written down and pinned by a test against a real captured triple — or a written cause of death naming what was tried and what ruled it out. Not "still investigating".

**Blocked by:** 07

**Status:** done

- [x] Either the derivation is written down, or the cause of death is, with the evidence behind it.
- [x] If the derivation is found, a test pins it against bytes captured from a real run rather than a round trip of the author’s own code.
- [x] The answer says plainly whether it uses anything visible on the wire.
- [x] The conclusion is recorded where the next reader will find it.

## The derivation, found

```
segment key = MD5_hex( tk + filename + "20220507" )
```

`tk` is the segment-list response's `tk`, `filename` is `119354-<uuid>.ts`, and the third input is a
constant the player's Bridge layer returns for an obfuscated name. **Two of the three inputs are on
the wire**, and the third is the same for every segment — so nothing has to be played, and nothing
has to be read out of a process, to compute a key.

Ticket 07's write breakpoint was never what answered this, and by the end it was not needed: the
chain is visible statically, and the one value that is not in the file was caught with a hook on
`0x1FD60` (`tools/parser-tools/probe_kdf.py`) while a lesson played.

Evidence, all of it recorded keys that were obtained *without* this formula:

| Source | How those keys were obtained | Result |
| --- | --- | --- |
| 258 triples, `ev2_out/keys.json` × `manifests.capture` | harvested by the earlier pipeline, different session | 258/258 |
| 42 triples, `ctx_dump.json` | rebuilt from each context's AES schedule | 42/42 |
| `triple.json` | schedule and manifest entry read at one moment | match |
| 56 contexts of a live player | rebuilt from its schedules while it played | 56/56 |

`crates/evmedia-core/tests/derivation.rs` pins thirteen of them and fails if the extra is dropped.
The full account, the candidates that were ruled out first, and the end-to-end CLI path are in
[`docs/KEY-DERIVATION.md`](../../../docs/KEY-DERIVATION.md).

## The earlier record, kept because it explains why this took so long

The candidates below were all tested against 258 real `(tk, filename, key)` triples before the
constant was caught, and all of them failed — which is the answer to "why not read it off the
wire": the third input is not on the wire. `sign`, `t`, `sid`, `bid`, `v`, `idx`, `sf` and `d_p`
after `tk+filename`; permutations of those in 2- and 3-part orders with eight separators; 2,825
32-hex strings harvested from the player's memory in four orders; and short constants of every
alphabet up to six characters. Zero hits each.

The one that would have found it — a *constant that is not hex, not from memory and not from the
URL* — was not in that space at all, which is why the live hook was the right next move rather
than more searching. The same applies to ticket 07's write watch: it was aimed at the wrong
question (where a key is *stored*, not what it is *made of*).
