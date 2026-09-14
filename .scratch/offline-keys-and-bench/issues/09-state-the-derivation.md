# 09 — State the derivation, or state why it cannot be had

**What to build:** The answer to the question ticket 07 opened. Either the derivation of a *segment key*, written down and pinned by a test against a real captured triple — or a written cause of death naming what was tried and what ruled it out. Not "still investigating".

**Blocked by:** 07

**Status:** ready-for-human

- [ ] Either the derivation is written down, or the cause of death is, with the evidence behind it.
- [ ] If the derivation is found, a test pins it against bytes captured from a real run rather than a round trip of the author’s own code.
- [ ] The answer says plainly whether it uses anything visible on the wire.
- [ ] The conclusion is recorded where the next reader will find it.

## The derivation is read off the code without a player, and one input is still missing

Status: **ready-for-human** for the last step; everything before it is done and is in
[`docs/KEY-DERIVATION.md`](../../../docs/KEY-DERIVATION.md). Ticket 07's write breakpoint is no
longer the only way in — the whole chain is visible statically in
`PlayerLibRender56_vs.dll`, and the parts that were guessed before are now confirmed:

```
segment key (32 lowercase hex characters) = MD5_hex( tk + filename + extra )
```

- `tk` is the M3U8-list response's `tk` (it is the context's `+0x288` field), `filename` is
  `ctx+0x18`. Both are on the wire, and the order is fixed by the two `memcpy`s in `0x21A30`.
- The hash is `0x1FD60`, which loads the MD5 constants and formats the 16 digest bytes with `"%02x"`
  in `0x1D8A0` — 32 lowercase hex characters, the stored key form.
- The key string is handed to `0x40AF0`, whose error string is `hls_decode url:%s  aes init failed
  %s`, which calls the AES setup at `0x20EC0` with `r8d = 0x100` and the destination `ctx+0x120`.
  That is the schedule `evmedia-core::crypto` reads, and it settles what that slot is.
- The context's virtual slot 1 is `xor al, al; ret`, so the "use the tk directly" branch the code
  appears to have is dead and the MD5 branch always runs.

**`extra` is a runtime value and it is the whole obstacle.** It is fetched through a Bridge
interface (`iface = *(void**)0xBF26C8`, an array filled by `Bridge.dll!__Init_CD_Later__`), by name
from a UTF-16 string pool at `.rdata:0x8034A0` — the key path uses the 10-character slice at
`0x803550` — and `0xA6` (166) on every one of the 415 such lookups in this binary. Neither the
value nor the meaning of the obfuscated name is on the wire.

Measured against real captures, so the negative is a measurement rather than an argument:

| Candidate third input | Data | Result |
| --- | --- | --- |
| `sign`, `t`, `sid`, `bid`, `v`, `idx`, `sf`, `d_p` after `tk+filename` | 258 same-session `(tk, filename, key)` triples | 0 each |
| 2- and 3-part permutations of every captured field, 8 separators | one simultaneously-read triple | 0 |
| any of 2,825 32-hex strings harvested from the player's memory, 4 orders | 258 triples | 0 |
| a short constant (hex ≤ 6, alnum ≤ 4, digits ≤ 5) | one triple, then the rest | 0 |

**Three ways to finish it, in the order worth trying:**

1. Read the module's getter. `0xBF26C8` holds the `bg::Interface*` array; the player's own
   registration stores its vtable, and the getter behind `+0x40` is where the value is computed.
   Static, no player.
2. Decode the pool. The `0x42A30` path uses one of those values as an **AES key for a captured API
   response** (`0x1EA10`, failure string `dec error !`), which makes it a checkable oracle.
3. Watch the lookup once against the running player and read the value. One segment is enough — this
   is the only step that needs a human, and it needs a *second*, not a lesson.

A test pinning the derivation cannot be written yet, and deliberately is not written against a
guess: the point of the ticket is that the last input is unknown, and a test that pins
`MD5_hex(tk + filename + something)` would be a test of the author's own assumption.
