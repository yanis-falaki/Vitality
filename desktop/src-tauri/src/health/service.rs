//! The app's health checks over time: `HealthService::refresh(probe)` runs them on this machine
//! (one run at a time) and keeps the result in the core (`Core::checks`), where Status, Setup,
//! the tray and `doctor` read it. The device probe of the server runs when `probe` is set (app
//! start, Setup's Test) or the server address changed; other runs reuse its last result.
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
}

impl HealthService {
    pub fn new(core: Arc<Core>, settings: Arc<Mutex<Settings>>, login: Arc<dyn StartAtLogin>) -> Arc<HealthService> {
        let screens = core.screens.clone();
        let env = MachineEnv::new(core.input.clone(), Box::new(move || Ok(screens.statuses())), core.store.root().to_path_buf(), login);
        Arc::new(HealthService { core, settings, env, device: Default::default(), running: Default::default() })
    }

    pub async fn refresh(&self, probe: bool) -> Vec<Check> {
        let _one_at_a_time = self.running.lock().await;
        let settings = self.settings.lock().unwrap().clone();
        let server = settings.server().unwrap_or_default();
        let mut last = self.device.lock().await;
        let device = match &*last {
            Some((s, c)) if *s == server && !probe => Device::Reuse(Some(c.clone())),
            _ => Device::Probe,
        };
        let result = checks(&self.env, &settings, device).await;
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
