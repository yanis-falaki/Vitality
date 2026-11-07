//! `vitality-client`: Vitality's desktop capture client.
//!
//! Without arguments it is the app (tray icon, window, capture); with arguments it is the small
//! CLI (`status`, `pause [MINUTES]`, `resume`, `doctor`), which talks to the running app.

#[allow(dead_code)] // wired into the runtime in Task 7
mod activity;
mod app;
mod cli;
#[allow(dead_code)] // wired into the runtime in Task 7
mod policy;
#[allow(dead_code)] // wired into the runtime in Task 7
mod session;
#[allow(dead_code)] // used from Task 5 on
mod types;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        app::run();
    } else {
        std::process::exit(cli::main(&args));
    }
}
