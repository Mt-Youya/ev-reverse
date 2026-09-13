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

**A second gap, inherited from ticket 03 — and the recipe written here for it was wrong.** The first
version of this ticket said: "scan backwards from `0x792c78` for the nearest compiler alignment padding
(`cc cc cc`) and take the address after it". Checked against the DLL on disk, that recipe returns the
**wrong function**:

- The bytes at RVA `0x792c78` are `33 6e f8 33 68 0c` = `xor ebp,[rsi-8]; xor ebp,[rax+0xc]` — a
  round-key XOR, **not** the inverse-S-box read this project has been describing. (The S-box reads are
  at `0x792c59` and nearby.)
- `.pdata` says the enclosing function is **`[0x792b1c, 0x792ec3)`**.
- There are **no `cc cc cc` runs at all in `[0x792000, 0x792c78)`**. The nearest one is at `0x791f8d`,
  which is the entry of `AES_set_encrypt_key` — a *different* function. The recipe would have returned
  that one.

**The reliable way is `.pdata`, and the DLL has it.** `PlayerLibRender56_vs.dll` is not packed: six
normal sections, `.pdata` at RVA `0x12a8000` with **19,488** function entries, each `(start, end,
unwind)`. A function's entry is a lookup, not a scan. The same is true of `.text` in general — this
DLL is fully statically analysable from the file.

**And the disassembler this project believed it did not have is installed.** `capstone 5.0.7` and
`pefile 2024.8.26` are both present in `C:\Users\Yonjay\.conda\envs\subgen`. The old note "静态分析不可用：
无 capstone/pefile/dumpbin" is wrong — most likely it was run against the PATH `python`, which is the
Microsoft Store placeholder and has neither. Two scripts were deleted by ticket 03 on that false
premise; see the correction recorded there.

One practical note for anyone writing a disassembly loop: `capstone.Cs.disasm` **stops at the first
undecodable byte** and silently returns a short iteration. Over this DLL it yielded 448 `call`
instructions instead of tens of thousands until `md.skipdata = True` was set. A scan that "found
nothing" is more likely to have stopped early than to have proved an absence.

## Why this ticket matters more than its size suggests

This is one of the two routes to the thing the project actually wants. If the player's AES can be
called, then `POST /student/getPlayTimeKeySignEVS*` can be replayed from outside for any lesson in the
catalogue, and the segment manifests can be pulled without a human opening anything. That is the
"no play-this-one-fetch-this-one" goal, minus the per-lesson playback that blocks it today.

## The oracle's entry points, located statically

Found by disassembling the DLL from the file — no player needed for this part. All RVAs, image base
`0x180000000`.

**`0x20EC0` — the app's AES key setup.** `(rcx = the AES_KEY to fill, rdx = ?, r8d = bits, r9d = mode)`.
It selects a block function by mode and stores it, then expands the key:

```
0x020ef0  mov   rdi, rcx
0x020ef3  mov   [rsp+0x28], r9d        ; mode
0x020ef8  mov   ebx, r8d               ; bits
0x020f00  sar   ebx, 5                 ; nk = bits / 32
0x020f03  lea   rax, [rip-0x63a]       ; encrypt block function
0x020f12  lea   rcx, [rip-0xbd9]       ; decrypt block function
0x020f1c  cmovne rax, rcx              ; mode != 0 -> decrypt
0x020f27  mov   [rdi+0x118], rax       ; the chosen block function
0x020f2e  lea   r12d, [rbx+6]          ; rounds = nk + 6
```

The loop that follows (`0x11b`, a `0..0xff` byte table) is the GF(2^8) inverse table — a
constant-time AES, not OpenSSL's T-table version.

**`0x20EA0` — the block-call trampoline.** Call it with `rcx = ctx`:

```
0x020ea0  mov   eax, [rcx+0x110]       ; the round count
0x020ea6  mov   [rsp+0x30], eax
0x020eaa  jmp   qword [rcx+0x118]      ; tail-call the block function
```

So the minimum oracle is two calls and one allocation: allocate ~0x200 bytes for the AES_KEY, call
`0x20EC0` with the key you want to use (mode 0 to encrypt, nonzero to decrypt), then call `0x20EA0`
with that buffer. Nothing has to be recovered from the process, and nothing is hard-coded to one
captured message — which is what the acceptance criteria ask for.

**`AES_set_encrypt_key` at `0x791F90` and `AES_set_decrypt_key` at `0x791D10`** are the OpenSSL T-table
versions, called from 8 and 4 functions respectively, and they are on the *segment* path rather than
this one. `AES_set_encrypt_key` even appears among its own callers, which is the OpenSSL structure
(the decrypt-key routine calls the encrypt-key one).

**`0x3F4C0` — the second consumer of a context's key field.**

```
0x3f4c0  sub   rsp, 0x38
0x3f4c4  cmp   byte [rcx+0x264], 0      ; the ready byte
0x3f4cb  je    0x3f4ea
0x3f4cd  add   rcx, 0x120               ; the key field
0x3f4d4  mov   dword [rsp+0x28], 1
0x3f4dc  mov   qword [rsp+0x20], 0
0x3f4e5  call  0x20EA0                  ; the trampoline above
0x3f4ea  add   rsp, 0x38
0x3f4ee  ret
```

Called from `0x392A4`. With `hls_decode`, that makes **two and only two** places in the whole `.text`
that use `add rcx,0x120` — the segment-key field has exactly two consumers, and both pass it to the
app's AES. That is worth knowing for ticket 09: the field really is an AES key, and the AES is this one.

**A note on what `+0x120` is.** `hls_decode` passes `ctx+0x120` as the *first* argument to the key
setup, i.e. as the AES_KEY to be filled in place. So the 32 bytes this project has been calling "the
schedule" are the head of an expanded key schedule, which is consistent with the empirical relation
`schedule_to_key` inverts — but it means the field is written **by the AES setup**, expanded from a
key handed to it. Whatever produces that input key is ticket 07's writer, and it is one call away
rather than in the same instruction.

## The trampoline's calling convention, read off its other caller

`0x20EA0` reads its key from `rcx`, so the question was only what the remaining registers hold. The
function `[0x39080, 0x39366)` answers it, and happens to be the segment-decryption routine itself:

```
0x039260  mov   rax, [rdi+0x268]        ; the 32-character XOR mask
0x039267  movzx r8d, byte [rcx+rax]     ; one mask byte
0x03926c  lea   rdx, [rcx+r10]
0x039270  mov   rax, [rdi+0x240]
0x039277  xor   byte [rdx+rax], r8b     ; dest ^= mask[i % 16]
0x03927b  inc   rcx
0x03927e  cmp   rcx, 0x10
0x039282  jl    0x39242                 ; 16 bytes per pass
0x039284  inc   r11
0x039287  add   r10, 0x10
0x03928b  cmp   r11, rbp
0x03928e  jl    0x39240
0x039290  mov   r9d, ebp                ; length
0x039293  mov   r8, [rdi+0x240]         ; output buffer
0x03929a  mov   rdx, [rdi+0x248]        ; input buffer
0x0392a1  mov   rcx, rdi                ; the context
0x0392a4  call  0x3f4c0                 ; -> add rcx,0x120 ; call 0x20EA0
```

That is the whole decryption algorithm in one place, and it confirms the shape this project derived
empirically: mask-XOR first (`ctx+0x268` holds a pointer to the 32-character mask), then AES with the
key at `ctx+0x120`.

So the call is:

```
block(rcx = AES_KEY, rdx = in, r8 = out, r9 = length, [rsp+0x20] = 0, [rsp+0x28] = mode)
```

with `mode` 1 for decrypt and 0 for encrypt, the same flag `hls_decode` passes to the key setup. In
frida that is `new NativeFunction(0x20EA0, 'void', ['pointer','pointer','pointer','size_t','pointer','int'])`.

**What is still inferred rather than read.** The last two arguments are the ones `0x3f4c0` pushes
(`[rsp+0x20] = 0`, `[rsp+0x28] = 1`); the fifth is passed as zero on this path, which is consistent
with ECB and with a null IV, but this ticket establishes ECB from block reuse rather than from these
instructions. A single block run through the oracle against a captured `params` blob will settle it —
that is the acceptance criterion, and it needs the player.
