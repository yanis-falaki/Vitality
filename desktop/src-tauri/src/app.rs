//! The app: one instance per session (a second start shows the first one's window and exits),
//! the core with the real input, portal and frames, the control socket, the tray icon,
//! and the window (hidden at start unless no screen is set up; closing it only hides it).
//!
//! `run()` returns when the owner quits.

use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager, WindowEvent};

use crate::activity::{EvdevInput, SystemDevices};
use crate::control::{self, ControlRequest};
use crate::paths::Paths;
use crate::runtime::{Core, Parts, Runtime};
use crate::screens::feed::GstFeeds;
use crate::screens::portal::XdgPortal;
use crate::settings::Settings;
use crate::state::PauseFor;
use crate::tray::{self, TrayAction};
use crate::upload::UploadTimings;

pub fn run() {
    let paths = Paths::from_env();
    let _instance = match control::lock_instance(&paths.socket) {
        Ok(lock) => lock,
        Err(_) => {
            let shown = control::request(&paths.socket, &ControlRequest::Show).is_ok();
            eprintln!("vitality-client: already running{}", if shown { "; its window is shown" } else { "" });
            return;
        }
    };
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            crate::commands::status,
            crate::commands::status_page,
            crate::commands::pause,
            crate::commands::resume,
            crate::commands::screens_list,
            crate::commands::screens_add,
            crate::commands::screens_repick,
            crate::commands::screens_rename,
            crate::commands::screens_remove,
            crate::commands::screen_preview,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let core = tauri::async_runtime::block_on(start(paths.clone(), handle.clone()))?;
            app.manage(core.clone());
            if core.screens.count() == 0 {
                show(&handle);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("the Tauri app failed to start");
}

/// Starts the core and everything around it (inside the async runtime).
async fn start(paths: Paths, handle: AppHandle) -> Result<Arc<Core>, Box<dyn std::error::Error>> {
    let settings = Settings::load(&paths.settings);
    let feeds = GstFeeds::new()?;
    let core = Runtime::start(Parts {
        paths: paths.clone(),
        policy: settings.policy(),
        webp_quality: settings.webp_quality,
        server: settings.server(),
        upload_timings: UploadTimings::default(),
        input: Arc::new(EvdevInput::start(Box::new(SystemDevices), Duration::from_secs(30))),
        portal: Arc::new(XdgPortal),
        feeds: Arc::new(feeds),
        tick: Duration::from_millis(250),
    });
    *core.on_show.lock().unwrap() = Some(Box::new({
        let handle = handle.clone();
        move || show(&handle)
    }));
    if let Err(e) = control::serve(&paths.socket, core.clone()) {
        eprintln!("vitality-client: no control socket at {} ({e}); the CLI will not reach this app", paths.socket.display());
    }
    // The interface refreshes on `state-changed`.
    let mut changes = core.subscribe();
    let h = handle.clone();
    tokio::spawn(async move {
        while changes.recv().await.is_ok() || !changes.is_closed() {
            let _ = h.emit("state-changed", ());
        }
    });
    let act: Arc<dyn Fn(TrayAction) + Send + Sync> = {
        let core = core.clone();
        let handle = handle.clone();
        Arc::new(move |action| on_tray(core.clone(), handle.clone(), action))
    };
    tokio::spawn(tray::start(core.clone(), act));
    Ok(core)
}

fn on_tray(core: Arc<Core>, handle: AppHandle, action: TrayAction) {
    tauri::async_runtime::spawn(async move {
        match action {
            TrayAction::TogglePause if core.status().pause.paused => drop(core.resume().await),
            TrayAction::TogglePause => drop(core.pause(PauseFor::UntilResumed).await),
            TrayAction::Pause(how) => drop(core.pause(how).await),
            TrayAction::Resume => drop(core.resume().await),
            TrayAction::Open => show(&handle),
            TrayAction::Quit => {
                core.quit().await;
                handle.exit(0);
            }
        }
    });
}

fn show(handle: &AppHandle) {
    if let Some(window) = handle.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}
