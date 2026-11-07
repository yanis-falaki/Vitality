//! The Tauri commands the interface calls (typed in `ui/src/core.ts`): `status`, `pause`,
//! `resume`, `status_page`, `screens_list`, `screens_add`, `screens_repick`, `screens_rename`,
//! `screens_remove`, `screen_preview`, `settings_get`, `settings_save`, `free_up_space`,
//! `how_it_works`, `checks`, `checks_run`. Each is a thin call into the `Core`; errors come back
//! as their message. The `state-changed` event is sent by the app (see `app.rs`).

use std::sync::Arc;

use base64::Engine;
use tauri::State;

use crate::runtime::Core;
use crate::state::PauseFor;
use crate::health::{self, MachineEnv};
use crate::settings::{FieldError, Settings, StartAtLogin, SystemdUnit};
use crate::types::{Check, Freed, RuleText, ScreenId, ScreensView, StatusPage, StatusView};

type CoreState<'a> = State<'a, Arc<Core>>;

#[tauri::command]
pub fn status(core: CoreState<'_>) -> StatusView {
    core.status()
}

/// What the Status page shows: the status, today's counts, disk use, the checks and the last
/// 50 decisions.
#[tauri::command]
pub async fn status_page(core: CoreState<'_>) -> Result<StatusPage, String> {
    let core = core.inner().clone();
    tauri::async_runtime::spawn_blocking(move || core.status_page()).await.map_err(|e| e.to_string())
}

/// `minutes: null` pauses until resumed.
#[tauri::command]
pub async fn pause(core: CoreState<'_>, minutes: Option<u32>) -> Result<StatusView, String> {
    let how = minutes.map_or(PauseFor::UntilResumed, PauseFor::Minutes);
    Ok(core.pause(how).await)
}

#[tauri::command]
pub async fn resume(core: CoreState<'_>) -> Result<StatusView, String> {
    Ok(core.resume().await)
}

#[tauri::command]
pub fn screens_list(core: CoreState<'_>) -> ScreensView {
    core.screens.view()
}

#[tauri::command]
pub async fn screens_add(core: CoreState<'_>) -> Result<ScreensView, String> {
    let added = core.screens.add().await;
    core.notify_changed();
    added.map_err(|e| e.to_string())?;
    Ok(core.screens.view())
}

#[tauri::command]
pub async fn screens_repick(core: CoreState<'_>, id: ScreenId) -> Result<ScreensView, String> {
    core.screens.repick(&id).await.map_err(|e| e.to_string())?;
    core.forget_screen(&id); // a new share: its next shot is a fresh start
    Ok(core.screens.view())
}

#[tauri::command]
pub fn screens_rename(core: CoreState<'_>, id: ScreenId, name: String) -> Result<ScreensView, String> {
    core.screens.rename(&id, &name).map_err(|e| e.to_string())?;
    Ok(core.screens.view())
}

/// Async so the share is closed on the runtime, not on the interface's thread.
#[tauri::command]
pub async fn screens_remove(core: CoreState<'_>, id: ScreenId) -> Result<ScreensView, String> {
    core.screens.remove(&id).map_err(|e| e.to_string())?;
    core.forget_screen(&id);
    Ok(core.screens.view())
}

/// A `data:image/jpeg;base64,…` URL of the screen's newest frame, or null before the first.
#[tauri::command]
pub async fn screen_preview(core: CoreState<'_>, id: ScreenId) -> Result<Option<String>, String> {
    let core = core.inner().clone();
    let jpeg = tauri::async_runtime::spawn_blocking(move || core.screens.preview(&id)).await.map_err(|e| e.to_string())?;
    Ok(jpeg.map(|j| format!("data:image/jpeg;base64,{}", base64::engine::general_purpose::STANDARD.encode(j))))
}

/// What the Settings and Setup commands need besides the core.
pub struct AppState {
    pub settings: std::sync::Mutex<Settings>,
    pub settings_path: std::path::PathBuf,
    pub login: Box<dyn StartAtLogin>,
}

type App<'a> = State<'a, AppState>;

#[tauri::command]
pub fn settings_get(app: App<'_>) -> Settings {
    app.settings.lock().unwrap().clone()
}

/// The reply of `settings_save`: what is wrong (nothing saved then), or the saved settings and,
/// if start at login could not be changed, why.
#[derive(serde::Serialize)]
pub struct SaveReply {
    pub errors: Vec<FieldError>,
    pub settings: Settings,
    pub login_error: Option<String>,
}

#[tauri::command]
pub async fn settings_save(core: CoreState<'_>, app: App<'_>, settings: Settings) -> Result<SaveReply, String> {
    let errors = settings.validate();
    if !errors.is_empty() {
        return Ok(SaveReply { errors, settings: app.settings.lock().unwrap().clone(), login_error: None });
    }
    settings.save(&app.settings_path)?;
    let login_error = match app.login.enabled() {
        Ok(on) if on == settings.start_at_login => None,
        _ => app.login.set(settings.start_at_login).err(),
    };
    core.apply_settings(&settings);
    *app.settings.lock().unwrap() = settings.clone();
    refresh_checks(core.inner().clone(), settings.clone(), None).await;
    Ok(SaveReply { errors: vec![], settings, login_error })
}

/// Deletes the local copies of interactions already uploaded.
#[tauri::command]
pub async fn free_up_space(core: CoreState<'_>) -> Result<Freed, String> {
    let core = core.inner().clone();
    let freed = tauri::async_runtime::spawn_blocking(move || {
        let freed = core.store.free_up_space();
        core.recount();
        freed
    })
    .await
    .map_err(|e| e.to_string())?;
    freed.map_err(|e| e.to_string())
}

/// The rules in plain words with the current numbers.
#[tauri::command]
pub fn how_it_works(app: App<'_>) -> Vec<RuleText> {
    crate::policy::explain::explain(&app.settings.lock().unwrap().policy())
}

/// The health checks as last run (run now if they never were).
#[tauri::command]
pub async fn checks(core: CoreState<'_>, app: App<'_>) -> Result<Vec<Check>, String> {
    let cached = core.checks();
    if !cached.is_empty() {
        return Ok(cached);
    }
    let settings = app.settings.lock().unwrap().clone();
    Ok(refresh_checks(core.inner().clone(), settings, None).await)
}

/// Runs the checks now; with `server`, the server parts test that address instead (Setup's
/// Test button) and the result is not kept.
#[tauri::command]
pub async fn checks_run(core: CoreState<'_>, app: App<'_>, server: Option<String>) -> Result<Vec<Check>, String> {
    let settings = app.settings.lock().unwrap().clone();
    Ok(refresh_checks(core.inner().clone(), settings, server).await)
}

/// Runs the checks on this machine (kept in the core unless `server` overrides the address).
pub async fn refresh_checks(core: Arc<Core>, mut settings: Settings, server: Option<String>) -> Vec<Check> {
    let keep = server.is_none();
    if let Some(s) = server {
        settings.server = s;
    }
    let screens = core.screens.clone();
    let env = MachineEnv::new(core.input.clone(), Box::new(move || Ok(screens.statuses())), core.store.root().to_path_buf(), Box::new(SystemdUnit));
    let checks = health::checks(&env, &settings).await;
    if keep {
        core.set_checks(checks.clone());
    }
    checks
}
