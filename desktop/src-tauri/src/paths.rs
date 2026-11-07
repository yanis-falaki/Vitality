//! Where the app keeps things (spec §10). `Paths::from_env()` follows the XDG variables, whose
//! defaults give the spec's paths; `Paths::under(dir)` puts everything in one folder (tests).

use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct Paths {
    /// `~/.local/share/vitality/client/captures/`: interactions and their markers.
    pub captures: PathBuf,
    /// `~/.config/vitality/client.toml`.
    pub settings: PathBuf,
    /// `~/.local/state/vitality/client/screens.json`.
    pub screens: PathBuf,
    /// `~/.local/state/vitality/client/state.json`: the pause.
    pub state: PathBuf,
    /// `~/.local/state/vitality/client/capture.log`: the recent decisions.
    pub log: PathBuf,
    /// `$XDG_RUNTIME_DIR/vitality/client.sock`: the control socket.
    pub socket: PathBuf,
}

impl Paths {
    pub fn from_env() -> Paths {
        let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"));
        let xdg = |var: &str, default: &str| {
            std::env::var_os(var).map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home.join(default))
        };
        let data = xdg("XDG_DATA_HOME", ".local/share").join("vitality/client");
        let state = xdg("XDG_STATE_HOME", ".local/state").join("vitality/client");
        let config = xdg("XDG_CONFIG_HOME", ".config").join("vitality");
        let runtime = std::env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
        Paths {
            captures: data.join("captures"),
            settings: config.join("client.toml"),
            screens: state.join("screens.json"),
            state: state.join("state.json"),
            log: state.join("capture.log"),
            socket: runtime.join("vitality/client.sock"),
        }
    }

    #[cfg(test)]
    pub fn under(dir: &Path) -> Paths {
        Paths {
            captures: dir.join("captures"),
            settings: dir.join("client.toml"),
            screens: dir.join("screens.json"),
            state: dir.join("state.json"),
            log: dir.join("capture.log"),
            socket: dir.join("client.sock"),
        }
    }
}

/// Writes `bytes` to `path` atomically and durably: a temporary file beside it, synced, renamed
/// over `path`, then the folder synced, so a power loss leaves the old file or the new one.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let dir = path.parent().filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new("."));
    std::fs::create_dir_all(dir)?;
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let mut f = std::fs::File::create(&tmp)?;
    f.write_all(bytes)?;
    f.sync_all()?;
    drop(f);
    std::fs::rename(&tmp, path)?;
    sync_dir(dir)
}

/// Syncs a folder's entries (after a rename or a new file in it).
pub fn sync_dir(dir: &Path) -> std::io::Result<()> {
    std::fs::File::open(dir)?.sync_all()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_the_specs_paths() {
        // Only checks the shape: the variables of the test process are left alone.
        let p = Paths::from_env();
        assert!(p.captures.ends_with("vitality/client/captures"));
        assert!(p.settings.ends_with("vitality/client.toml"));
        assert!(p.screens.ends_with("vitality/client/screens.json"));
        assert!(p.state.ends_with("vitality/client/state.json"));
        assert!(p.log.ends_with("vitality/client/capture.log"));
        assert!(p.socket.ends_with("vitality/client.sock"));
    }

    #[test]
    fn atomic_write_replaces_the_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a/b.json");
        write_atomic(&path, b"1").unwrap();
        write_atomic(&path, b"2").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"2");
        assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
    }
}
