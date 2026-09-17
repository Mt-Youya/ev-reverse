//! The `Catalog` JSON contract: course folders and video leaves.

use crate::read_json;
use anyhow::Result;
use evmedia_contract::Reporter;
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Deserialize, Serialize)]
pub struct Catalog {
    pub title: String,
    pub roots: Vec<CatalogNode>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CatalogNode {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub kind: NodeKind,
    #[serde(default)]
    pub children: Vec<CatalogNode>,
    #[serde(default)]
    pub video: Option<VideoRef>,
}

#[derive(Debug, Deserialize, Serialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    #[default]
    Folder,
    Video,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct VideoRef {
    pub id: String,
    pub duration_seconds: Option<f64>,
    pub source: String,
}

fn render_node(node: &CatalogNode, depth: usize, out: &mut Vec<String>) {
    let indent = "  ".repeat(depth);
    match &node.video {
        Some(video) => out.push(format!("{indent}▶ {} [{}]", node.title, video.id)),
        None => out.push(format!("{indent}▾ {}", node.title)),
    }
    for child in &node.children {
        render_node(child, depth + 1, out);
    }
}

/// The tree as lines, so the caller decides where they go.
pub fn render_tree(catalog: &Catalog) -> Vec<String> {
    let mut out = vec![catalog.title.clone()];
    for root in &catalog.roots {
        render_node(root, 1, &mut out);
    }
    out
}

pub fn run(catalog_path: &Path, reporter: &Reporter) -> Result<()> {
    let catalog: Catalog = read_json(catalog_path)?;
    for line in render_tree(&catalog) {
        reporter.info(line);
    }
    Ok(())
}
