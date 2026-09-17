//! Downloaded EVS manifests use the same filename mask as TS, with the descriptor's base key.
use aes::{Aes128, Aes256};
use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ecb::cipher::{block_padding::{NoPadding, Pkcs7}, BlockDecryptMut, KeyInit};
use serde::{Deserialize, Serialize};

#[derive(Deserialize, Serialize)]
pub struct Descriptor {
    pub host: String,
    pub req: String,
    pub dkey: String,
    pub dkey_ver: u32,
    pub cache_key: String,
    pub base_key: String,
}

impl Descriptor {
    pub fn open(tkey: &str) -> Result<Self> {
        let mut bytes = STANDARD.decode(tkey).context("invalid EVS descriptor base64")?;
        let plain = ecb::Decryptor::<Aes128>::new_from_slice(b"11585ec1b1f8f30e")
            .expect("constant AES key")
            .decrypt_padded_mut::<Pkcs7>(&mut bytes)
            .map_err(|_| anyhow::anyhow!("invalid EVS descriptor padding"))?;
        serde_json::from_slice(plain).context("invalid EVS descriptor JSON")
    }

    pub fn manifest(&self, ciphertext: &[u8], filename: &str) -> Result<String> {
        if filename.contains(['/', '\\']) || !filename.ends_with(".evs") {
            bail!("expected the EVS upload filename");
        }
        if ciphertext.is_empty() || ciphertext.len() % 16 != 0 {
            bail!("EVS manifest is not AES-block aligned");
        }
        let key = crate::crypto::key_from_text(&self.base_key)?;
        let mask = crate::crypto::mask_from_filename(filename);
        let mut bytes: Vec<_> = ciphertext.iter().enumerate().map(|(i, b)| b ^ mask[i % 16]).collect();
        ecb::Decryptor::<Aes256>::new_from_slice(&key).expect("validated AES key")
            .decrypt_padded_mut::<NoPadding>(&mut bytes)
            .map_err(|_| anyhow::anyhow!("invalid EVS manifest ciphertext"))?;
        let padding = bytes.iter().rev().take_while(|b| **b == b'#').count();
        if padding > 15 { bail!("invalid EVS manifest padding"); }
        bytes.truncate(bytes.len() - padding);
        let text = String::from_utf8(bytes).context("EVS manifest is not UTF-8")?;
        crate::vod::Vod::parse(&text).context("EVS does not contain a complete video playlist")?;
        Ok(text)
    }
}
