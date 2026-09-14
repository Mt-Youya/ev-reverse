# The segment-key derivation, as read off PlayerLibRender56_vs.dll

Static, no player required. Everything below is an RVA in
`D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll` (12,828,160 bytes, image base `0x180000000`,
19,488 `.pdata` entries).

The result, in one line:

```
segment key (32 lowercase hex characters) = MD5_hex( tk + filename + extra )
```

`tk` and `filename` are both visible on the wire — `tk` is the M3U8-list response's `tk` field, and
`filename` is the segment's `119354-<uuid>.ts`. **`extra` is not.** It is read out of a Bridge
interface inside the player at the moment the segment is decrypted, which is the whole reason the
key cannot be computed from a capture.

## The three functions

| RVA | What it is | How it was confirmed |
| --- | --- | --- |
| `0x40AF0` | `hls_decode`'s AES init: `(rcx = ctx, rdx = std::string key)` | Its own error string is `hls_decode url:%s  aes init failed %s` (`0x803330`), printed with the identity (`ctx+0x18`) and the key string |
| `0x20EC0` | AES key setup, called by `0x40AF0` as `(rcx = ctx+0x120, rdx = key, r8d = 0x100, r9d = 1)` | `r8d = 0x100` is 256 bits; the destination is the schedule slot `evmedia-core::crypto` reads |
| `0x42660` | The key function: `(rcx = manager, rdx = identity)` | It is the only caller of `0x1FD60`, and it hands the result to `0x40AF0` |

`0x40AF0` does more than set the key, and the extra work is what makes it recognisable:

```
cmp  byte ptr [rcx+0x264], 0   ; "already initialised" — return if set
add  rcx, 0x120                ; the schedule slot
mov  r8d, 0x100                ; AES-256
mov  r9d, 1
call 0x20EC0                   ; AES_set_encrypt_key
... malloc 0x40000 -> ctx+0x240, 0x200 -> ctx+0x248, 0x200 -> ctx+0x250
mov  byte ptr [rdi+0x264], 1
```

That is a direct confirmation of two things `evmedia-win` already assumes: the schedule is at
`ctx+0x120`, and `ctx+0x264` is the "the player has decrypted this segment" flag. It also confirms
the schedule is an **AES-256 key schedule of the 32-character key string**, which is why
`crypto::schedule_to_key` has to undo one MixColumns step on the second half.

## `0x42660`, the key function

```
map lookup       manager+0x2A8 is a std::map<identity, context*>, guarded by a pthread mutex at
                 manager+0x320
bail             cmp byte [ctx+0x264], 0 / jne  ->  return, already done
virtual call     ctx->vtable[1]  (vtable 0x803310, slot 1 = 0x9AC0 = `xor al, al; ret`)
                 it *always returns false*, so the MD5 branch below is the only live one
build S          S  = ctx+0x288 (std::string, the tk) + ctx+0x18 (std::string, the filename)
                 via 0x21A30, whose two memcpys fix the order: tk first, then filename
append           std::string::append(S, value)   (0x7A80)   <- the runtime "extra"
hash             T  = MD5_hex(S)                 (0x1FD60)
key              AES setup with T                (0x40AF0)
```

`0x1FD60` really is MD5-hex, and it is worth naming the evidence because it is the one step that
could have been something else: `0x1D810` loads `0x67452301`, `0xEFCDAB89`, `0x98BADCFE`,
`0x10325476` into the state, calls `0x1E220` (update) and `0x1D6F0` (final), and `0x1D8A0` walks the
16 digest bytes formatting each with `"%02x"` — 32 lowercase hex characters, which is exactly the
*stored* key form.

The two callers of `0x42660` are `0x42590` (a wrapper that then calls `0x3F330`) and `0x44790`,
whose failure path prints `open_ts_ctx %s not found !` (`0x803640`).

## Where `extra` comes from, and why it is the whole problem

Every "extra" in this binary is fetched the same way, 415 times over:

```
iface = *(void**)0xBF26C8                     ; the bg::Interface* array
obj   = *(void**)(iface + 0x78)
obj->vtable[+0xD0](&out_std_string, pool_name_utf16, byte_len)   ; name -> something
obj->vtable[+0x40](&out2, bg::Value(out), 0xA6)                  ; -> the value
value = *(char**)(out2 + 0x30)
```

* `0xBF26C8` is filled by `Bridge.dll!?__Init_CD_Later__@@YAHPEAPEAVInterface@bg@@@Z`, called from
  `0x1080`. So the lookups are served by a **Bridge module interface**, not by the player's own
  code, and `0xA6` (166) is a constant on every one of the 415 call sites.
* `pool_name_utf16` is a UTF-16 **slice of a string pool in `.rdata` starting at `0x8034A0`**. The
  key path uses offset `0x803550`, length `0x14` (20 bytes = 10 characters): `"m4OEgjo4nU"`.
  `0x42A30`, the sibling function, uses `0x8034A0` with length `0x20` and passes the result to
  `0x1EA10` as an **AES key for decrypting an API response** (failure string: `dec error !`).
* Those names are not plain text: `"m4OEgj"` recurs at 8-character boundaries through the pool, and
  the pool decodes to non-ASCII. Whether the pool is obfuscated or the names are simply opaque is
  **not settled** — and it does not have to be settled to answer the question that matters, because
  either way the *value* comes from a module inside the player.

## What was measured against real data

| Check | Data | Result |
| --- | --- | --- |
| Mask = `MD5(filename)[:16]` | `ctx_dump.json`, 50 contexts | 50/50 |
| `key = MD5_hex(tk + filename)` and 7 variants (`+sign`, `+t`, `+sid`, `+bid`, `+v`, `+idx`, `+sf`, `+d_p`) | 258 same-session `(tk, filename, key)` triples | 0/258 each |
| Permutations of `{tk, file, sf, path, sign, t, bid, sid, v}` in 2- and 3-part orders with 8 separators | `triple.json`, one simultaneously-read record | 0 hits |
| `extra` drawn from `captured/keys.txt` (2,825 strings harvested from the player's memory), in 4 orders | 258 triples | 0 hits |
| `extra` a short constant: hex ≤ 6, alphanumeric ≤ 4, digits ≤ 5 | 1 triple, then verified across the rest | 0 hits |

So `extra` is neither a wire field nor a short constant nor anything that happens to be lying
around as a 32-hex string in memory. It is produced on demand by the module.

## Reproducing any of this

```powershell
$py = "C:\Users\Yonjay\.conda\envs\subgen\python.exe"
$dll = "D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll"

& $py -u tools\parser-tools\static_xref.py $dll callers 0x20ec0        # who sets an AES key
& $py -u tools\parser-tools\static_xref.py $dll callers 0x42660        # who computes one
& $py -u tools\parser-tools\annotate.py   $dll 0x42660 0x42a21         # the derivation, annotated
& $py -u tools\parser-tools\annotate.py   $dll 0x40af0 0x40bbd         # the hls_decode AES init
& $py -u tools\parser-tools\field_refs.py $dll 0x288                   # who writes the tk slot
& $py -u tools\parser-tools\field_refs.py $dll --rip-target 0xbf26c8   # who uses the interface array
& $py -u tools\parser-tools\bridge_values.py    $dll                   # all 415 pool lookups
& $py -u tools\parser-tools\registrations.py    $dll                   # what those ids register
& $py -u tools\parser-tools\key_formula.py tools\parser-tools\ctx_dump.json
& $py -u tools\parser-tools\pair_capture.py
& $py -u tools\parser-tools\search_third.py
```

`field_refs.py` survives undecodable bytes by resuming one byte later; a linear sweep that stops at
the first one reports zero references to a field that is referenced, which is how a "nothing writes
this" conclusion gets made by accident.

## What is left

One question, sharply stated: **what does the module behind `vtable[+0x40](..., 0xA6)` return for
the name at `0x803550`?** Three ways in, none of them requiring a full playback:

1. Find the `bg::Interface` implementation that PlayerLib registers (its constructor stores a
   vtable; `0xBF26C8` is the array `__Init_CD_Later__` fills) and read the getter. Start from
   `Bridge.dll!?__Init_CD_Later__@@YAHPEAPEAVInterface@bg@@@Z` at `0x38E0`, which stores its first
   entry from `Bridge.dll:0x71818`; note that slot holds two 32-bit RVAs (`0x5BEA4`, `0x228B0`)
   rather than a relocated pointer, so the "first interface" is assembled at runtime and the
   `+0x78` member behind the lookup is not in the file.
2. Decode the pool. If the names are obfuscated rather than opaque, the decode routine is in the
   module and the `0x42A30` path — where the value is used as an AES key for a captured response —
   is a checkable oracle for it.
3. Watch the lookup once with the player running (`probe_at.py` on the `+0xD0` target) and read the
   value directly. One segment is enough; this does not need the whole lesson.

Until one of those lands, ticket 10's answer stands as: **there is no offline path to a segment
key**, and the cost of a lesson is one playback pass.
