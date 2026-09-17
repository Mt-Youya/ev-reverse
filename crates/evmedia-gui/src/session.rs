//! Reading and sanity-checking the session file the CLI authenticates with.
//!
//! The file is a short-lived credential produced by the player probe: a bearer token plus the
//! catalog's dynamic keys. The window never uses it for anything except handing its path to
//! `evmedia`, so this module exists only to answer "is this the right file?" before a batch of two
//! hundred videos starts failing on a typo.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Summary {
    pub path: String,
    /// The account the token belongs to, when the file states it. Informational: the CLI is told the
    /// account explicitly, so a wrong one here is a warning and not an error.
    pub account_id: Option<i64>,
    pub machine_id: String,
    pub busi_id: Option<i64>,
    pub token_hint: String,
}

/// Only the fields this module reports on, so the file may carry more than the CLI needs.
#[derive(Deserialize)]
struct Raw {
    #[serde(default)]
    token: String,
    #[serde(default)]
    data_key: String,
    #[serde(default)]
    sign_secret: String,
    #[serde(default)]
    machine_id: String,
    #[serde(default)]
    busi_id: Option<i64>,
    #[serde(default)]
    account_id: Option<i64>,
}

pub fn inspect(path: &Path) -> Result<Summary, String> {
    let bytes = std::fs::read(path).map_err(|error| format!("读取会话文件失败：{error}"))?;
    let value: Value = serde_json::from_slice(crate::strip_bom(&bytes))
        .map_err(|error| format!("会话文件不是 JSON：{error}"))?;
    let raw: Raw = serde_json::from_value(value)
        .map_err(|error| format!("会话文件缺少字段：{error}"))?;

    // These three are exactly what `CatalogApi::new` refuses to work without; checking them here
    // turns "every video failed" into one message before anything starts.
    if raw.token.trim().is_empty() {
        return Err("会话文件里没有 token，请在 EVPlayer2 里重新登录后重新获取".to_string());
    }
    if raw.sign_secret.trim().is_empty() {
        return Err("会话文件里没有 sign_secret".to_string());
    }
    if raw.data_key.chars().count() != 16 {
        return Err(format!(
            "会话文件的 data_key 必须是 16 个字符，实际是 {}",
            raw.data_key.chars().count()
        ));
    }

    Ok(Summary {
        path: path.display().to_string(),
        account_id: raw.account_id,
        machine_id: raw.machine_id,
        busi_id: raw.busi_id,
        token_hint: hint(&raw.token),
    })
}

/// A token is a credential: it is never echoed whole, and never written into a log the user might
/// paste somewhere. Enough characters to tell two tokens apart, and no more.
fn hint(token: &str) -> String {
    let value = token.strip_prefix("Bearer ").unwrap_or(token);
    let head: String = value.chars().take(8).collect();
    format!("{head}...（共 {} 字符）", value.chars().count())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(name: &str, body: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!("evmedia-session-{name}.json"));
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn a_complete_session_is_accepted() {
        let path = write("ok", r#"{"token":"Bearer abcdefghijklmnop","data_key":"0123456789abcdef",
            "sign_secret":"s","machine_id":"m","busi_id":7,"account_id":119354}"#);
        let summary = inspect(&path).unwrap();
        assert_eq!(summary.account_id, Some(119354));
        assert!(summary.token_hint.starts_with("abcdefgh"));
        // The token itself must not appear anywhere in what the UI is given.
        assert!(!summary.token_hint.contains("klmnop"));
    }

    #[test]
    fn the_three_fields_the_cli_needs_are_checked_here() {
        let short_key = write("key", r#"{"token":"t","data_key":"short","sign_secret":"s"}"#);
        assert!(inspect(&short_key).unwrap_err().contains("16"));

        let no_token = write("token", r#"{"token":"","data_key":"0123456789abcdef","sign_secret":"s"}"#);
        assert!(inspect(&no_token).unwrap_err().contains("token"));

        let no_secret = write("secret", r#"{"token":"t","data_key":"0123456789abcdef"}"#);
        assert!(inspect(&no_secret).unwrap_err().contains("sign_secret"));
    }

    #[test]
    fn extra_fields_are_tolerated() {
        let path = write("extra", r#"{"token":"t","data_key":"0123456789abcdef","sign_secret":"s",
            "machine_id":"m","note":"the probe writes more than the CLI reads"}"#);
        assert!(inspect(&path).is_ok());
    }

    #[test]
    fn a_missing_file_says_so() {
        let error = inspect(Path::new("definitely/not/here.json")).unwrap_err();
        assert!(error.contains("读取会话文件失败"));
    }
}
