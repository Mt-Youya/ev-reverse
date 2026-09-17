//! Reading the directory tree the CLI writes.
//!
//! `evmedia catalog` already does the hard part — it walks every authorized course and writes
//! `catalog.json`. This module only parses that file, so the window's tree is the CLI's data and
//! not a second interpretation of the API. The shapes below are the contract in
//! `evmedia-core::catalog`; a field that is missing there must stay optional here rather than
//! becoming a parse failure, because the CLI is allowed to grow the file.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct Catalog {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub roots: Vec<Node>,
}

#[derive(Deserialize, Serialize, Clone, Debug, Default)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    #[default]
    Folder,
    Video,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct Node {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub kind: NodeKind,
    #[serde(default)]
    pub children: Vec<Node>,
    #[serde(default)]
    pub video: Option<VideoRef>,
}

/// One video leaf.
///
/// The two names are not decoration, and they are not the same name. `evmedia catalog` writes
/// `duration_seconds` — which is what this reads — and the view reads `durationSeconds`, which is
/// what this writes. Getting only one of them right is invisible in a passing build and shows up as
/// a column of "—" for nineteen hundred lessons, which is exactly what happened once.
/// `every_field_keeps_its_name_through_the_wire` is the guard.
///
/// It deserialises from either spelling, so the round trip through the wire form works as well. The
/// alternative — one `rename_all` — cannot satisfy both directions, which is the trap.
#[derive(Serialize, Clone, Debug)]
pub struct VideoRef {
    pub id: String,
    #[serde(rename = "durationSeconds")]
    pub duration_seconds: Option<f64>,
    pub source: String,
}

impl<'de> Deserialize<'de> for VideoRef {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        struct Either {
            #[serde(default)]
            id: String,
            /// What `evmedia catalog` writes.
            #[serde(default)]
            duration_seconds: Option<f64>,
            /// What this type itself writes, so the wire form parses back.
            #[serde(default, rename = "durationSeconds")]
            duration_seconds_camel: Option<f64>,
            #[serde(default)]
            source: String,
        }
        let raw = Either::deserialize(deserializer)?;
        Ok(Self {
            id: raw.id,
            duration_seconds: raw.duration_seconds.or(raw.duration_seconds_camel),
            source: raw.source,
        })
    }
}

impl Catalog {
    pub fn load(path: &Path) -> Result<Self, String> {
        let bytes = std::fs::read(path)
            .map_err(|error| format!("读取 {} 失败：{error}", path.display()))?;
        serde_json::from_slice(crate::strip_bom(&bytes))
            .map_err(|error| format!("{} 不是合法的目录文件：{error}", path.display()))
    }

    pub fn video_count(&self) -> usize {
        self.roots.iter().map(Node::video_count).sum()
    }

    /// Every video in the tree whose id is in `selected`, in tree order, each carrying the folder
    /// titles above it.
    ///
    /// The path is not decoration: it is where the file goes. A selection whose paths came back
    /// empty wrote every lesson into one flat `out/` directory, which is exactly the numbering
    /// collision the folders exist to avoid.
    pub fn selected(&self, selected: &[String]) -> Vec<Found> {
        let mut out = Vec::new();
        for root in &self.roots {
            root.collect_selected(selected, &mut Vec::new(), &mut out);
        }
        out
    }
}

/// A video leaf together with the folder titles above it, so a queue row can say where it came
/// from without the frontend having to walk the tree again.
#[derive(Clone, Debug)]
pub struct Found {
    pub course: i64,
    pub file: i64,
    pub title: String,
    pub duration_seconds: Option<f64>,
    pub path: Vec<String>,
}

impl Found {
    pub fn id(&self) -> String {
        format!("{}:{}", self.course, self.file)
    }
}

impl Node {
    pub fn video_count(&self) -> usize {
        if self.video.is_some() {
            return 1;
        }
        self.children.iter().map(Node::video_count).sum()
    }

    fn collect_selected(&self, selected: &[String], trail: &mut Vec<String>, out: &mut Vec<Found>) {
        if let Some(video) = &self.video {
            if selected.iter().any(|id| id == &video.id) {
                if let Some(mut found) = parse(&video.id, &self.title, video) {
                    found.path = trail.clone();
                    out.push(found);
                }
            }
            return;
        }
        // The same fold the screen and the path builder use: the catalog nests a course under a
        // folder of its own name, and that repetition belongs in neither a breadcrumb nor a path.
        let name = self.title.trim();
        let repeated = name.is_empty() || trail.last().map(String::as_str) == Some(name);
        if !repeated {
            trail.push(name.to_string());
        }
        for child in &self.children {
            child.collect_selected(selected, trail, out);
        }
        if !repeated {
            trail.pop();
        }
    }
    /// The ids of every video at or below this node — what a folder checkbox selects.
    pub fn video_ids(&self) -> Vec<String> {
        let mut out = Vec::new();
        self.collect_ids(&mut out);
        out
    }

    fn collect_ids(&self, out: &mut Vec<String>) {
        if let Some(video) = &self.video {
            out.push(video.id.clone());
            return;
        }
        for child in &self.children {
            child.collect_ids(out);
        }
    }
}

/// A video id is `<course>:<file>`, which is what `evmedia-core` writes in `course_node`. A leaf
/// that does not follow that shape is skipped rather than guessed at: it means the catalog came
/// from a different producer, and a guessed id would export the wrong lesson.
pub fn parse(id: &str, title: &str, video: &VideoRef) -> Option<Found> {
    let (course, file) = id.split_once(':')?;
    Some(Found {
        course: course.parse().ok()?,
        file: file.parse().ok()?,
        title: title.to_string(),
        duration_seconds: video.duration_seconds,
        path: Vec::new(),
    })
}

/// Flatten the tree into the list the window's middle pane shows, carrying folder titles down.
pub fn flatten(catalog: &Catalog) -> Vec<Found> {
    let mut out = Vec::new();
    for root in &catalog.roots {
        walk(root, &mut Vec::new(), &mut out);
    }
    out
}

fn walk(node: &Node, trail: &mut Vec<String>, out: &mut Vec<Found>) {
    if let Some(video) = &node.video {
        if let Some(mut found) = parse(&video.id, &node.title, video) {
            found.path = trail.clone();
            out.push(found);
        }
        return;
    }
    // Folded exactly as `collect_selected` folds it. The two walk the same tree for two different
    // callers — the screen and the export — and a disagreement between them means the path shown is
    // not the path written.
    let name = node.title.trim();
    let repeated = name.is_empty() || trail.last().map(String::as_str) == Some(name);
    if !repeated {
        trail.push(name.to_string());
    }
    for child in &node.children {
        walk(child, trail, out);
    }
    if !repeated {
        trail.pop();
    }
}

/// Where the CLI leaves the directory: `evmedia catalog --output <root>` writes `<root>/catalog.json`
/// and the recursive index beside it. This is the CLI's own layout, and the window reads it rather
/// than choosing one.
pub fn catalog_path(root: &Path) -> PathBuf {
    root.join("catalog.json")
}

/// The CLI's own recursive index, which is also what proves a refresh actually finished.
pub fn index_path(root: &Path) -> PathBuf {
    root.join("index.json")
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"{
      "title": "账号 119354",
      "roots": [
        { "id": "f1", "title": "第一章", "kind": "folder", "children": [
          { "id": "315187:903780", "title": "2-2. React和Vue描述页面的区别", "kind": "video",
            "video": { "id": "315187:903780", "duration_seconds": 1908.9, "source": "abc.evs" } },
          { "id": "315187:903781", "title": "2-3. 组件化", "kind": "video",
            "video": { "id": "315187:903781", "duration_seconds": 600.0, "source": "def.evs" } }
        ] },
        { "id": "f2", "title": "空章节", "kind": "folder", "children": [] }
      ]
    }"#;

    fn sample() -> Catalog {
        serde_json::from_str(SAMPLE).unwrap()
    }

    #[test]
    fn counts_and_flattens_every_video() {
        let catalog = sample();
        assert_eq!(catalog.video_count(), 2);
        let flat = flatten(&catalog);
        assert_eq!(flat.len(), 2);
        assert_eq!(flat[0].course, 315187);
        assert_eq!(flat[0].file, 903780);
        assert_eq!(flat[0].path, vec!["第一章".to_string()]);
        assert_eq!(flat[0].id(), "315187:903780");
    }

    #[test]
    fn selection_keeps_tree_order_and_ignores_unknown_ids() {
        let catalog = sample();
        let chosen = vec![
            "315187:903781".to_string(),
            "315187:903780".to_string(),
            "nope".to_string(),
        ];
        let found = catalog.selected(&chosen);
        assert_eq!(
            found.iter().map(Found::id).collect::<Vec<_>>(),
            vec!["315187:903780".to_string(), "315187:903781".to_string()]
        );
    }

    /// A selected video has to carry the folders above it: the export path is built from them, so an
    /// empty path means every lesson of every chapter lands in one directory under the same `01.`
    /// name.
    #[test]
    fn a_selected_video_carries_its_folders() {
        let nested = r#"{
          "title": "t",
          "roots": [{ "id": "r", "title": "前端课程", "kind": "folder", "children": [
            { "id": "r2", "title": "前端课程", "kind": "folder", "children": [
              { "id": "c", "title": "求职之道", "kind": "folder", "children": [
                { "id": "c2", "title": "求职之道", "kind": "folder", "children": [
                  { "id": "1:1", "title": "01. 导言.mp4", "kind": "video",
                    "video": { "id": "1:1", "duration_seconds": 60.0, "source": "e" } }
                ] } ] } ] } ] }]
        }"#;
        let catalog: Catalog = serde_json::from_str(nested).unwrap();
        let found = catalog.selected(&["1:1".to_string()]);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].path, vec!["前端课程".to_string(), "求职之道".to_string()]);

        // And it agrees with what the flattened list says, since the screen and the export read the
        // same tree two different ways.
        assert_eq!(flatten(&catalog)[0].path, found[0].path);
    }

    /// A folder checkbox asks this node for its ids; it must reach nested folders, not just the
    /// leaves directly under it.
    #[test]
    fn a_folder_reports_the_ids_of_everything_below_it() {
        let catalog = sample();
        assert_eq!(catalog.roots[0].video_ids().len(), 2);
        assert!(catalog.roots[1].video_ids().is_empty());
    }

    #[test]
    fn a_missing_optional_field_does_not_break_the_parse() {
        let minimal: Catalog = serde_json::from_str(r#"{"roots":[{"title":"x"}]}"#).unwrap();
        assert!(minimal.title.is_empty());
        assert_eq!(minimal.video_count(), 0);
    }

    /// The tree crosses to the frontend as JSON, so a field the view reads has to survive that
    /// hop under the name the view uses. A missing camelCase rename is invisible here and shows up
    /// as an empty column on screen, which is exactly how a duration column once rendered "—" for
    /// every lesson.
    #[test]
    fn every_field_keeps_its_name_through_the_wire() {
        let catalog = sample();
        let wire = serde_json::to_value(&catalog).unwrap();
        let video = &wire["roots"][0]["children"][0]["video"];
        assert!(video["durationSeconds"].is_number(), "the view reads durationSeconds: {video}");
        assert!(video.get("duration_seconds").is_none(), "snake_case leaked into the wire: {video}");
        assert_eq!(video["id"], "315187:903780");
        // And the parse of that same wire form works, so the round trip is closed.
        let back: Catalog = serde_json::from_value(wire).unwrap();
        assert_eq!(back.video_count(), 2);
        assert_eq!(flatten(&back)[0].duration_seconds, Some(1908.9));
    }
}
