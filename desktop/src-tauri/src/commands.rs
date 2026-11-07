//! The Tauri commands the interface calls (typed in `ui/src/core.ts`): `status`, `pause`,
//! `resume`, `status_page`, `screens_list`, `screens_add`, `screens_repick`, `screens_rename`,
//! `screens_remove`, `screen_preview`, `settings_get`, `settings_save`, `free_up_space`,
//! `how_it_works`, `checks`, `checks_run`, `server_test`, `setup_finish`. Each is a thin call into the `Core`; errors come back
//! as their message. The `state-changed` event is sent by the app (see `app.rs`).

use std::sync::Arc;

use base64::Engine;
use tauri::State;

use crate::runtime::Core;
use crate::state::PauseFor;
use crate::health::service::HealthService;
use crate::settings::{FieldError, Settings, StartAtLogin};
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
pub async fn screens_add(core: CoreState<'_>, app: App<'_>) -> Result<ScreensView, String> {
    let added = core.screens.add().await;
    core.notify_changed();
    app.health.refresh_soon();
    added.map_err(|e| e.to_string())?;
    Ok(core.screens.view())
}

#[tauri::command]
pub async fn screens_repick(core: CoreState<'_>, app: App<'_>, id: ScreenId) -> Result<ScreensView, String> {
    let picked = core.screens.repick(&id).await;
    app.health.refresh_soon();
    picked.map_err(|e| e.to_string())?;
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
pub async fn screens_remove(core: CoreState<'_>, app: App<'_>, id: ScreenId) -> Result<ScreensView, String> {
    core.screens.remove(&id).map_err(|e| e.to_string())?;
    app.health.refresh_soon();
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
    /// The settings the app runs with (shared with the health checks).
    pub settings: Arc<std::sync::Mutex<Settings>>,
    pub settings_path: std::path::PathBuf,
    pub login: Arc<dyn StartAtLogin>,
    pub health: Arc<HealthService>,
    /// One save at a time.
    pub saving: tokio::sync::Mutex<()>,
}

type App<'a> = State<'a, AppState>;

/// The settings, and what was wrong in `client.toml` (those fields use their defaults).
#[derive(serde::Serialize)]
pub struct SettingsView {
    pub settings: Settings,
    pub problems: Vec<String>,
}

#[tauri::command]
pub fn settings_get(app: App<'_>) -> SettingsView {
    let problems = Settings::load_full(&app.settings_path).problems;
    SettingsView { settings: app.settings.lock().unwrap().clone(), problems }
}

/// The reply of `settings_save`: what is wrong (then nothing was saved; `field` is a setting's
/// name, or "file" when it could not be written), or the saved settings and, if start at login
/// could not be changed, why.
#[derive(serde::Serialize)]
pub struct SaveReply {
    pub errors: Vec<FieldError>,
    pub settings: Settings,
    pub login_error: Option<String>,
}

/// Saves settings from the Settings page (each field checked, types included: an empty field is
/// an error) and applies them. Returns as soon as the file is written; the checks rerun in the
/// background.
#[tauri::command]
pub async fn settings_save(core: CoreState<'_>, app: App<'_>, settings: serde_json::Value) -> Result<SaveReply, String> {
    Ok(save(&core, &app, settings).await)
}

async fn save(core: &Arc<Core>, app: &AppState, value: serde_json::Value) -> SaveReply {
    let _one = app.saving.lock().await;
    let current = app.settings.lock().unwrap().clone();
    let (settings, errors) = Settings::from_fields(|k| value.get(k).cloned());
    if !errors.is_empty() {
        return SaveReply { errors, settings: current, login_error: None };
    }
    let path = app.settings_path.clone();
    let to_save = settings.clone();
    let written = tauri::async_runtime::spawn_blocking(move || to_save.save(&path)).await.map_err(|e| e.to_string()).and_then(|r| r);
    if let Err(message) = written {
        return SaveReply { errors: vec![FieldError { field: "file".into(), message }], settings: current, login_error: None };
    }
    let login_error = if settings.start_at_login != current.start_at_login {
        let (login, on) = (app.login.clone(), settings.start_at_login);
        tauri::async_runtime::spawn_blocking(move || login.set(on)).await.map_err(|e| e.to_string()).and_then(|r| r).err()
    } else {
        None
    };
    core.apply_settings(&settings);
    *app.settings.lock().unwrap() = settings.clone();
    app.health.refresh_soon();
    SaveReply { errors: vec![], settings, login_error }
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

/// The health checks as last run; null until the first run (after the saved screens reopened).
#[tauri::command]
pub fn checks(core: CoreState<'_>) -> Option<Vec<Check>> {
    core.checks()
}

/// Runs the checks now, probing the server for this device too.
#[tauri::command]
pub async fn checks_run(app: App<'_>) -> Result<Vec<Check>, String> {
    Ok(app.health.refresh(true).await)
}

/// Setup's Test: saves `server` as the address (if it changed), then runs the checks with the
/// device probe. Errors are the address's.
#[tauri::command]
pub async fn server_test(core: CoreState<'_>, app: App<'_>, server: String) -> Result<Vec<Check>, String> {
    let mut value = serde_json::to_value(app.settings.lock().unwrap().clone()).map_err(|e| e.to_string())?;
    if value["server"] != server.as_str() {
        value["server"] = server.into();
        let reply = save(&core, &app, value).await;
        if let Some(e) = reply.errors.first() {
            return Err(e.message.clone());
        }
    }
    Ok(app.health.refresh(true).await)
}

/// Setup's Done: remembered, so Setup is shown again only when an essential check fails.
#[tauri::command]
pub async fn setup_finish(core: CoreState<'_>) -> Result<StatusView, String> {
    core.finish_setup().await;
    Ok(core.status())
}
