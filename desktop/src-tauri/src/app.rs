//! The app: one instance per session (a second start shows the first one's window and exits;
//! the installed app started outside its service, as from the app launcher, starts the service
//! instead and shows its window, so the copy that runs is always the supervised one),
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
use crate::settings::{self, Settings};
use crate::state::PauseFor;
use crate::tray::{self, TrayAction};
use crate::commands::AppState;
use crate::health::service::HealthService;
use crate::settings::{StartAtLogin, SystemdUnit};
use crate::upload::UploadTimings;

pub fn run() {
    let paths = Paths::from_env();
    let exe = std::env::current_exe().unwrap_or_default();
    if settings::hand_to_service(&exe, SystemdUnit.running_as_service()) {
        match SystemdUnit::start() {
            Ok(()) => return show_when_up(&paths.socket),
            Err(why) => eprintln!("vitality-client: {why}; running without the service"),
        }
    }
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
            crate::commands::settings_get,
            crate::commands::settings_save,
            crate::commands::free_up_space,
            crate::commands::how_it_works,
            crate::commands::checks,
            crate::commands::checks_run,
            crate::commands::server_test,
            crate::commands::setup_finish,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let settings = Arc::new(std::sync::Mutex::new(Settings::load(&paths.settings)));
            let login: Arc<dyn StartAtLogin> = Arc::new(SystemdUnit);
            let (core, health) = tauri::async_runtime::block_on(start(paths.clone(), settings.clone(), login.clone(), handle.clone()))?;
            app.manage(core.clone());
            app.manage(AppState { settings, settings_path: paths.settings.clone(), login, health, saving: Default::default() });
            if core.screens.count() == 0 || !core.status().setup_done {
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
async fn start(
    paths: Paths,
    shared: Arc<std::sync::Mutex<Settings>>,
    login: Arc<dyn StartAtLogin>,
    handle: AppHandle,
) -> Result<(Arc<Core>, Arc<HealthService>), Box<dyn std::error::Error>> {
    let settings = shared.lock().unwrap().clone();
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
        suspend_clock: Arc::new(crate::runtime::suspended_time),
    });
    core.apply_settings(&settings);
    // The health checks: once the saved screens have been reopened (with the device probe),
    // then every 30 s (reusing the probe's result).
    let health = HealthService::new(core.clone(), shared, login);
    tokio::spawn({
        let (core, health) = (core.clone(), health.clone());
        async move {
            while !core.screens.opened_all() {
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
            health.refresh(true).await;
            loop {
                tokio::time::sleep(Duration::from_secs(30)).await;
                health.refresh(false).await;
            }
        }
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
    Ok((core, health))
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


/// Asks the running app to show its window, waiting up to 10 s for its socket to answer.
fn show_when_up(socket: &std::path::Path) {
    for _ in 0..50 {
        if control::request(socket, &ControlRequest::Show).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    eprintln!("vitality-client: the service started but did not answer; see `journalctl --user -u vitality-client`");
}
