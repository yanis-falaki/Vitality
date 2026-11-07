//! The Tauri commands the interface calls (typed in `ui/src/core.ts`): `status`, `pause`,
//! `resume`, `screens_list`, `screens_add`, `screens_repick`, `screens_rename`,
//! `screens_remove`, `screen_preview`. Each is a thin call into the `Core`; errors come back
//! as their message. The `state-changed` event is sent by the app (see `app.rs`).

use std::sync::Arc;

use base64::Engine;
use tauri::State;

use crate::runtime::Core;
use crate::state::PauseFor;
use crate::types::{ScreenId, ScreensView, StatusView};

type CoreState<'a> = State<'a, Arc<Core>>;

#[tauri::command]
pub fn status(core: CoreState<'_>) -> StatusView {
    core.status()
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
    let picked = core.screens.repick(&id).await;
    core.forget_screen(&id); // its next shot is a fresh start
    picked.map_err(|e| e.to_string())?;
    Ok(core.screens.view())
}

#[tauri::command]
pub fn screens_rename(core: CoreState<'_>, id: ScreenId, name: String) -> Result<ScreensView, String> {
    core.screens.rename(&id, &name).map_err(|e| e.to_string())?;
    Ok(core.screens.view())
}

#[tauri::command]
pub fn screens_remove(core: CoreState<'_>, id: ScreenId) -> Result<ScreensView, String> {
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
