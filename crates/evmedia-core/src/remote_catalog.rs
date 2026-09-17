//! Fetch every authorized course and retain recursive folders and video metadata.
use crate::{catalog_api::CatalogApi, catalog::{Catalog, CatalogNode, NodeKind, VideoRef}};
use anyhow::{bail, Context, Result};
use evmedia_contract::Reporter;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::{BTreeMap, HashSet}, fs, path::Path};

#[derive(Clone, Deserialize, Serialize)]
pub struct Video {
    pub account_id: i64,
    pub course_id: i64,
    pub file_id: i64,
    pub title: String,
    pub duration: f64,
    pub enc_ver: String,
    pub fsave: String,
    pub upload_key: String,
    #[serde(rename = "type")]
    pub kind: String,
}

#[derive(Deserialize, Serialize)]
pub struct Index {
    pub account_id: i64,
    pub authority: Value,
    pub courses: BTreeMap<i64, Value>,
}

impl Index {
    pub fn videos(&self) -> Result<Vec<Video>> {
        let mut output = Vec::new();
        for (&course, data) in &self.courses {
            collect(&data["course_detail"], &mut output, 0)?;
            if output.iter().any(|v| v.account_id != self.account_id) {
                bail!("catalog contains a different account");
            }
            let mut ids = HashSet::new();
            for video in output.iter().filter(|v| v.course_id == course) {
                if !ids.insert(video.file_id) { bail!("duplicate video in course {course}"); }
            }
        }
        Ok(output)
    }

    pub fn find(&self, course: i64, file: i64) -> Result<Video> {
        self.videos()?.into_iter().find(|v| v.course_id == course && v.file_id == file)
            .context("video is not in the authorized catalog")
    }

    pub fn tree(&self) -> Result<Catalog> {
        let mut entries = BTreeMap::new();
        for group in ["big_list", "small_list", "authority_list"] {
            for item in self.authority[group].as_array().context("missing catalog group")? {
                let uuid = item["uuid"].as_str().context("missing folder UUID")?.to_string();
                if entries.insert(uuid, item).is_some() { bail!("duplicate catalog UUID"); }
            }
        }
        let mut seen = HashSet::new();
        let roots = folders("0", &entries, &self.courses, &mut seen, 0)?;
        if seen.len() != entries.len() { bail!("catalog contains orphaned or cyclic folders"); }
        Ok(Catalog { title: format!("账号 {}", self.account_id), roots })
    }
}

fn collect(node: &Value, output: &mut Vec<Video>, depth: usize) -> Result<()> {
    if depth > 128 { bail!("catalog nesting exceeds 128 levels"); }
    for file in node["files"].as_array().context("missing course files")? {
        if file["type"] == "video" { output.push(serde_json::from_value(file.clone())?); }
    }
    for child in node["childs"].as_array().context("missing course children")? {
        collect(child, output, depth + 1)?;
    }
    Ok(())
}

fn course_node(data: &Value, depth: usize) -> Result<CatalogNode> {
    if depth > 128 { bail!("catalog nesting exceeds 128 levels"); }
    let own = &data["self"];
    let mut children = Vec::new();
    for child in data["childs"].as_array().context("missing course children")? {
        children.push(course_node(child, depth + 1)?);
    }
    for file in data["files"].as_array().context("missing course files")? {
        if file["type"] != "video" { continue; }
        let video: Video = serde_json::from_value(file.clone())?;
        let id = format!("{}:{}", video.course_id, video.file_id);
        children.push(CatalogNode { id: id.clone(), title: video.title, kind: NodeKind::Video,
            children: Vec::new(), video: Some(VideoRef { id, duration_seconds: Some(video.duration),
            source: video.upload_key }) });
    }
    Ok(CatalogNode { id: own["uuid"].as_str().context("missing folder UUID")?.into(),
        title: own["name"].as_str().context("missing folder name")?.into(),
        kind: NodeKind::Folder, children, video: None })
}

fn folders(parent: &str, entries: &BTreeMap<String, &Value>, courses: &BTreeMap<i64, Value>,
           seen: &mut HashSet<String>, depth: usize) -> Result<Vec<CatalogNode>> {
    if depth > 128 { bail!("catalog nesting exceeds 128 levels"); }
    let mut items: Vec<_> = entries.iter().filter(|(_, v)| v["parent_uuid"] == parent).collect();
    items.sort_by_key(|(_, v)| (v["order_num"].as_i64().unwrap_or(0), v["id"].as_i64().unwrap_or(0)));
    let mut nodes = Vec::new();
    for (uuid, item) in items {
        if !seen.insert(uuid.clone()) { bail!("catalog folder cycle"); }
        let id = item["id"].as_i64().context("missing course ID")?;
        let mut node = if let Some(course) = courses.get(&id) {
            course_node(&course["course_detail"], depth + 1)?
        } else {
            CatalogNode { id: uuid.clone(), title: item["name"].as_str().context("missing folder name")?.into(),
                kind: NodeKind::Folder, children: Vec::new(), video: None }
        };
        node.children.extend(folders(uuid, entries, courses, seen, depth + 1)?);
        nodes.push(node);
    }
    Ok(nodes)
}

pub async fn fetch(api: &CatalogApi, account: i64, output: &Path, reporter: &Reporter) -> Result<()> {
    let authority = api.roots(account).await?;
    let list = authority["authority_list"].as_array().context("missing authority list")?;
    let raw = output.join("raw");
    fs::create_dir_all(&raw)?;
    fs::write(raw.join("authority.json"), serde_json::to_vec_pretty(&authority)?)?;
    let mut courses = BTreeMap::new();
    for (position, course) in list.iter().enumerate() {
        if reporter.stopped() { bail!("catalog fetch stopped"); }
        let id = course["id"].as_i64().context("missing course ID")?;
        if course["account_id"].as_i64() != Some(account) { bail!("unexpected course account"); }
        if courses.contains_key(&id) { bail!("duplicate authorized course"); }
        let detail = api.course(account, id).await?;
        fs::write(raw.join(format!("course-{id}.json")), serde_json::to_vec_pretty(&detail)?)?;
        courses.insert(id, detail);
        reporter.info(format!("读取课程 {}/{}", position + 1, list.len()));
    }
    let index = Index { account_id: account, authority, courses };
    let count = index.videos()?.len();
    let tree = index.tree()?;
    fs::write(output.join("catalog.json"), serde_json::to_vec_pretty(&tree)?)?;
    fs::write(output.join("index.json"), serde_json::to_vec_pretty(&index)?)?;
    reporter.info(format!("目录读取完成：{} 门课程，{count} 个视频", index.courses.len()));
    Ok(())
}
