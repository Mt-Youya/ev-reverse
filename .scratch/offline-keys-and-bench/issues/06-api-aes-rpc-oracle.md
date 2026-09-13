# 06 — Read the API’s AES out of the player with an RPC oracle

**What to build:** The player’s own AES, callable from outside the process. A captured request body decrypts through it, and it accepts any buffer rather than one hard-coded message. The key is never recovered and never needs to be: the point is to stop needing it.

**Blocked by:** None — can start immediately

**Status:** ready-for-human

- [ ] A buffer can be handed to the player’s AES and the transformed buffer comes back.
- [ ] A captured request body decrypts to readable plaintext through it.
- [ ] The oracle accepts an arbitrary input, not one captured message.
- [ ] The command used and its output are recorded as the evidence.

## What is already in place, and the one gap

Written up while preparing the human session, because the second half of this ticket turns out not to
be a script that exists.

**Startable with a surviving instrument.** `probe_at.py` hooks one RVA in a live module and dumps, at
each hit, the registers that point at printable strings, the strings on the stack, and a 32-hex scan.
Run against the AES body it is how the calling convention gets read off:

```
C:\Users\Yonjay\.conda\envs\subgen\python.exe -u tools\parser-tools\probe_at.py PlayerLibRender56_vs.dll 0x792c78 30 0x2000
```

Preconditions: EVPlayer2 running and logged in, then browse the catalogue or open a lesson so the API
call happens while the hook is armed. (`0x792c78` is the instruction that reads the inverse S-box.)
What to read out of the hits: which register holds the input buffer, which holds the output, and
whether a third argument points at an `AES_KEY`. That is what a callable oracle needs.

**The gap: no surviving instrument calls into a module.** The three scripts that use `rpc.exports` —
`find_in_mem.py`, `sniff_plain.py`, `wait_ready.py` — all export memory *scanners*, not calls. So the
oracle itself is a script that has to be written, and it cannot be written honestly until the
calling convention above is known.

**A second gap, inherited from ticket 03.** `0x792c78` is an instruction in the middle of the routine,
not its entry point, and the entry for the *encrypt/decrypt* half has never been confirmed — only
`0x791F90` is known, which is `AES_set_encrypt_key` (OpenSSL signature, and `hook_aes_key.py` caught
2825 keys there, so it is a real entry). To call the AES you need the enclosing entry of `0x792c78`.
Two scripts that were hunting exactly that — `aes_disasm.py` and `aes_map.py` — were deleted by ticket
03, correctly: they needed a disassembler this machine does not have and could not run. Their approach
is recoverable without one, at runtime: scan backwards from `0x792c78` for the nearest compiler
alignment padding (`cc cc cc`) or prologue, and take the address after it. That is a small addition to
the probe, not a new investigation, and it should be done in the same sitting.

## Why this ticket matters more than its size suggests

This is one of the two routes to the thing the project actually wants. If the player's AES can be
called, then `POST /student/getPlayTimeKeySignEVS*` can be replayed from outside for any lesson in the
catalogue, and the segment manifests can be pulled without a human opening anything. That is the
"no play-this-one-fetch-this-one" goal, minus the per-lesson playback that blocks it today.
