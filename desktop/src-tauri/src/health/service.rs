//! The app's health checks over time: `HealthService::refresh(probe)` runs them on this machine
//! (one run at a time) and keeps the result in the core (`Core::checks`), where Status, Setup,
//! the tray and `doctor` read it. The device probe of the server runs when `probe` is set (app
//! start, Setup's Test), the server address changed or the server became reachable again;
//! other runs reuse its last result. A real upload's answer (`Uploader::acceptance`) beats any
//! probe: when there is one, it is what the check says.
//! `refresh_soon()` runs it in the background (after a Settings save or a screens change).

use std::sync::{Arc, Mutex};

use super::{checks, Device, MachineEnv};

use crate::runtime::Core;
use crate::settings::{Settings, StartAtLogin};
use crate::types::Check;

pub struct HealthService {
    core: Arc<Core>,
    settings: Arc<Mutex<Settings>>,
    env: MachineEnv,
    /// The last device probe: for which server, and its result.
    device: tokio::sync::Mutex<Option<(String, Check)>>,
    running: tokio::sync::Mutex<()>,
    /// Whether the server answered at the last run.
    server_was_up: Mutex<Option<bool>>,
}

/// How to tell this device's acceptance: a real upload's answer first, else a probe when asked
/// (or the server is new), else the last probe's result.
pub fn device_plan(evidence: Option<crate::upload::Acceptance>, last: Option<&(String, Check)>, server: &str, probe: bool) -> Device {
    match (evidence, last) {
        (Some(a), _) if a.server == server => Device::Uploaded { accepted: a.accepted, at: a.at },
        (_, Some((s, c))) if s == server && !probe => Device::Reuse(Some(c.clone())),
        _ => Device::Probe,
    }
}

/// Whether to probe again after a run: the server came back and the device was only reused.
pub fn reprobe(was_up: Option<bool>, now_up: bool, reused: bool) -> bool {
    reused && now_up && was_up == Some(false)
}

impl HealthService {
    pub fn new(core: Arc<Core>, settings: Arc<Mutex<Settings>>, login: Arc<dyn StartAtLogin>) -> Arc<HealthService> {
        let screens = core.screens.clone();
        let env = MachineEnv::new(core.input.clone(), Box::new(move || Ok(screens.statuses())), core.store.root().to_path_buf(), login);
        Arc::new(HealthService { core, settings, env, device: Default::default(), running: Default::default(), server_was_up: Default::default() })
    }

    pub async fn refresh(&self, probe: bool) -> Vec<Check> {
        let _one_at_a_time = self.running.lock().await;
        let settings = self.settings.lock().unwrap().clone();
        let server = settings.server().unwrap_or_default();
        let mut last = self.device.lock().await;
        let evidence = self.core.uploader.acceptance();
        let device = device_plan(evidence.clone(), last.as_ref(), &server, probe);
        let reused = matches!(device, Device::Reuse(_));
        let mut result = checks(&self.env, &settings, device).await;
        let up = result.iter().any(|c| c.id == "server_health" && c.ok);
        let was_up = self.server_was_up.lock().unwrap().replace(up);
        if reprobe(was_up, up, reused) {
            result = checks(&self.env, &settings, Device::Probe).await;
        }
        if let Some(c) = result.iter().find(|c| c.id == "device_accepted") {
            *last = Some((server, c.clone()));
        }
        self.core.set_checks(result.clone());
        result
    }

    pub fn refresh_soon(self: &Arc<Self>) {
        let me = self.clone();
        tokio::spawn(async move { me.refresh(false).await });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::samples_page::check;

    fn acceptance(server: &str, accepted: bool) -> crate::upload::Acceptance {
        crate::upload::Acceptance { server: server.into(), accepted, at: chrono::Utc::now() }
    }

    #[test]
    fn an_uploads_answer_beats_the_probe() {
        let last = ("http://box".to_string(), check("device_accepted", false, false, "This device accepted", "not tested: no connection", None));
        assert!(matches!(device_plan(Some(acceptance("http://box", true)), Some(&last), "http://box", true), Device::Uploaded { accepted: true, .. }));
        assert!(matches!(device_plan(Some(acceptance("http://old", true)), Some(&last), "http://box", false), Device::Reuse(_)), "another server's answer does not count");
        assert!(matches!(device_plan(None, Some(&last), "http://box", false), Device::Reuse(_)));
        assert!(matches!(device_plan(None, Some(&last), "http://new", false), Device::Probe));
        assert!(matches!(device_plan(None, None, "http://box", false), Device::Probe));
    }

    #[test]
    fn the_probe_runs_again_when_the_server_comes_back() {
        assert!(reprobe(Some(false), true, true));
        assert!(!reprobe(Some(true), true, true), "it was up already");
        assert!(!reprobe(None, true, true), "the first run probes anyway");
        assert!(!reprobe(Some(false), false, true), "still down");
        assert!(!reprobe(Some(false), true, false), "already decided by an upload or a probe");
    }
}
