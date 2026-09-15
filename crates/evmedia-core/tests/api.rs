//! The signed request, against what the player's own request body says.
//!
//! The strongest check available without a token is that the encryption is *the same encryption*:
//! build a body from a captured request's fields and it must decrypt back to those fields, and the
//! signed string must be the name-ordered `key=value&…` form the player was caught hashing at
//! `0x1FD60`. The signature *value* is not pinned here on purpose — the hook truncated the preimage
//! at 512 characters, so what it hashes beyond that is still open, and a test written against a
//! guess would be a test of the guess.

use evmedia_core::api::{self, ListRequest, PROTOCOL_VERSION};

const PLAYKEY: &str = "V4bsTWiOcJ1KCbkjYwkzaRFWUM0Xyr2aYnpVdqQbikK2";
const LISTSTR: &str = "0|0|119354-aaaaaaaa-0000-4000-8000-000000000001.ts";

#[test]
fn the_signed_string_is_the_fields_in_name_order() {
    let request = ListRequest::new(PLAYKEY, LISTSTR);
    let canonical = request.sign_input(1789470730);
    assert_eq!(
        canonical,
        format!(
            "app_version=5.0.5&evs_playkey={PLAYKEY}&need_zip=1&os_name=windows&platform=1&\
             platform_type=1&req_time=1789470730&ts_liststr={LISTSTR}"
        )
    );
    // `sign` is the result and `type` is sent without being signed; neither may appear here.
    assert!(!canonical.contains("sign="));
    assert!(!canonical.contains("&type="));
}

#[test]
fn a_body_round_trips_through_its_own_encryption() {
    let request = ListRequest::new(PLAYKEY, LISTSTR);
    let body = request.body(1789470730).expect("body");
    let (playkey, liststr) = api::request_fields(body.as_bytes()).expect("decrypts");
    assert_eq!(playkey, PLAYKEY);
    assert_eq!(liststr, LISTSTR);
}

#[test]
fn the_envelope_carries_the_version_the_endpoint_parses() {
    // 202, not 200: sent as 200 the server parses the params under an older protocol and answers
    // with a JSON parse error that looks like a decryption failure.
    let request = ListRequest::new(PLAYKEY, LISTSTR);
    let body = request.body(1789470730).expect("body");
    let envelope: serde_json::Value = serde_json::from_str(&body).expect("json");
    assert_eq!(envelope["version"], PROTOCOL_VERSION);
    assert!(envelope["params"].as_str().is_some_and(|text| text.len() % 4 == 0));
}

#[test]
fn the_signature_changes_with_the_time_it_signs_over() {
    let request = ListRequest::new(PLAYKEY, LISTSTR);
    assert_ne!(request.sign(1789470730), request.sign(1789470731));
}

#[test]
fn a_captured_body_without_a_play_key_is_refused() {
    assert!(api::request_fields(b"{\"params\":\"AAAA\",\"version\":202}").is_err());
    assert!(api::request_fields(b"not json").is_err());
    assert!(api::request_fields(b"{\"version\":202}").is_err());
}
