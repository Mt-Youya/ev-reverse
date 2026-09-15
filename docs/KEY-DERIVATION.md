# The segment-key derivation, as read off PlayerLibRender56_vs.dll

Static, no player required. Everything below is an RVA in
`D:\Learning\EVPlayer2\PlayerLibRender56_vs.dll` (12,828,160 bytes, image base `0x180000000`,
19,488 `.pdata` entries).

The result, in one line:

```
segment key (32 lowercase hex characters) = MD5_hex( tk + filename + "20220507" )
```

`tk` is the segment-list response's `tk` field, `filename` is the segment's `119354-<uuid>.ts`, and
the third input is a **constant**. All three are knowable without a player: the list response
carries `tk` next to the signed URL it belongs to, which is what makes `evmedia derive` possible
and what retires the "one playback pass per lesson" limit this project was built around.

## How the third input was caught

It is not in any file — `20220507` appears nowhere in the DLL, Bridge.dll, MetaLib.dll or the
executable — because the player's Bridge layer returns it for an obfuscated name. So it was read
off a running player instead:

```
python tools\parser-tools\probe_kdf.py --pid <pid> --seconds 60
  md5_input = 251a42ee1984284be5f7a11a191c5369119354-de1ed8af-40d3-491f-8d12-f0e759280190.ts20220507
    tk      = 251a42ee1984284be5f7a11a191c5369
    file    = 119354-de1ed8af-40d3-491f-8d12-f0e759280190.ts
    EXTRA   = '20220507'
```

`0x1FD60` is the `std::string = MD5_hex(input)` wrapper, and its second argument is the whole
concatenation, so one hook yields `extra` for every segment the player decrypts. Six derivations
in sixty seconds, all with the same third input.

Then, against data that predates the hook:

| Evidence | How those keys were obtained | Result |
| --- | --- | --- |
| 258 triples (`ev2_out/keys.json` × `manifests.capture`) | harvested by the earlier pipeline, a different session | 258/258 |
| 42 triples (`ctx_dump.json`) | rebuilt from each context's AES schedule | 42/42 |
| `triple.json` | context schedule and manifest entry read at the same moment | match |
| 56 contexts of the player that was playing | rebuilt from its live schedules while it played | 56/56 |

`crates/evmedia-core/tests/derivation.rs` pins thirteen of those triples, and refuses to pass if the
extra is dropped.

## The offline path, end to end

```
evmedia derive --playlist list.json --input <segments> --output manifest.json
evmedia decode-ev <segments> manifest.json lesson.ts
```

Verified on a real lesson: a list response captured from the wire, the ciphertext already in the
player's download directory, no player running and no memory read. The merge came out as 18,854
MPEG-TS packets with 18,854 sync bytes, and `ffprobe` reads H.264 2992×1682 plus AAC from it.
`tools/parser-tools/cached_lessons.py` reports which captured lists a download directory can
already satisfy.

### What those checks do not prove, and what does

The paragraph above was read as "the export works" for a while, and it is not enough to say that.
**Sync bytes and `ffprobe` metadata are satisfied by a decryption that produces structurally valid
but visually worthless video.** Measured: decode `verify_out5/lesson.ts` and `verify_out4/
live2_lesson.ts` all the way through instead of probing them, and both report ~1,500 decoder
errors; extract frames and they are flat grey with faint vertical banding, which is the decoder's
error concealment, not a picture.

What the layers actually check out as, on a decrypted segment:

| Check | Result |
| --- | --- |
| TS sync byte at every 188-byte boundary | 100% |
| NAL structure (AUD, SPS 26 B, PPS 6 B, IDR slice 74,224 B) | intact |
| Stream parameters (`ffprobe`) | H.264 High@4.0, yuv420p, 1920×1080, 30 fps |
| **Audio decode** | **clean** (8 warnings over the same file) |
| **Video decode** | **1,874 error lines, frames grey** |

The audio is the point: it travels in the same ciphertext, on its own PID, and decodes cleanly. A
wrong key or a wrong mask would destroy it along with the transport headers. So the AES-256-ECB
layer and the `MD5(filename)[:16]` mask are right, and **the video slice payloads carry a second
scrambling that this project has not undone** — which is what the DLL's `DecryptFilterMgr`,
`DecryptFilter@ev` and `release DecryptFilter start!` strings describe, and why the player can
render a lesson whose segments, decrypted this way, decode to grey.

Until that filter is understood, the honest status of the offline path is: keys, ordering and the
container are solved; the picture is not. Frames are the only oracle worth trusting —
`ffmpeg -v error -i out.ts -f null -` and an extracted PNG say more than any sync count.


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

The candidates below were tested *before* the value was caught live, and they are kept here because
they are the reason the answer had to come from the player rather than from a capture: nothing on
the wire is the third input, and no amount of guessing the shape finds a value that is not there.

| Check | Data | Result |
| --- | --- | --- |
| `key = MD5_hex(tk + filename + "20220507")` | 258 + 42 + 1 + 56 triples, four independent sources | all of them |
| Mask = `MD5(filename)[:16]` | `ctx_dump.json`, 50 contexts | 50/50 |
| `key = MD5_hex(tk + filename)` and 7 variants (`+sign`, `+t`, `+sid`, `+bid`, `+v`, `+idx`, `+sf`, `+d_p`) | 258 same-session triples | 0/258 each |
| Permutations of `{tk, file, sf, path, sign, t, bid, sid, v}` in 2- and 3-part orders with 8 separators | `triple.json` | 0 hits |
| `extra` drawn from `captured/keys.txt` (2,825 strings harvested from memory), in 4 orders | 258 triples | 0 hits |
| `extra` a short constant: hex ≤ 6, alphanumeric ≤ 4, digits ≤ 5 | one triple, then the rest | 0 hits (it is 8 digits, in a different position) |

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

Nothing blocks a key anymore. What remains is plumbing rather than reverse engineering:

1. **Fetching the list without the player.** `derive` consumes a list that something else obtained.
   The player gets it from `en2v4.ieway.cn` with a bearer token, and every response body on that
   wire is encrypted (`docs/API.md`) — so a tool that fetches lists for itself still needs the
   response decryption, which is ticket 06. Until then the list comes from
   `tools/parser-tools/capture_all.py`, which reads it inside the player.
2. **The other Bridge values.** The same mechanism returns a 32-character value that `0x42A30`
   uses as an AES key for an API response. That value is a constant too, and the hook that caught
   `20220507` catches it the same way — which is the shortest route to closing ticket 06.
