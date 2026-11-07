//! The screens the owner set up, in their order, with names and saved portal permissions,
//! kept in `screens.json` (an ordered list of `ScreenEntry`).
//!
//! `Registry::load(path)` reads it (missing → empty); `add`, `rename`, `remove` and `set_token`
//! change it and write it back at once (atomically: a temporary file, then a rename).

use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::types::ScreenId;

/// One screen: its id, the owner's name for it, and the portal's restore token (the saved
/// permission that reopens it without a dialog).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScreenEntry {
    pub id: ScreenId,
    pub name: String,
    pub restore_token: Option<String>,
}

#[derive(Debug)]
pub struct Registry {
    path: PathBuf,
    entries: Vec<ScreenEntry>,
}

/// The longest name kept (longer names are cut).
pub const MAX_NAME: usize = 60;

impl Registry {
    /// The registry at `path`; empty if the file is missing or unreadable (said on stderr).
    pub fn load(path: &Path) -> Registry {
        let entries = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|e| {
                eprintln!("vitality-client: {} is unreadable ({e}); starting with no screens", path.display());
                vec![]
            }),
            Err(_) => vec![],
        };
        Registry { path: path.to_path_buf(), entries }
    }

    pub fn entries(&self) -> &[ScreenEntry] {
        &self.entries
    }

    pub fn get(&self, id: &ScreenId) -> Option<&ScreenEntry> {
        self.entries.iter().find(|e| &e.id == id)
    }

    /// A new screen at the end, named "Screen N" (the lowest N not in use).
    pub fn add(&mut self, restore_token: Option<String>) -> io::Result<ScreenEntry> {
        let n = (1..).find(|n| !self.entries.iter().any(|e| e.name == format!("Screen {n}"))).unwrap();
        let entry = ScreenEntry { id: self.new_id(), name: format!("Screen {n}"), restore_token };
        self.entries.push(entry.clone());
        self.save()?;
        Ok(entry)
    }

    /// Renames a screen; blank names are refused. Whether it was found.
    pub fn rename(&mut self, id: &ScreenId, name: &str) -> io::Result<bool> {
        let name: String = name.trim().chars().take(MAX_NAME).collect();
        if name.is_empty() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "a screen's name cannot be empty"));
        }
        if self.entries.iter().any(|e| &e.id != id && e.name == name) {
            // The name goes into shots: two screens with one name could not be told apart.
            return Err(io::Error::new(io::ErrorKind::InvalidInput, format!("another screen is already called {name:?}")));
        }
        let Some(entry) = self.entries.iter_mut().find(|e| &e.id == id) else { return Ok(false) };
        entry.name = name;
        self.save()?;
        Ok(true)
    }

    pub fn remove(&mut self, id: &ScreenId) -> io::Result<bool> {
        let before = self.entries.len();
        self.entries.retain(|e| &e.id != id);
        if self.entries.len() == before {
            return Ok(false);
        }
        self.save()?;
        Ok(true)
    }

    pub fn set_token(&mut self, id: &ScreenId, token: Option<String>) -> io::Result<()> {
        if let Some(entry) = self.entries.iter_mut().find(|e| &e.id == id) {
            if entry.restore_token != token {
                entry.restore_token = token;
                self.save()?;
            }
        }
        Ok(())
    }

    fn new_id(&self) -> ScreenId {
        let mut seed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0) as u64;
        loop {
            // A small mix of the clock (splitmix64), so ids made in one second differ.
            seed = seed.wrapping_add(0x9e37_79b9_7f4a_7c15);
            let mut z = seed;
            z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
            let id = ScreenId(format!("s{:08x}", (z ^ (z >> 31)) as u32));
            if self.get(&id).is_none() {
                return id;
            }
        }
    }

    fn save(&self) -> io::Result<()> {
        crate::paths::write_atomic(&self.path, &serde_json::to_vec_pretty(&self.entries)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_keeps_order_names_and_tokens() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state/screens.json");
        let mut r = Registry::load(&path);
        let a = r.add(Some("tok-a".into())).unwrap();
        let b = r.add(None).unwrap();
        r.rename(&b.id, "  Side  ").unwrap();
        r.set_token(&b.id, Some("tok-b".into())).unwrap();
        let again = Registry::load(&path);
        assert_eq!(again.entries(), r.entries());
        assert_eq!(again.entries()[0], ScreenEntry { id: a.id.clone(), name: "Screen 1".into(), restore_token: Some("tok-a".into()) });
        assert_eq!(again.entries()[1].name, "Side");
        assert_ne!(a.id, b.id);
    }

    #[test]
    fn names_default_to_the_lowest_free_screen_number() {
        let dir = tempfile::tempdir().unwrap();
        let mut r = Registry::load(&dir.path().join("screens.json"));
        let one = r.add(None).unwrap();
        let two = r.add(None).unwrap();
        assert_eq!((one.name.as_str(), two.name.as_str()), ("Screen 1", "Screen 2"));
        r.remove(&one.id).unwrap();
        assert_eq!(r.add(None).unwrap().name, "Screen 1");
        assert_eq!(r.add(None).unwrap().name, "Screen 3");
    }

    #[test]
    fn blank_names_are_refused_and_unknown_ids_reported() {
        let dir = tempfile::tempdir().unwrap();
        let mut r = Registry::load(&dir.path().join("screens.json"));
        let a = r.add(None).unwrap();
        assert!(r.rename(&a.id, "   ").is_err());
        let b = r.add(None).unwrap();
        assert!(r.rename(&b.id, "Screen 1").is_err(), "names stay unique");
        assert!(!r.rename(&ScreenId("nope".into()), "X").unwrap());
        assert!(!r.remove(&ScreenId("nope".into())).unwrap());
        assert!(r.rename(&a.id, &"x".repeat(100)).unwrap());
        assert_eq!(r.get(&a.id).unwrap().name.len(), MAX_NAME);
    }

    #[test]
    fn a_corrupt_file_loads_as_empty() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("screens.json");
        std::fs::write(&path, b"{not json").unwrap();
        assert!(Registry::load(&path).entries().is_empty());
    }
}
