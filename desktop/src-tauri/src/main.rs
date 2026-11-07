//! `vitality-client`: Vitality's desktop capture client.
//!
//! Without arguments it is the app (tray icon, window, capture); with arguments it is the small
//! CLI (`status`, `pause [MINUTES]`, `resume`, `doctor`), which talks to the running app.

mod activity;
mod app;
mod cli;
mod commands;
mod control;
mod log;
mod paths;
mod policy;
mod runtime;
mod screens;
mod session;
mod settings;
mod state;
mod store;
mod tray;
mod types;
mod upload;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        // WebKitGTK's DMA-BUF renderer crashes on some Wayland setups (NVIDIA: "Error 71
        // (Protocol error) dispatching to Wayland display"). Set before GTK or WebKit starts,
        // and only when the owner has not chosen.
        if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            // SAFETY: still single-threaded here; nothing else reads the environment yet.
            unsafe { std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1") };
        }
        app::run();
    } else {
        std::process::exit(cli::main(&args));
    }
}
