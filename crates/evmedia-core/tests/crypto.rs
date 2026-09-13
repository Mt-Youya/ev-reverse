//! The decryption path, and the trap sitting in the middle of it.
//!
//! There are two ways a key arrives — as 32 hex characters used directly as 32 bytes (live), and
//! as 64 hex characters to be decoded (manifest). Confusing them yields a 16-byte key and a
//! failure with no useful message, so both forms are pinned here.

use aes::Aes256;
use ecb::cipher::{block_padding::NoPadding, BlockEncryptMut, KeyInit};
use evmedia_core::crypto::{
    decrypt, hex_lower, key_from_hex, key_from_text, mask_from_filename, mask_from_hex, md5_hex,
    schedule_to_key, sha256_hex, HEAP_FILL,
};

/// 188 bytes per packet is the only size that survives the alignment check, and four packets is
/// also a whole number of AES blocks, so a round trip needs no '#' padding.
const PACKETS: usize = 4;

fn synthetic_ts() -> Vec<u8> {
    let mut data = vec![0u8; 188 * PACKETS];
    for packet in 0..PACKETS {
        let base = packet * 188;
        data[base] = 0x47;
        data[base + 3] = 0x10; // adaptation field present, payload present
        for offset in 4..188 {
            data[base + offset] = ((packet * 7 + offset) % 251) as u8;
        }
    }
    data
}

/// The inverse of [`decrypt`]: AES first, then the mask.
fn encrypt(plain: &[u8], key: &[u8; 32], mask: &[u8; 16]) -> Vec<u8> {
    let mut buffer = plain.to_vec();
    let length = plain.len();
    ecb::Encryptor::<Aes256>::new_from_slice(key)
        .unwrap()
        .encrypt_padded_mut::<NoPadding>(&mut buffer, length)
        .unwrap();
    for (index, byte) in buffer.iter_mut().enumerate() {
        *byte ^= mask[index % 16];
    }
    buffer
}

const KEY_TEXT: &str = "0123456789abcdef0123456789abcdef";
const FILENAME: &str = "119354-0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.ts";

#[test]
fn decrypt_reverses_encryption_exactly() {
    let plain = synthetic_ts();
    let key = key_from_text(KEY_TEXT).unwrap();
    let mask = mask_from_filename(FILENAME);
    let cipher = encrypt(&plain, &key, &mask);
    assert_ne!(cipher, plain);
    let recovered = decrypt(&cipher, &key, &mask, FILENAME).unwrap();
    assert_eq!(recovered, plain);
}

#[test]
fn the_two_key_representations_are_not_interchangeable() {
    let live = key_from_text(KEY_TEXT).unwrap();
    // Live form: 32 hex characters used verbatim as the 32 key bytes.
    assert_eq!(&live[..], KEY_TEXT.as_bytes());

    // Manifest form: those same 32 bytes, hex-encoded to 64 characters.
    let stored = hex_lower(&live);
    assert_eq!(stored.len(), 64);
    assert_eq!(key_from_hex(&stored).unwrap(), live);

    // Feeding the manifest form to the live constructor must fail rather than silently
    // producing half a key — this is the mistake the two constructors exist to prevent.
    assert!(key_from_text(&stored).is_err());
    assert!(key_from_hex(KEY_TEXT).is_err());
}

#[test]
fn the_mask_is_the_first_sixteen_ascii_bytes_of_the_filename_digest() {
    let mask = mask_from_filename(FILENAME);
    assert_eq!(&mask[..], &md5_hex(FILENAME.as_bytes()).as_bytes()[..16]);
    // The manifest stores that ASCII as hex, so the round trip must hold.
    assert_eq!(mask_from_hex(&hex_lower(&mask)).unwrap(), mask);
}

#[test]
fn a_context_that_has_not_been_decrypted_yet_has_no_key() {
    let mut untouched = [0u8; 32];
    untouched[..4].copy_from_slice(&HEAP_FILL);
    assert_eq!(schedule_to_key(&untouched), None, "heap fill must not read as a key");
    assert_eq!(schedule_to_key(&[0u8; 32]), None, "an empty schedule is not a key");
}

#[test]
fn schedule_to_key_never_returns_anything_but_a_live_key() {
    // A schedule that is not in the player's format must be rejected, not turned into a key
    // that fails later with a decryption error that points at the wrong place.
    let mut state = 0x1234_5678_9abc_def0u64;
    for _ in 0..512 {
        let mut schedule = [0u8; 32];
        for byte in schedule.iter_mut() {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            *byte = (state & 0xff) as u8;
        }
        if let Some(key) = schedule_to_key(&schedule) {
            assert_eq!(key.len(), 32);
            assert!(key.bytes().all(|byte| byte.is_ascii_hexdigit()));
            assert_eq!(&key.as_bytes()[..16], &schedule[..16]);
        }
    }
}

#[test]
fn decrypt_rejects_data_that_is_not_a_whole_number_of_aes_blocks() {
    let key = key_from_text(KEY_TEXT).unwrap();
    let mask = mask_from_filename(FILENAME);
    assert!(decrypt(&[], &key, &mask, FILENAME).is_err());
    assert!(decrypt(&[0u8; 15], &key, &mask, FILENAME).is_err());
}

#[test]
fn decrypt_rejects_the_wrong_key_instead_of_returning_noise() {
    let plain = synthetic_ts();
    let mask = mask_from_filename(FILENAME);
    let cipher = encrypt(&plain, &key_from_text(KEY_TEXT).unwrap(), &mask);
    let wrong = key_from_text("fedcba9876543210fedcba9876543210").unwrap();
    assert!(decrypt(&cipher, &wrong, &mask, FILENAME).is_err());
}

#[test]
fn decrypt_strips_hash_padding() {
    // Five packets: 940 bytes, which is not a multiple of 16, so the producer pads with '#'.
    let mut plain = vec![0u8; 188 * 5];
    for packet in 0..5 {
        plain[packet * 188] = 0x47;
        plain[packet * 188 + 3] = 0x10;
    }
    let padded_to = plain.len().div_ceil(16) * 16;
    let padding = padded_to - plain.len();
    let mut padded = plain.clone();
    padded.extend(std::iter::repeat(b'#').take(padding));

    let key = key_from_text(KEY_TEXT).unwrap();
    let mask = mask_from_filename(FILENAME);
    let cipher = encrypt(&padded, &key, &mask);
    let recovered = decrypt(&cipher, &key, &mask, FILENAME).unwrap();
    assert_eq!(recovered, plain);
    assert_eq!(recovered.len(), 188 * 5);
}

#[test]
fn hashing_helpers_agree_with_their_own_definitions() {
    assert_eq!(md5_hex(b""), "d41d8cd98f00b204e9800998ecf8427e");
    assert_eq!(
        sha256_hex(b""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
    assert_eq!(hex_lower(&[0x00, 0xab, 0xff]), "00abff");
}
