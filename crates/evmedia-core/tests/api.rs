//! The signed request, against what the player's own request body says.
//!
//! The signature is the one part of this protocol that cannot be read off a capture: it covers a
//! secret the request does not carry. It was caught live at `0x1FD60` — the player hashed
//! `…&type=0&&ieway.cn@20200611` — and it reproduces all 238 captured request signatures. The
//! string and the secret are pinned here so that a change to either fails loudly.

use evmedia_core::api::{self, ListRequest, PROTOCOL_VERSION, SIGN_SECRET};

const PLAYKEY: &str = "V4bsTWiOcJ1KCbkjYwkzaRFWUM0Xyr2aYnpVdqQbikK2";
const LISTSTR: &str = "0|0|119354-aaaaaaaa-0000-4000-8000-000000000001.ts";

#[test]
fn the_signed_string_is_the_fields_in_name_order_with_the_secret_appended() {
    let request = ListRequest::new(PLAYKEY, LISTSTR);
    let canonical = request.sign_input(1789470730);
    assert_eq!(
        canonical,
        format!(
            "app_version=5.0.5&evs_playkey={PLAYKEY}&need_zip=1&os_name=windows&platform=1&\
             platform_type=1&req_time=1789470730&ts_liststr={LISTSTR}&type=0&&{SIGN_SECRET}"
        )
    );
    // `sign` is the result, never an input.
    assert!(!canonical.contains("sign="));
}

/// The mutation check for the line above: without the tail the same fields hash to something else,
/// which is what two rounds of rebuilding the signature from captures kept producing.
#[test]
fn dropping_the_secret_changes_the_signature() {
    assert_eq!(SIGN_SECRET, "ieway.cn@20200611");
    let request = ListRequest::new(PLAYKEY, LISTSTR);
    let fields_only = request
        .sign_input(1789470730)
        .trim_end_matches(&format!("&&{SIGN_SECRET}"))
        .to_string();
    assert_ne!(
        request.sign(1789470730),
        evmedia_core::crypto::md5_hex(fields_only.as_bytes())
    );
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
