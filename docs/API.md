# The EVPlayer2 API, as captured

Every fact here comes from `tools/parser-tools/captured/`: `bodies.bin` (317 request bodies and 317
response bodies, split by `tools/parser-tools/api_summary.py` and `extract_bodies.py`), `urls.log`
and `events.jsonl` (the same traffic as an event stream), and `inflated/` (103 responses the player
itself decompressed, caught by hooking its inflate call).

Two hosts, and only one of them is an API:

| Host | What it serves | Transport |
| --- | --- | --- |
| `en2.ieway.cn`, `en2v4.ieway.cn` | `/student/<method>` — the account, authority and key API | HTTP/2 POST, JSON body, `Authorization: Bearer <JWT>` |
| `cn28027.evplayer.cn` | `<uuid>/119354-<uuid>.ts?bid=&sid=&t=&v=&sign=` — the segments | plain HTTP GET, no auth header, URL-signed |

## Endpoints seen

| Endpoint | Captured | What it answers |
| --- | --- | --- |
| `getPlayTimeKeySignEVS20260515` | 238 | the per-playback key signature |
| `getPlayTimeKeySignEVS20231103` | 30 | the same, older protocol version |
| `getEvsSignUrl` | 20 | a signed URL for one `.evs` file |
| `getDownEVSKey` | 12 | the key for a downloaded `.evs` |
| `getPlaySubtitle` | 8 | subtitles |
| `getPlayAuthorityEVS` | 8 | the right to play a lesson |
| `getEvsAuthorityCourse` | 1 | the course's authority list |

Two versions of the same method is not redundancy: the player calls `…20260515` in normal
playback and `…20231103` for the older flow, and the captured counts follow the player's own
choice (238 against 30).

### The whole method table, read out of the running process

The capture above is only what was exercised. The player's method table is not in any file as
text — `endpoints_by_caller.py` scans for `/student/…` literals and finds **zero** — but it is in
memory once the player has started, and `dump_endpoints.py` reads it there. All nine paths sit as
plain ASCII in `EVPlayer2.exe` at `+0x484300`…`+0x489100`:

| Endpoint | In the captures? |
| --- | --- |
| `/student/getPlayTimeKeySignEVS20260515` | yes, 238 |
| `/student/getDownEVSKey` | yes, 12 |
| `/student/getPlaySubtitle` | yes, 8 |
| `/student/getEvsAuthorityCourse`, `/student/getEvsSignUrl`, `/student/getPlayAuthorityEVS` | yes |
| `/student/getEvsSignUrlByKey?key=` | no |
| `/student/getCourseDetailPreView` | no |
| `/student/searchCourseVideos` | no |
| `/student/playReport` | no |
| `/student/errorReport`, `/student/feedBackV2` | no |

The same paths also exist inside `PlayerLibRender56_vs.dll`, but not as text: they are UTF-16
copies of base64 blobs of the form `m4OEgjp…`, 296 of them, each decoding to a header
`9b 83 84 82 3a` followed by a payload that is neither AES (not block-aligned) nor a plain XOR
against any constant this project knows. That is why a *static* scan reports no endpoints while
the process reports nine. Only the DLL's copy of `…20231103` was ever seen decrypted in memory.

## The envelope, and why almost nothing is readable

Every response is the same shape:

```json
{"errcode": 0, "errmsg": "", "uuid": "<uuid>", "zip": 1, "encrypt": 1, "result": "<base64>"}
```

`encrypt` was **1 on all 317 responses captured**, and `zip` was 1 on 270 of them. The `result` is
base64, and behind the base64 there is ciphertext — `api_summary.py` decodes all 317 and reports
that not one of them reaches UTF-8 or zlib:

```
envelope flags seen: {'zip=1 encrypt=1': 270, 'zip=0 encrypt=1': 47}
en2.ieway.cn/student/getDownEVSKey        12  zip=0 encrypt=1  base64 only
en2v4.ieway.cn/student/getPlayTimeKeySign… 238 zip varies     base64 only
...
```

So a packet capture of this player reads **nothing** on its own, and that is a property of the
envelope rather than of any one endpoint. What made the catalog and the segment lists readable was
hooking `inflate` inside the process (`capture_all.py`) — the player decrypts a response and then
inflates it, and the hook catches the plaintext between the two steps. That is the origin of
`captured/inflated/`.

## The scheme, read off the decryption itself

A proxy cannot answer this one. The response body is encrypted *before* it reaches the socket, so a
proxy — even a trusted-certificate MITM — hands back the same ciphertext the capture already holds.
The plaintext exists for one instruction inside the process, and `0x1EA10` is it: five call sites,
one per family of endpoint, each asking the Bridge layer for a key first.
`tools/parser-tools/capture_plaintext.py` hooks that function and records, for every response, the
base64 input, the key and the plaintext.

What comes out:

```
result (base64)  ->  AES-128-ECB  ->  gzip  ->  JSON
                        ^
                        |
   per-request key, delivered in a request descriptor that is itself encrypted:
   {"host": "https://en2v4.ieway.cn", "req": "/student/getPlayTimeKeySignEVS20260515",
    "dkey": "x!@#y.cn_xnk0506", "dkey_ver": 202, "cache_key": "...", "base_key": "9299133a...",
    "ext": ".evs", "tiku": 0, "water_ts": "", "evc_val": 0}
   that descriptor decrypts under a second constant, "11585ec1b1f8f30e"
```

* **ECB, not CBC.** Measured, not assumed: against a live `(ciphertext, key, plaintext)` triple,
  ECB reproduces all 500 bytes and CBC diverges at byte 16. The decrypted buffer is a fixed-size
  allocation with a stale tail, so `gzip.decompress` on the whole thing fails a few hundred bytes
  in — which reads exactly like a wrong key.
* **The data key rotates with `dkey_ver`.** Today's is `x!@#y.cn_xnk0506` (ver 202) and it opens
  the segment lists. The responses captured on 2026-09-13 do **not** open under it, which is why
  `captured/bodies.bin` still holds unread envelopes: those were taken under an earlier key, and
  the list payloads from that session were read through the inflate hook instead.
* `tools/parser-tools/decrypt_response.py` decrypts what today's keys can, offline, and reports per
  endpoint which key worked.
* **Descriptors arrive over the network, not from disk.** `find_descriptors.py` walks a directory
  for base64 blobs that open into one; the player's install directory (110 files), its
  `%LOCALAPPDATA%\EVPlayer2` (5) and its download directory (59 non-segment files) hold **none**.
  That is why a capture taken under an older `dkey_ver` cannot be opened afterwards: the descriptor
  that named its key was traffic, and what was captured was that traffic — encrypted.

Per-call-site table, as caught live:

| Call site | Key | Payload |
| --- | --- | --- |
| `0x0283F2` (inside `0x26730`) | `x!@#y.cn_xnk0506` | gzip → the segment list (`d_p`, `k_l`) |
| `0x03B435` (inside `0x3B2B0`) | `11585ec1b1f8f30e` | the request descriptor |
| `0x042C49` (inside `0x42A30`) | `11585ec1b1f8f30e` | the request descriptor |

`0x1B480` and `0x34390`, the remaining two call sites, have not fired in any session observed so
far, which is why the endpoints behind them are still unread. `0x1B480` is not an endpoint handler
at all: it is a **method of the crypto object** (`.rdata:0x801438`, a vtable sitting next to
`[aes …]` and `key length must be 16 or 24 or 32`), so it will fire for whatever flow decrypts
through that object rather than for one endpoint.

`0x34390` is a two-instruction wrapper — it calls `0x1EA10` from its first address — and it has
four callers, at `0x31E43`, `0x32474`, `0x36E18` and `0x374DB`. Those two functions reference
`json_params` and `wdisklist` and eight of the encrypted `m4OEgjp…` strings, so the flow behind
them builds its request around a *disk* list, which is the offline/download side of the player
(`getDownEVSKey`, and the "already downloaded" view) rather than playback. Ten minutes of the
player fetching segment lists fired neither site: those twenty-four decryptions were all the
segment list, all through `0x0283F2`.

## Signing a request

Every request carries `sign` and the server checks it — a wrong one is refused with `签名错误`.
The preimage is **not** in the request: it is the fields, then `&&`, then a secret the player asks
its Bridge layer for by an obfuscated name. Caught live at `0x1FD60`:

```
app_version=5.0.5&evs_playkey=…&need_zip=1&os_name=windows&platform=1&platform_type=1
&req_time=…&ts_liststr=…&type=0&&ieway.cn@20200611
```

so `sign = MD5(that)` — every field except `sign`, in name order, plus the tail. It reproduces all
**238** request signatures in `captured/bodies/`. Two things made it hard to see, and both are worth
keeping: the first capture truncated the preimage at 512 characters, in the middle of `ts_liststr`;
and a rebuild from the request *without* the secret looks entirely plausible and matches nothing at
all — which is why 16,863 combinations of fields, orders and guessed secrets found no fit.

`evmedia fetch` implements the whole request — body, signature, and the reply's decryption.
Verified against the live server and then end to end: the signed list it returned was downloaded,
its `tk`s became keys through `evmedia derive`, and the merge came out as 10,284 MPEG-TS packets
with 10,284 sync bytes. **The player was not involved beyond supplying one token and one play key.**


## The one response shape that matters for a capture

The segment list — a `z-inflate` product — is what ties a file to a key:

```json
{"d_p": "http://cn28027.evplayer.cn/<uuid>",
 "k_l": [{"idx": 0,
          "sf": "/119354-<uuid>.ts?bid=119354&sid=1113723&t=6aa6975c&v=2.0&sign=<32 hex>",
          "tk": "<32 hex>"}, ...]}
```

* `idx` is the segment's *lesson index* — the ordering the merge trusts.
* `sf` is the path half of the signed URL; the host is `d_p`.
* `tk` is the per-segment token. It is stable within a lesson in every capture that was checked
  (268 filenames, no filename with two different `tk`), and it becomes the playback context's
  `+0x288` field, which is the *first* input of the key derivation
  ([`KEY-DERIVATION.md`](KEY-DERIVATION.md)).
* `bid`/`sid` are the course and lesson ids, `t` a timestamp, `v` the protocol version, `sign` the
  server's signature over the URL. **None of them is the key**, and none of them is the third
  input of the derivation either — that was measured on 258 real triples.

## What this leaves

* `tk` and `idx` are on the wire, and the segment key is *derived* from them rather than sent:
  `key = MD5_hex(tk + filename + "20220507")`, verified on every triple that was caught
  ([`KEY-DERIVATION.md`](KEY-DERIVATION.md)). Nothing has to be played and nothing has to be
  hooked: `evmedia fetch` signs the request, `derive` computes the keys, and the export runs with
  the player closed.
* The responses that were unreadable when captured stay unreadable for one reason only: their
  key was named by a descriptor that arrived as traffic under a `dkey_ver` that has since rotated.
  Nothing on disk holds a descriptor — the install directory, `%LOCALAPPDATA%\EVPlayer2` and the
  download directory were all searched — so an archived capture cannot be reopened later.
* `0x1B480` and `0x34390` are the only decryption call sites never observed firing; a probe is
  armed for them (`capture_keys.py`), and what they serve is the one question the list-derived
  export does not need answered.
