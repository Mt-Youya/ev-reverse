//! The signed API request, and the encrypted reply that comes back.
//!
//! The player's segment list is not served, it is *signed*: the client sends the segment names it
//! already knows (`ts_liststr`) and gets back a signed URL and a `tk` for each. Reproducing that
//! request is what removes the player from the loop, and every ingredient is now known:
//!
//! * the body is JSON under AES-128-ECB with a constant key, base64'd into `{params, version}`;
//! * `version` is 202 — sent as 200 the server parses the params under an older protocol and
//!   answers "invalid character '/' looking for beginning of value", which reads like a
//!   decryption failure and is a version mismatch;
//! * the signature is `MD5` of the request's fields as a sorted `key=value&key=value` string, with
//!   `sign` and `type` omitted — caught live at `0x1FD60`, where the player hashes exactly that
//!   string;
//! * the reply's `result` is base64 of AES-128-ECB, then gzip.
//!
//! The two things that are credentials rather than protocol — the bearer token and the lesson's
//! `evs_playkey` — are inputs. `request_fields` exists so that a captured request body supplies
//! them without anyone retyping a 280-character key.

use aes::Aes128;
use anyhow::{anyhow, bail, Context, Result};
use ecb::cipher::{block_padding::NoPadding, BlockDecryptMut, BlockEncryptMut, KeyInit};
use md5::{Digest as _, Md5};
use serde_json::{json, Map, Value};
use std::io::Read;

/// The key every request and response body on this API is encrypted with. Caught live at
/// `0x1EA10`, where the player hands it to its own AES.
pub const DATA_KEY: &[u8; 16] = b"x!@#y.cn_xnk0506";

/// The protocol version this endpoint parses its params under. It matches the `dkey_ver` the
/// request descriptors carry, and getting it wrong produces a parse error rather than a version
/// error.
pub const PROTOCOL_VERSION: u32 = 202;

pub const DEFAULT_HOST: &str = "https://en2v4.ieway.cn";
pub const LIST_ENDPOINT: &str = "/student/getPlayTimeKeySignEVS20260515";

/// The fields the signature covers: every field the request carries except `sign` itself, in name
/// order.
const SIGNED_FIELDS: [&str; 9] =
    ["app_version", "evs_playkey", "need_zip", "os_name", "platform", "platform_type", "req_time",
     "ts_liststr", "type"];

/// What the player appends before hashing, and the reason rebuilding the signature from the request
/// alone never matched: it is signed with a **secret the request does not carry**.
///
/// Caught live at `0x1FD60`, where the player hashed `…&ts_liststr=…&type=0&&ieway.cn@20200611` —
/// the fields, then `&&`, then this. It is not in the DLL's strings either; the player asks its
/// Bridge layer for it by an obfuscated name. The first capture missed it because the read stopped
/// at 512 characters, in the middle of `ts_liststr`.
pub const SIGN_SECRET: &str = "ieway.cn@20200611";

#[derive(Debug, Clone)]
pub struct ListRequest {
    pub host: String,
    pub endpoint: String,
    /// The lesson's play key: the server issues it, and it rotates with the version.
    pub playkey: String,
    /// `0|0|<file>.ts,1|0|<file>.ts,...` — the segments the client wants signed.
    pub liststr: String,
}

impl ListRequest {
    pub fn new(playkey: impl Into<String>, liststr: impl Into<String>) -> Self {
        Self {
            host: DEFAULT_HOST.to_string(),
            endpoint: LIST_ENDPOINT.to_string(),
            playkey: playkey.into(),
            liststr: liststr.into(),
        }
    }

    pub fn segment_count(&self) -> usize {
        if self.liststr.trim().is_empty() {
            0
        } else {
            self.liststr.matches(',').count() + 1
        }
    }

    /// The exact string the player hashes: `key=value` pairs in name order, joined by `&`, then the
    /// separator and the secret — `…&type=0&&ieway.cn@20200611`, as caught live.
    pub fn sign_input(&self, req_time: u64) -> String {
        let fields = self.signed_values(req_time);
        let joined = SIGNED_FIELDS
            .iter()
            .map(|name| {
                let value = fields.get(*name).cloned().unwrap_or(Value::Null);
                let rendered = match value {
                    Value::String(text) => text,
                    other => other.to_string(),
                };
                format!("{name}={rendered}")
            })
            .collect::<Vec<_>>()
            .join("&");
        format!("{joined}&&{SIGN_SECRET}")
    }

    pub fn sign(&self, req_time: u64) -> String {
        hex::encode(Md5::digest(self.sign_input(req_time).as_bytes()))
    }

    fn signed_values(&self, req_time: u64) -> Map<String, Value> {
        let mut fields = Map::new();
        fields.insert("app_version".into(), json!("5.0.5"));
        fields.insert("evs_playkey".into(), json!(self.playkey));
        fields.insert("need_zip".into(), json!(1));
        fields.insert("os_name".into(), json!("windows"));
        fields.insert("platform".into(), json!(1));
        fields.insert("platform_type".into(), json!(1));
        fields.insert("req_time".into(), json!(req_time));
        fields.insert("ts_liststr".into(), json!(self.liststr));
        fields.insert("type".into(), json!(0));
        fields
    }

    /// The request body: the signed fields plus `sign`, encrypted and wrapped.
    pub fn body(&self, req_time: u64) -> Result<String> {
        let mut fields = self.signed_values(req_time);
        fields.insert("sign".into(), json!(self.sign(req_time)));
        let plain = serde_json::to_vec(&Value::Object(fields))?;
        let mut buffer = pkcs7(&plain);
        let cipher = ecb::Encryptor::<Aes128>::new_from_slice(DATA_KEY)
            .map_err(|_| anyhow!("invalid AES-128 key"))?;
        let length = buffer.len();
        cipher
            .encrypt_padded_mut::<NoPadding>(&mut buffer, length)
            .map_err(|_| anyhow!("AES-128 encryption failed"))?;
        use base64::Engine as _;
        let envelope = json!({
            "params": base64::engine::general_purpose::STANDARD.encode(&buffer),
            "version": PROTOCOL_VERSION,
        });
        Ok(serde_json::to_string(&envelope)?)
    }
}

fn pkcs7(plain: &[u8]) -> Vec<u8> {
    let pad = 16 - (plain.len() % 16);
    let mut buffer = plain.to_vec();
    buffer.extend(std::iter::repeat(pad as u8).take(pad));
    buffer
}

/// A response envelope's `result` -> the JSON inside it.
///
/// `result` is base64 of AES-128-ECB, and what comes out is usually a gzip stream whose buffer has
/// a stale tail — so the gzip reader has to stop where the stream stops rather than at the end of
/// the buffer, or a correct key reports a corrupt stream.
pub fn decrypt_result(result: &str) -> Result<Value> {
    use base64::Engine as _;
    let cipher = base64::engine::general_purpose::STANDARD
        .decode(result.trim())
        .context("result is not base64")?;
    let usable = cipher.len() / 16 * 16;
    if usable == 0 {
        bail!("result is shorter than one AES block");
    }
    let mut buffer = cipher[..usable].to_vec();
    ecb::Decryptor::<Aes128>::new_from_slice(DATA_KEY)
        .map_err(|_| anyhow!("invalid AES-128 key"))?
        .decrypt_padded_mut::<NoPadding>(&mut buffer)
        .map_err(|_| anyhow!("AES-128 decryption failed"))?;
    let plain = if buffer.starts_with(&[0x1f, 0x8b]) {
        let mut text = Vec::new();
        flate2::read::GzDecoder::new(&buffer[..])
            .read_to_end(&mut text)
            .context("result is not a gzip stream")?;
        text
    } else {
        buffer
    };
    let end = plain.iter().rposition(|byte| *byte == b'}').unwrap_or(plain.len());
    serde_json::from_slice(&plain[..=end]).context("result is not JSON")
}

/// The whole envelope: errcode, errmsg and the decrypted payload, or the server's complaint.
pub fn open_envelope(body: &[u8]) -> Result<Value> {
    let envelope: Value = serde_json::from_slice(body).context("response is not JSON")?;
    let code = envelope.get("errcode").and_then(Value::as_i64).unwrap_or(0);
    if code != 0 {
        bail!(
            "the API refused: errcode {code}: {}",
            envelope.get("errmsg").and_then(Value::as_str).unwrap_or("")
        );
    }
    match envelope.get("result").and_then(Value::as_str) {
        Some(result) if !result.is_empty() => decrypt_result(result),
        _ => bail!("the API returned no result"),
    }
}

/// A captured request body -> `(playkey, ts_liststr)`.
///
/// This is the one thing a capture is still good for here, and it saves retyping a 280-character
/// play key that rotates per session: the body is decryptable with the same constant the rest of
/// this module uses.
pub fn request_fields(body: &[u8]) -> Result<(String, String)> {    use base64::Engine as _;
    let envelope: Value = serde_json::from_slice(body).context("captured body is not JSON")?;
    let params = envelope
        .get("params")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("captured body has no params"))?;
    let cipher = base64::engine::general_purpose::STANDARD
        .decode(params.trim())
        .context("params is not base64")?;
    let usable = cipher.len() / 16 * 16;
    if usable == 0 {
        bail!("params is shorter than one AES block");
    }
    let mut buffer = cipher[..usable].to_vec();
    ecb::Decryptor::<Aes128>::new_from_slice(DATA_KEY)
        .map_err(|_| anyhow!("invalid AES-128 key"))?
        .decrypt_padded_mut::<NoPadding>(&mut buffer)
        .map_err(|_| anyhow!("AES-128 decryption failed"))?;
    let end = buffer.iter().rposition(|byte| *byte == b'}').unwrap_or(buffer.len());
    let fields: Value = serde_json::from_slice(&buffer[..=end]).context("params is not JSON")?;
    let playkey = fields.get("evs_playkey").and_then(Value::as_str).unwrap_or_default();
    let liststr = fields.get("ts_liststr").and_then(Value::as_str).unwrap_or_default();
    if playkey.is_empty() || liststr.is_empty() {
        bail!("captured request has no play key or segment list");
    }
    Ok((playkey.to_string(), liststr.to_string()))
}

/// The signed preimage the player hashes -> `(playkey, ts_liststr)`.
///
/// `tools/parser-tools/probe_kdf.py` logs exactly this string when the player builds a request, and
/// it is the only place the *current* play key is visible in readable form: it rotates per session
/// and exists in no file. Reading it here is what makes `fetch` usable without copying a
/// 280-character key by hand.
pub fn fields_from_preimage(preimage: &str) -> Result<(String, String)> {
    if !preimage.starts_with("app_version=") {
        bail!("not a signed request preimage");
    }
    let mut playkey = None;
    let mut liststr = None;
    for part in preimage.split('&') {
        if let Some(value) = part.strip_prefix("evs_playkey=") {
            playkey = Some(value.to_string());
        }
        if let Some(value) = part.strip_prefix("ts_liststr=") {
            liststr = Some(value.to_string());
        }
    }
    match (playkey, liststr) {
        (Some(playkey), Some(liststr)) if !playkey.is_empty() && !liststr.is_empty() => {
            Ok((playkey, liststr))
        }
        _ => bail!("preimage carries no play key or no segment list"),
    }
}

/// The newest signed preimage in a `probe_kdf.py` capture.
pub fn fields_from_capture(path: &std::path::Path) -> Result<(String, String)> {
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let mut newest = None;
    for line in text.lines() {
        let Ok(record) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if let Some(input) = record.get("md5_input").and_then(Value::as_str) {
            if input.starts_with("app_version=") {
                newest = Some(input.to_string());
            }
        }
    }
    let preimage = newest.ok_or_else(|| anyhow!("no signed request in {}", path.display()))?;
    fields_from_preimage(&preimage)
}

/// Send the request and return the signed list.
pub async fn fetch_list(request: &ListRequest, token: &str) -> Result<Value> {
    let req_time = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .context("system clock is before the epoch")?
        .as_secs();
    let body = request.body(req_time)?;
    let url = format!("{}{}", request.host.trim_end_matches('/'), request.endpoint);
    let client = reqwest::Client::builder().build()?;
    // A captured header value already carries its scheme (`Bearer eyJ…`), and a token pasted from
    // anywhere else may not. Sending `Bearer Bearer …` is refused as "not logged in", which reads
    // like an expired token and is a doubled prefix.
    let token = token.trim();
    let bearer = if token.to_ascii_lowercase().starts_with("bearer ") {
        token.to_string()
    } else {
        format!("Bearer {token}")
    };
    let response = client
        .post(&url)
        .header("content-type", "application/json")
        .header("accept", "*/*")
        .header("user-agent", "restclient-cpp/@restclient-cpp_VERSION@")
        .header("authorization", bearer)
        .body(body)
        .send()
        .await
        .with_context(|| format!("POST {url}"))?;
    let status = response.status();
    let bytes = response.bytes().await?;
    if !status.is_success() {
        bail!("POST {url} -> {status}");
    }
    open_envelope(&bytes)
}
