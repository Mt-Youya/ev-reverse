//! The offline key derivation, pinned against keys that were obtained without it.
//!
//! The player computes a segment key as `MD5_hex(tk + filename + extra)`. `tk` and the filename
//! come from the segment-list response; `extra` is a constant. Every key in
//! `derivation_triples.json` was obtained a different way — rebuilt from a live playback
//! context's AES schedule, or harvested by an older pipeline that tested candidates against
//! ciphertext — so agreement here is evidence rather than a round trip of one implementation.

use evmedia_core::crypto::{key_from_tk, DERIVATION_EXTRA};

#[derive(Debug, serde::Deserialize)]
struct Triple {
    file: String,
    tk: String,
    key: String,
    source: String,
}

fn triples() -> Vec<Triple> {
    serde_json::from_str(include_str!("derivation_triples.json")).expect("fixture parses")
}

#[test]
fn every_recorded_triple_derives_from_its_token_and_filename() {
    let triples = triples();
    assert!(triples.len() >= 12, "the fixture must hold a real spread, not one sample");
    for triple in &triples {
        assert_eq!(
            key_from_tk(&triple.tk, &triple.file).unwrap(),
            triple.key,
            "{} ({})",
            triple.file,
            triple.source
        );
    }
}

/// The mutation check: with the extra dropped the derivation must stop matching, or the test
/// above would pass just as well against `MD5_hex(tk + filename)`.
#[test]
fn dropping_the_extra_stops_the_derivation_matching() {
    assert_eq!(DERIVATION_EXTRA, "20220507");
    let triple = &triples()[0];
    let without = evmedia_core::crypto::md5_hex(format!("{}{}", triple.tk, triple.file).as_bytes());
    assert_ne!(without, triple.key, "the fixture must actually depend on the extra");
}

#[test]
fn a_token_that_is_not_thirty_two_hex_characters_is_refused() {
    assert!(key_from_tk("", "119354-abc.ts").is_err());
    assert!(key_from_tk("6b682ec8bf813f0bd20c323992b4c9e", "119354-abc.ts").is_err());
    assert!(key_from_tk("6b682ec8bf813f0bd20c323992b4c9gz", "119354-abc.ts").is_err());
}
