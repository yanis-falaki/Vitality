//! The command line: `vitality-client status | pause [MINUTES] | resume | doctor`.
//!
//! `main(args)` parses the arguments, does the command and returns the process's exit code
//! (0 done, 1 the command failed — for `doctor`, an essential check — 2 the arguments were
//! wrong).

use chrono::{DateTime, Local, TimeZone, Utc};

use crate::control::{self, ControlReply, ControlRequest};
use crate::paths::Paths;
use crate::types::{CaptureMode, StatusView, StopReason, UploadState};

/// One parsed command.
#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    Status,
    Pause { minutes: Option<u32> },
    Resume,
    Doctor,
}

const USAGE: &str = "usage: vitality-client [status | pause [MINUTES] | resume | doctor]\n\
                     Without arguments it starts the app.";

/// The command named by `args` (the arguments after the program's name), or the reason it is wrong.
pub fn parse(args: &[String]) -> Result<Command, String> {
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    match words.as_slice() {
        ["status"] => Ok(Command::Status),
        ["pause"] => Ok(Command::Pause { minutes: None }),
        ["pause", m] => match m.parse::<u32>() {
            Ok(n) if n > 0 => Ok(Command::Pause { minutes: Some(n) }),
            _ => Err(format!("pause takes a whole number of minutes, not {m:?}")),
        },
        ["resume"] => Ok(Command::Resume),
        ["doctor"] => Ok(Command::Doctor),
        _ => Err(format!("unknown command: {}", words.join(" "))),
    }
}

pub fn main(args: &[String]) -> i32 {
    if matches!(args.first().map(String::as_str), Some("-h" | "--help" | "help")) {
        println!("{USAGE}");
        return 0;
    }
    match parse(args) {
        Ok(command) => run(command),
        Err(why) => {
            eprintln!("vitality-client: {why}\n{USAGE}");
            2
        }
    }
}

fn run(command: Command) -> i32 {
    let request = match command {
        Command::Status => ControlRequest::Status,
        Command::Pause { minutes } => ControlRequest::Pause { minutes },
        Command::Resume => ControlRequest::Resume,
        Command::Doctor => return doctor(),
    };
    match control::request(&Paths::from_env().socket, &request) {
        Ok(ControlReply { ok: true, status: Some(status), .. }) => {
            println!("{}", summary(&status, &Local));
            0
        }
        Ok(reply) => {
            eprintln!("vitality-client: {}", reply.error.unwrap_or_else(|| "the app refused the request".into()));
            1
        }
        Err(_) => {
            println!("Vitality isn't running");
            1
        }
    }
}

/// `doctor`: every health check on this machine, ✓/✗ with fixes; exit 1 if an essential one
/// fails. Screens are asked of the running app (they fail when it is not running).
fn doctor() -> i32 {
    use crate::activity::{EvdevInput, SystemDevices};
    use crate::health::{report, MachineEnv};
    use crate::settings::{Settings, SystemdUnit};
    let paths = Paths::from_env();
    let settings = Settings::load(&paths.settings);
    let socket = paths.socket.clone();
    let screens = move || match control::request(&socket, &ControlRequest::Screens) {
        Ok(ControlReply { screens: Some(view), .. }) => Ok(view.screens),
        _ => Err("Vitality isn't running, so its screens are not open".to_string()),
    };
    let input = std::sync::Arc::new(EvdevInput::start(Box::new(SystemDevices), std::time::Duration::from_secs(3600)));
    let env = MachineEnv::new(input, Box::new(screens), paths.captures.clone(), Box::new(SystemdUnit));
    let runtime = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("vitality-client: {e}");
            return 1;
        }
    };
    let checks = runtime.block_on(crate::health::checks(&env, &settings));
    let (text, code) = report(&checks);
    print!("{text}");
    code
}

/// The few lines `status`, `pause` and `resume` print, with times in `tz`.
pub fn summary<Tz: TimeZone>(s: &StatusView, tz: &Tz) -> String
where
    Tz::Offset: std::fmt::Display,
{
    let at = |t: &DateTime<Utc>| t.with_timezone(tz).format("%H:%M").to_string();
    let capture = match (s.capture.mode, s.capture.stopped_by) {
        (CaptureMode::Active, _) => format!(
            "Recording · active interaction since {} · {} shots",
            s.capture.since.as_ref().map(at).unwrap_or_default(),
            s.capture.shots
        ),
        (CaptureMode::Passive, _) => format!(
            "Recording · passive interaction since {} · {} shots",
            s.capture.since.as_ref().map(at).unwrap_or_default(),
            s.capture.shots
        ),
        (CaptureMode::Idle, _) => match &s.capture.next_check_at {
            Some(t) => format!("Recording · idle · passive check at {}", t.with_timezone(tz).format("%H:%M:%S")),
            None => "Recording · idle".into(),
        },
        (CaptureMode::Stopped, Some(StopReason::Paused)) => match &s.pause.until {
            Some(u) => format!("Paused until {}", at(u)),
            None => "Paused until resumed".into(),
        },
        (CaptureMode::Stopped, _) => "Stopping".into(),
    };
    let screens = match (s.screens, s.screens_live) {
        (0, _) => "Screens: none set up (open Vitality to add one)".to_string(),
        (n, live) if live == n => format!("Screens: {n}, all live"),
        (n, live) if !s.screens_attention.is_empty() => {
            format!("Screens: {n}, {live} live (to pick again in Vitality or stopped: {})", s.screens_attention.join(", "))
        }
        (n, live) => format!("Screens: {n}, {live} live"),
    };
    let input = match s.input.as_str() {
        "ok" => "Input: ok".to_string(),
        "permission_denied" => "Input: no access (sudo usermod -aG input $USER, then log out and in)".to_string(),
        _ => "Input: no keyboard or mouse found".to_string(),
    };
    let last = s.upload.last_upload.as_ref().map(|t| format!(" · last {}", at(t))).unwrap_or_default();
    let upload = match &s.upload.state {
        UploadState::Ok => format!("Upload: ok · {} pending{last}", s.upload.pending),
        UploadState::Offline => format!("Upload: server unreachable, retrying · {} pending{last}", s.upload.pending),
        UploadState::Rejected { device } => {
            format!("Upload: refused · allow the device \"{device}\" on the server · {} pending", s.upload.pending)
        }
        UploadState::Disabled => format!("Upload: off (no server set) · {} kept here", s.upload.pending),
    };
    let mut lines = vec![capture, screens, input, upload];
    if s.upload.failed > 0 {
        let last = s.upload.last_failure.as_ref().map(|f| format!(" · latest: {f}")).unwrap_or_default();
        lines.push(format!("Warning: {} interaction(s) refused by the server for good{last}", s.upload.failed));
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    #[test]
    fn parses_each_command() {
        assert_eq!(parse(&args(&["status"])), Ok(Command::Status));
        assert_eq!(parse(&args(&["pause"])), Ok(Command::Pause { minutes: None }));
        assert_eq!(parse(&args(&["pause", "30"])), Ok(Command::Pause { minutes: Some(30) }));
        assert_eq!(parse(&args(&["resume"])), Ok(Command::Resume));
        assert_eq!(parse(&args(&["doctor"])), Ok(Command::Doctor));
    }

    #[test]
    fn rejects_bad_arguments() {
        assert!(parse(&args(&["pause", "0"])).is_err());
        assert!(parse(&args(&["pause", "soon"])).is_err());
        assert!(parse(&args(&["status", "now"])).is_err());
        assert!(parse(&args(&["record"])).is_err());
        assert_eq!(main(&args(&["record"])), 2);
    }

    #[test]
    fn summaries_in_plain_words() {
        use crate::types::samples;
        let utc = Utc;
        assert_eq!(
            summary(&samples::status(), &utc),
            "Recording · active interaction since 20:02 · 14 shots\nScreens: 2, 1 live (to pick again in Vitality or stopped: Screen 2)\n\
             Input: ok\nUpload: ok · 0 pending · last 20:01"
        );
        let paused = summary(&samples::status_paused(), &utc);
        assert!(paused.starts_with("Paused until 21:00\nScreens: 2, all live\nInput: no access"), "{paused}");
        assert!(paused.contains("Upload: refused · allow the device \"desktop\" on the server · 3 pending\n"), "{paused}");
        assert!(paused.ends_with("Warning: 1 interaction(s) refused by the server for good · latest: 2026-11-07/17-00-00: too large for the server (413)"), "{paused}");
        let idle = summary(&samples::status_idle(), &utc);
        assert!(idle.starts_with("Recording · idle · passive check at 20:03:15\nScreens: none set up"), "{idle}");
        assert!(idle.contains("Input: no keyboard or mouse found\nUpload: server unreachable"), "{idle}");
    }
}
