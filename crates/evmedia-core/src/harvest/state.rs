//! Persist facts that cannot be recovered once a playback context has been released.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::{collections::{BTreeMap, BTreeSet, HashMap}, fs, io::Write, path::Path};

#[derive(Default, Serialize, Deserialize)]
pub struct State {
    pub known: BTreeMap<String, String>,
    pub places: HashMap<String, u32>,
    pub seen: BTreeSet<u32>,
}

impl State {
    pub fn load(output: &Path) -> Result<Self> {
        let path = output.join("grab-state.json");
        match fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes).context("read grab-state.json"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error).context("read grab-state.json"),
        }
    }

    pub fn save(&self, output: &Path) -> Result<()> {
        let mut placed = BTreeMap::new();
        for (file, index) in &self.places {
            if let Some(other) = placed.insert(index, file) {
                bail!("conflicting files at index {index}: {other} and {file}; select one lesson and use a separate output directory");
            }
        }
        let path = output.join("grab-state.json");
        let temporary = path.with_extension("json.tmp");
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&serde_json::to_vec(self)?)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, &path).context("save grab-state.json")
    }
}
