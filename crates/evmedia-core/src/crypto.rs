//! Segment key material, in one place.
//!
//! There are two ways a key reaches us and they must not be confused, because getting it wrong
//! produces a 16-byte "key" and a total failure with no useful error:
//!
//! * the **live** path (`grab`, `recover`, `capture-ev`) holds a 32-character lowercase hex
//!   *string* which is then used **directly as 32 key bytes** — see [`key_from_text`].
//! * the **manifest** path (`decode-ev`) stores that same string already hex-encoded, so it is
//!   64 characters that must be **decoded** back to 32 bytes — see [`key_from_hex`].
//!
//! [`decrypt`] therefore takes the finished `[u8; 32]` and `[u8; 16]`, and each caller picks
//! the constructor that matches where its key came from.
//!
//! There are also two ways a key is *found*, and they cover for each other. The playback context
//! carries the key in its schedule slot ([`schedule_to_key`]) for as long as the player holds
//! that context — precise, cheap, and gives the index and filename with it. A key outlives its
//! context, though, so once a context is released the key is only a bare 32-hex string loose in
//! the heap, and there it can only be found by testing candidates against segment bytes
//! (`keyscan`). Neither source subsumes the other.

use aes::Aes256;
use anyhow::{bail, Context, Result};
use ecb::cipher::{block_padding::NoPadding, BlockDecryptMut, KeyInit};
use md5::{Digest as _, Md5};
use sha2::Sha256;

/// First four bytes of a context's key schedule before the player has decrypted that segment
/// (`0xBAADF00D` in little-endian). A schedule starting with this is not a key yet.
pub const HEAP_FILL: [u8; 4] = [0x0d, 0xf0, 0xad, 0xba];

fn xtime(value: u8) -> u8 {
    (value << 1) ^ if value & 0x80 != 0 { 0x1b } else { 0 }
}

/// Inverse of the MixColumns step the player applies to the second half of the schedule.
fn mix(input: &[u8]) -> [u8; 16] {
    let mut output = [0u8; 16];
    for base in (0..16).step_by(4) {
        let t = input[base] ^ input[base + 1] ^ input[base + 2] ^ input[base + 3];
        for index in 0..4 {
            output[base + index] =
                input[base + index] ^ t ^ xtime(input[base + index] ^ input[base + (index + 1) % 4]);
        }
    }
    output
}

/// Rebuild the 32-character hex key from a playback context's 32-byte schedule.
///
/// This **is** a key source, and it was wrongly deleted once on the strength of a test that was
/// itself broken. Measured directly against a live player: 333 contexts with a filled slot, 333
/// keys reconstructed, 333 segments opened, 0 failures. The tool that settles it is
/// `tools/parser-tools/probe_schedule_key.py`.
///
/// The player keeps `key[0..16]` verbatim and `key[16..32]` MixColumns'd. Returns `None` while
/// the slot is still heap fill or zeroed, which is how "the player has not decrypted this
/// segment yet" is detected.
pub fn schedule_to_key(schedule: &[u8; 32]) -> Option<String> {
    if schedule[..4] == HEAP_FILL || schedule.iter().all(|byte| *byte == 0) {
        return None;
    }
    let mut key = [0u8; 32];
    key[..16].copy_from_slice(&schedule[..16]);
    key[16..].copy_from_slice(&mix(&schedule[16..]));
    let text = String::from_utf8(key.to_vec()).ok()?;
    is_hex32(&text).then_some(text)
}

/// The third input of the derivation, and the reason a *segment key* needs no player.
///
/// The player computes `MD5_hex(tk + filename + extra)` and gets `extra` from its Bridge layer
/// under an obfuscated name (`m4OEgjo4nU`, `.rdata:0x803550` in `PlayerLibRender56_vs.dll`). That
/// lookup returns this constant. It was read live off a running player with
/// `tools/parser-tools/probe_kdf.py`, and then confirmed against every recording this repository
/// has: 258 segment triples captured in one session, 42 keys rebuilt from playback contexts'
/// schedules, and 56 contexts keyed by the very player that was playing at the time.
pub const DERIVATION_EXTRA: &str = "20220507";

/// Derive a segment key the way the player does: `MD5_hex(tk + filename + extra)`.
///
/// This is the whole offline path. `tk` is the per-segment token in the segment-list response —
/// the same response that carries the signed URL — so no playback, no memory reading and no
/// player are involved. See `docs/KEY-DERIVATION.md`.
pub fn key_from_tk(tk: &str, filename: &str) -> Result<String> {
    if !is_hex32(tk) {
        bail!("segment token is not 32 hex characters");
    }
    Ok(md5_hex(format!("{tk}{filename}{DERIVATION_EXTRA}").as_bytes()))
}

/// Key as it exists in the live path: 32 hex characters used directly as 32 bytes.
pub fn key_from_text(text: &str) -> Result<[u8; 32]> {
    if !is_hex32(text) {
        bail!("live key is not 32 hex characters");
    }
    Ok(*<&[u8; 32]>::try_from(text.as_bytes()).expect("checked length"))
}

/// Key as it exists in a manifest: 64 hex characters decoded back to 32 bytes.
pub fn key_from_hex(text: &str) -> Result<[u8; 32]> {
    let bytes = hex::decode(text).context("key is not hex")?;
    <[u8; 32]>::try_from(bytes.as_slice()).map_err(|_| anyhow::anyhow!("key is not 32 bytes"))
}

/// XOR mask as it exists in the live path: the first 16 ASCII bytes of `MD5(filename)`.
pub fn mask_from_filename(filename: &str) -> [u8; 16] {
    let digest = md5_hex(filename.as_bytes());
    *<&[u8; 16]>::try_from(&digest.as_bytes()[..16]).expect("md5 hex is 32 characters")
}

/// XOR mask as it exists in a manifest: 16 hex-encoded ASCII bytes.
pub fn mask_from_hex(text: &str) -> Result<[u8; 16]> {
    let bytes = hex::decode(text).context("mask is not hex")?;
    <[u8; 16]>::try_from(bytes.as_slice()).map_err(|_| anyhow::anyhow!("mask is not 16 bytes"))
}

/// Unmask and AES-256-ECB decrypt one segment, then verify it lands on 188-byte TS packets.
///
/// This is the leniency the *live* path has always had. `decode-ev` additionally rejects
/// packets whose adaptation-field control bits are invalid; that stricter check deliberately
/// stays in `decode.rs` rather than moving here, because tightening this function would start
/// rejecting segments the live path accepts today.
pub fn decrypt(ciphertext: &[u8], key: &[u8; 32], mask: &[u8; 16], label: &str) -> Result<Vec<u8>> {
    if ciphertext.is_empty() || ciphertext.len() % 16 != 0 {
        bail!("{label} is not AES-block aligned");
    }
    let mut buffer = ciphertext.to_vec();
    for (index, byte) in buffer.iter_mut().enumerate() {
        *byte ^= mask[index % 16];
    }
    ecb::Decryptor::<Aes256>::new_from_slice(key)
        .context("invalid AES-256 key")?
        .decrypt_padded_mut::<NoPadding>(&mut buffer)
        .map_err(|_| anyhow::anyhow!("{label} has invalid AES block data"))?;

    let padding = buffer.len() % 188;
    if padding > 15 || (padding > 0 && buffer[buffer.len() - padding..] != vec![b'#'; padding]) {
        bail!("{label} has invalid '#' padding (wrong key?)");
    }
    if padding > 0 {
        buffer.truncate(buffer.len() - padding);
    }
    if buffer.is_empty() || buffer.len() % 188 != 0 {
        bail!("{label} is not aligned MPEG-TS");
    }
    if buffer.iter().step_by(188).any(|byte| *byte != 0x47) {
        bail!("{label} is not aligned MPEG-TS");
    }
    Ok(buffer)
}

pub fn md5_hex(value: &[u8]) -> String {
    let mut hasher = Md5::new();
    hasher.update(value);
    hex::encode(hasher.finalize())
}

pub fn sha256_hex(value: &[u8]) -> String {
    use sha2::Digest as _;
    hex::encode(Sha256::digest(value))
}

pub fn hex_lower(value: &[u8]) -> String {
    hex::encode(value)
}

pub fn is_hex32(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}
