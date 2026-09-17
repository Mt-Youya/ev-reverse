//! The catalog protocol uses version 200 and the logged-in player's session keys.
use aes::Aes128;
use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ecb::cipher::{block_padding::Pkcs7, BlockDecryptMut, BlockEncryptMut, KeyInit};
use md5::{Digest, Md5};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{io::Read, time::{Duration, SystemTime, UNIX_EPOCH}};

const HOST: &str = "https://en2.ieway.cn";

#[derive(Deserialize)]
pub struct CatalogSession {
    pub token: String,
    pub data_key: String,
    pub sign_secret: String,
    pub machine_id: String,
    pub busi_id: i64,
}

pub struct CatalogApi {
    pub session: CatalogSession,
    client: reqwest::Client,
}

impl CatalogApi {
    pub fn new(session: CatalogSession) -> Result<Self> {
        if session.data_key.len() != 16 || session.sign_secret.is_empty() || session.token.is_empty() {
            bail!("catalog session is missing valid credentials");
        }
        Ok(Self { session, client: reqwest::Client::builder()
            .timeout(Duration::from_secs(45)).build()? })
    }

    pub fn body(&self, endpoint: &str, fields: Value, timestamp: u64) -> Result<Value> {
        let mut fields = fields.as_object().context("request fields must be an object")?.clone();
        for (key, value) in json!({"app_name":"EVPlayer2", "app_version":"5.0.5", "need_zip":1,
            "os_name":"windows", "platform":1, "platform_type":1, "req_time":timestamp,
            "net_url":format!("{HOST}{endpoint}")}).as_object().unwrap() {
            fields.insert(key.clone(), value.clone());
        }
        fields.remove("sign");
        let mut names: Vec<_> = fields.keys().collect();
        names.sort();
        let canonical = names.into_iter().map(|name| {
            let value = &fields[name];
            let rendered = value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string());
            format!("{name}={rendered}")
        }).collect::<Vec<_>>().join("&");
        let signature = hex::encode(Md5::digest(format!("{canonical}&&{}", self.session.sign_secret)));
        fields.insert("sign".into(), json!(signature));
        let mut bytes = serde_json::to_vec(&fields)?;
        let length = bytes.len();
        bytes.resize(length + 16, 0);
        let cipher = ecb::Encryptor::<Aes128>::new_from_slice(self.session.data_key.as_bytes())
            .map_err(|_| anyhow::anyhow!("invalid catalog key"))?
            .encrypt_padded_mut::<Pkcs7>(&mut bytes, length)
            .map_err(|_| anyhow::anyhow!("catalog encryption failed"))?;
        Ok(json!({"params":STANDARD.encode(cipher), "version":200}))
    }

    pub fn open(&self, envelope: Value) -> Result<Value> {
        if envelope["errcode"].as_i64() != Some(0) {
            bail!("catalog API refused: {}: {}", envelope["errcode"], envelope["errmsg"]);
        }
        let mut bytes = if envelope["encrypt"].as_u64() == Some(1) {
            let mut cipher = STANDARD.decode(envelope["result"].as_str().context("missing result")?)?;
            ecb::Decryptor::<Aes128>::new_from_slice(self.session.data_key.as_bytes())
                .map_err(|_| anyhow::anyhow!("invalid catalog key"))?
                .decrypt_padded_mut::<Pkcs7>(&mut cipher)
                .map_err(|_| anyhow::anyhow!("invalid catalog ciphertext or padding"))?.to_vec()
        } else if let Some(text) = envelope["result"].as_str() {
            text.as_bytes().to_vec()
        } else {
            return Ok(envelope["result"].clone());
        };
        if envelope["zip"].as_u64() == Some(1) {
            let mut plain = Vec::new();
            flate2::read::GzDecoder::new(bytes.as_slice()).read_to_end(&mut plain)?;
            bytes = plain;
        }
        serde_json::from_slice(&bytes).context("catalog result is not JSON")
    }

    pub async fn request(&self, endpoint: &str, fields: Value) -> Result<Value> {
        let token = self.session.token.strip_prefix("Bearer ").unwrap_or(&self.session.token);
        for attempt in 0..3 {
            let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
            let body = serde_json::to_vec(&self.body(endpoint, fields.clone(), now)?)?;
            let response = self.client.post(format!("{HOST}{endpoint}"))
                .bearer_auth(token).header("User-Agent", "EVPlayer2/5.0.5")
                .header("Content-Type", "application/json").body(body).send().await;
            match response {
                Ok(response) if response.status().is_success() => {
                    let body = response.bytes().await?;
                    return self.open(serde_json::from_slice(&body)?);
                }
                Ok(response) if attempt < 2 && (response.status().is_server_error()
                    || response.status() == reqwest::StatusCode::TOO_MANY_REQUESTS) => {}
                Ok(response) => { response.error_for_status()?; bail!("unexpected catalog HTTP status"); }
                Err(error) if attempt == 2 => return Err(error.into()),
                Err(_) => {}
            }
            tokio::time::sleep(Duration::from_secs(1 << attempt)).await;
        }
        unreachable!()
    }

    pub async fn roots(&self, account: i64) -> Result<Value> {
        self.request("/student/getEvsAuthorityCourse", json!({"account_id":account,
            "api_version":"20250103"})).await
    }

    pub async fn course(&self, account: i64, course: i64) -> Result<Value> {
        self.request("/student/getEVSCourseDetail", json!({"account_id":account,"course_id":course})).await
    }

    pub async fn download_url(&self, video: &Value) -> Result<Value> {
        self.request("/student/getEvsSignUrl", json!({"file_id":video["file_id"],
            "enc_ver":video["enc_ver"], "fsave":video["fsave"], "online":-1,
            "api_version":20260528})).await
    }

    pub async fn download_key(&self, file: i64) -> Result<Value> {
        self.request("/student/getDownEVSKey", json!({"file_id":file, "device_name":"windows",
            "machine_id":self.session.machine_id, "busi_id":self.session.busi_id,
            "api_version":20231103})).await
    }

    pub async fn get_url(&self, url: &str) -> Result<Vec<u8>> {
        let response = self.client.get(url).send().await?.error_for_status()?;
        Ok(response.bytes().await?.to_vec())
    }
}
