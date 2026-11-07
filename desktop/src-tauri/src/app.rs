//! The app: starts Tauri with the window (hidden until asked for) and, later, the core.
//!
//! `run()` never returns until the owner quits.

pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("the Tauri app failed to start");
}
