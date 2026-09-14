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
`captured/inflated/`, and it is also the reason `getDownEVSKey` (ticket 08) has never been read:
its plaintext is not inflated, so no hook has stood between the two steps yet.

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

* `tk` and `idx` are on the wire; the *segment key* is not, and cannot be computed from what is.
* The unreadable responses are unreadable for one reason: the player's own AES is applied before
  anything hooks. Ticket 06's oracle exists for exactly this, and the same key material appears to
  come from a Bridge lookup (`0x42A30` uses one of those values as an AES key), which is the one
  thing standing between a capture and every response in it.
