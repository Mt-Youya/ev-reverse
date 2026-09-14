# evmedia

Capturing course lessons from a running EVPlayer2 player on Windows: reading the keys it holds
in memory, downloading the encrypted segments, decrypting them, and producing a playable file.

## Language

### The media being captured

**Lesson**:
One video of a course — the unit that is captured, decrypted and merged into a single file.
_Avoid_: video, course, chapter, 课件

**Segment**:
One encrypted `.ts` chunk of a lesson, identified by a filename and by a zero-based index that
gives its position in the lesson.
_Avoid_: part, chunk, block, 分片（在代码与文件名里用 segment）

**Lesson index**:
A segment's zero-based position within its lesson. Contiguous `0..n-1` for a lesson that is
fully captured, and the only ordering the merge trusts.
_Avoid_: position, order, seq

### Where the keys come from

**Segment key**:
The 32-character lowercase hex string that opens one segment. It is not derived from anything
locally visible — it exists only after the player decrypts that segment for playback.
_Avoid_: salt, password, secret

**Playback context**:
The player's per-segment object, holding the lesson index, the segment filename, and the AES
schedule. Finding these is how the tool learns what the player can currently decrypt.
_Avoid_: session, record, entry

**Schedule**:
The 32-byte slot in a playback context from which the segment key is recovered. It reads
`0xBAADF00D` until the player has decrypted that segment, so it doubles as a liveness flag.
_Avoid_: state, keystream, round keys

**Key window**:
The contiguous run of lesson indexes the player holds decrypted at one moment. It starts at the
playhead and extends ahead of it, which is why it can be read as a playhead position.
_Avoid_: cache, buffer

**Gap**:
A lesson index that is not decrypted yet. A gap *ahead of* the playhead fills itself as
playback proceeds; a gap *behind* the playhead never will.
_Avoid_: hole, missing, hole in the lesson

**Extra**:
The third input of the key derivation, after the *tk* and the filename. The player reads it from a
Bridge interface at the moment it decrypts a segment; it is on no wire and in no file, which is why a
*segment key* cannot be computed from a capture. See `docs/KEY-DERIVATION.md`.
_Avoid_: salt, param, runtime parameter

### Getting the keys

**Harvest**:
The loop that polls the player for keys and URLs, joins them on filename, then downloads and
decrypts whatever is new.
_Avoid_: grab, capture, scrape, scan

**Sweep**:
Driving the player's playhead backwards over a gap behind it, so its read-ahead decrypts that
gap again. The only way to fill such a gap.
_Avoid_: seek, scrub, rewind, 自动扫

**Stall**:
A run of polls during which no new segment was decrypted. A stall is what triggers a sweep.
_Avoid_: idle, timeout, hang

### Representations worth keeping apart

**Live key form**:
32 hex characters used directly as 32 key bytes. This is what the player's schedule yields.
_Avoid_: raw key, text key

**Stored key form**:
The live key form hex-encoded to 64 characters, as it appears in a manifest. Decoding it is not
optional: treating it as 32 bytes yields half a key and a failure that points nowhere.
_Avoid_: manifest key, hex key

**Manifest**:
An ordered list of a lesson's segments together with the material needed to decrypt each one.
_Avoid_: index, catalog, playload

**Capture**:
Writing a manifest for a lesson from a live player (`capture-ev`). Distinct from harvest, which
downloads and decrypts rather than describing.
_Avoid_: dump, export, extract

### The result

**Merge**:
Concatenating a lesson's decrypted segments in index order.
_Avoid_: join, combine, concat

**Complete merge**:
A merge whose indexes form an unbroken `0..n-1` run. It is written to `lesson.ts`; anything else
is written to `lesson.partial.ts`. The filename is a promise to the caller.
_Avoid_: full, finished, successful


下载的 7537 个加密文件,硬链接进 enc 目录(不占额外磁盘),grab 直接复用它们解密,一个字节都不重新下载。
解密公式本身早就有了:明文 = AES-256-ECB解密(密文 XOR MD5(文件名)前16字节),密钥是 32 个十六进制字符。这个项目整个 Rust 链路(grab→解密→合并→转 MP4)就是干这个的,现在正跑着。
唯一绕不过去的硬约束:每段的密钥和播放顺序(index)只存在于播放器的内存里——密钥是播放器解密那一刻才生成、顺序是播放器 context 里的一个 u32。文件名是随机 UUID,不含顺序;加密内容里也读不出顺序。所以离线(不播放)拿不到,必须让播放器把这些段解出来,grab 从内存抓。
