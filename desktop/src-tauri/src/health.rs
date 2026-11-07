//! The health checks (spec §8): one list behind Setup, Status and `vitality-client doctor`.
//!
//! `checks(env, settings, device)` runs them all through `HealthEnv` (the machine, so tests use
//! fakes) and returns `Check`s in a fixed order, each with a fix text when it fails:
//! `input_access`, `desktop_session`, `portal_available` (essential: Setup is shown while one
//! fails), `screens_open` (essential only while no screen is set up; a configured screen that
//! is not live is a warning), then the server in three parts: `tailscale` (running, and this
//! device's name; 3 s at most), `server_health` (`GET /health` answers 200) and
//! `device_accepted` (a `POST /interactions` with an empty body: 422 = accepted, 403 = not
//! accepted, anything else = cannot tell; nothing is stored). That probe is logged on the
//! server, so it runs only when `device` is `Probe` (Setup's Test, app start, a new server);
//! otherwise the last result is passed in (`Reuse`). Then `disk_space` and `start_at_login`
//! (also: enabled but the session never starts `graphical-session.target`).
//! With no server set, the three server parts are one `server` check that passes.
//! `report(checks)` is `doctor`'s text and exit code. `MachineEnv` is the real environment.

use async_trait::async_trait;

use crate::activity::Access;
use crate::settings::{Settings, StartAtLogin, NOT_INSTALLED};
use crate::types::{Check, ScreenState, ScreenStatus};

pub const INPUT_FIX: &str = "Run `sudo usermod -aG input $USER`, then log out and in again.";

/// What Tailscale says about this machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tailscale {
    pub running: bool,
    /// This device's name on the tailnet.
    pub device: Option<String>,
    /// `BackendState` ("Running", "Stopped", "NeedsLogin"…).
    pub state: String,
}

#[async_trait]
pub trait HealthEnv: Send + Sync {
    fn input(&self) -> Access;
    /// Whether this user is in the `input` group (from the group database; a new membership
    /// counts only after logging in again).
    fn in_input_group(&self) -> bool;
    fn env(&self, key: &str) -> Option<String>;
    /// The ScreenCast portal's version, or why there is none.
    async fn portal(&self) -> Result<u32, String>;
    /// The screens and their states, or why they are unknown (the app is not running).
    fn screens(&self) -> Result<Vec<ScreenStatus>, String>;
    async fn tailscale(&self) -> Result<Tailscale, String>;
    async fn get(&self, url: &str) -> Result<u16, String>;
    async fn post_empty(&self, url: &str) -> Result<u16, String>;
    fn disk_free(&self) -> Option<u64>;
    /// The service's state and whether the session starts `graphical-session.target`.
    /// The service's state, whether the session starts `graphical-session.target`, and
    /// whether this process is the service running now.
    async fn login(&self) -> (Result<bool, String>, Option<bool>, bool);
}

/// Whether to probe the server for this device's acceptance, or reuse an earlier result.
pub enum Device {
    Probe,
    Reuse(Option<Check>),
    /// What a real upload said (the truth, when there is one): accepted or refused, and when.
    Uploaded { accepted: bool, at: chrono::DateTime<chrono::Utc> },
}

fn check(id: &str, ok: bool, essential: bool, title: &str, detail: String, fix: Option<String>) -> Check {
    Check { id: id.into(), ok, essential, title: title.into(), detail, fix: if ok { None } else { fix } }
}

pub async fn checks(env: &dyn HealthEnv, settings: &Settings, device: Device) -> Vec<Check> {
    let (portal, server, login) = tokio::join!(portal_available(env), server(env, settings, device), start_at_login(env, settings));
    let mut out = vec![input_access(env), desktop_session(env), portal, screens_open(env)];
    out.extend(server);
    out.push(disk_space(env, settings));
    out.push(login);
    out
}

fn input_access(env: &dyn HealthEnv) -> Check {
    let title = "Keyboard and mouse";
    match env.input() {
        Access::Ok => check("input_access", true, true, title, "input devices are readable".into(), None),
        Access::PermissionDenied if env.in_input_group() => check(
            "input_access",
            false,
            true,
            title,
            "you are in the input group, but this session started before you joined it".into(),
            Some("Log out and in again.".into()),
        ),
        Access::PermissionDenied => check(
            "input_access",
            false,
            true,
            title,
            "not allowed to read /dev/input: you are not in the input group".into(),
            Some(INPUT_FIX.into()),
        ),
        Access::NoDevices => check("input_access", false, true, title, "no keyboard or mouse found".into(), Some("Plug in a keyboard or mouse.".into())),
    }
}

fn desktop_session(env: &dyn HealthEnv) -> Check {
    let title = "Desktop session";
    let display = env.env("WAYLAND_DISPLAY").map(|d| format!("WAYLAND_DISPLAY={d}")).or_else(|| env.env("DISPLAY").map(|d| format!("DISPLAY={d}")));
    let kind = env.env("XDG_SESSION_TYPE").filter(|k| !k.is_empty());
    let fix = "Start Vitality inside your desktop session. For the systemd service, pass the session's variables to systemd at login: `dbus-update-activation-environment --systemd --all`.";
    match (display, kind) {
        (Some(d), Some(k)) => check("desktop_session", true, true, title, format!("{k} session ({d})"), None),
        (None, _) => check("desktop_session", false, true, title, "no WAYLAND_DISPLAY or DISPLAY".into(), Some(fix.into())),
        (Some(d), None) => check("desktop_session", false, true, title, format!("{d}, but no XDG_SESSION_TYPE"), Some(fix.into())),
    }
}

async fn portal_available(env: &dyn HealthEnv) -> Check {
    let title = "Screen-share portal";
    match env.portal().await {
        Ok(v) => check("portal_available", true, true, title, format!("ScreenCast portal, version {v}"), None),
        Err(why) => check(
            "portal_available",
            false,
            true,
            title,
            why,
            Some("Install xdg-desktop-portal and your desktop's backend (xdg-desktop-portal-hyprland, -gnome, -kde or -wlr), then log in again.".into()),
        ),
    }
}

fn screens_open(env: &dyn HealthEnv) -> Check {
    let title = "Screens";
    match env.screens() {
        Err(why) => check("screens_open", false, true, title, why, Some("Start Vitality: `systemctl --user start vitality-client`.".into())),
        Ok(s) if s.is_empty() => check("screens_open", false, true, title, "no screen set up".into(), Some("Open Screens and add a screen.".into())),
        // A configured screen that is not live is a warning (Status, tray), not Setup.
        Ok(s) => {
            let stuck: Vec<&str> = s.iter().filter(|x| x.state != ScreenState::Live).map(|x| x.name.as_str()).collect();
            if stuck.is_empty() {
                let n = s.len();
                check("screens_open", true, true, title, if n == 1 { "1 screen, live".into() } else { format!("{n} screens, all live") }, None)
            } else {
                let names = stuck.join(", ");
                let verb = if stuck.len() == 1 { "is" } else { "are" };
                check("screens_open", false, false, title, format!("{names} {verb} not live"), Some(format!("Open Screens and pick {names} again.")))
            }
        }
    }
}

async fn server(env: &dyn HealthEnv, settings: &Settings, device: Device) -> Vec<Check> {
    let Some(server) = settings.server() else {
        return vec![check("server", true, false, "Server", "no server set: captures stay on this machine".into(), None)];
    };
    let health_url = format!("{server}/health");
    let (ts, health) = tokio::join!(env.tailscale(), env.get(&health_url));
    let device_name = ts.as_ref().ok().and_then(|t| t.device.clone());
    let tailscale = match &ts {
        Ok(t) if t.running => check("tailscale", true, false, "Tailscale", format!("running{}", device_name.as_ref().map(|d| format!(" as \"{d}\"")).unwrap_or_default()), None),
        Ok(t) => check("tailscale", false, false, "Tailscale", format!("not running ({})", t.state), Some("Start Tailscale: `sudo tailscale up`.".into())),
        Err(why) => check("tailscale", false, false, "Tailscale", why.clone(), Some("Install and start Tailscale (`sudo systemctl enable --now tailscaled`, then `sudo tailscale up`).".into())),
    };
    let health = match health {
        Ok(200) => check("server_health", true, false, "Server answers", format!("{server} is up"), None),
        Ok(code) => check("server_health", false, false, "Server answers", format!("{server}/health answered {code}"), Some("Check the server (`vitality-server status` on it).".into())),
        Err(why) => check(
            "server_health",
            false,
            false,
            "Server answers",
            format!("{server} cannot be reached: {why}"),
            Some("Check the address, and that this machine and the server are on the same tailnet.".into()),
        ),
    };
    let name = device_name.clone().unwrap_or_else(crate::upload::device_name);
    let probe = match device {
        Device::Uploaded { accepted, at } => {
            let when = at.with_timezone(&chrono::Local).format("%H:%M");
            let known = if accepted {
                check("device_accepted", true, false, "This device accepted", format!("the server stored an upload from \"{name}\" at {when}"), None)
            } else {
                check(
                    "device_accepted",
                    false,
                    false,
                    "This device accepted",
                    format!("the server refused an upload from \"{name}\" at {when}"),
                    Some(format!("On the server, add \"{name}\" to allowed_clients.")),
                )
            };
            return vec![tailscale, health, known];
        }
        Device::Reuse(previous) => {
            let reused = previous.unwrap_or_else(|| {
                check("device_accepted", false, false, "This device accepted", "not tested yet".into(), Some("Test the server in Setup.".into()))
            });
            return vec![tailscale, health, reused];
        }
        Device::Probe => env.post_empty(&format!("{server}{}", crate::upload::INTERACTIONS_PATH)).await,
    };
    // Only 422 proves the device passed: the server checks the size headers (411, 413) before
    // the device (403), and the body (422) after.
    let accepted = match probe {
        Ok(422) => check("device_accepted", true, false, "This device accepted", format!("the server accepts \"{name}\""), None),
        Ok(403) => check(
            "device_accepted",
            false,
            false,
            "This device accepted",
            format!("the server does not accept \"{name}\""),
            Some(format!("On the server, add \"{name}\" to allowed_clients.")),
        ),
        Ok(code) => check(
            "device_accepted",
            false,
            false,
            "This device accepted",
            format!("cannot tell: the server answered {code}"),
            Some("Test again; if it stays, check the server's log.".into()),
        ),
        Err(why) => check("device_accepted", false, false, "This device accepted", format!("not tested: {why}"), Some("Make the server reachable first.".into())),
    };
    vec![tailscale, health, accepted]
}

fn disk_space(env: &dyn HealthEnv, settings: &Settings) -> Check {
    let title = "Disk space";
    match env.disk_free() {
        None => check("disk_space", false, false, title, "unknown".into(), Some("Check that the captures folder's disk is mounted.".into())),
        Some(free) => {
            let gb = free as f64 / 1e9;
            let ok = free >= settings.disk_warning_bytes();
            check(
                "disk_space",
                ok,
                false,
                title,
                format!("{gb:.1} GB free{}", if ok { String::new() } else { format!(", under the warning level of {} GB", settings.disk_warning_gb) }),
                Some("Free up space in Settings (it deletes local copies already uploaded).".into()),
            )
        }
    }
}

async fn start_at_login(env: &dyn HealthEnv, settings: &Settings) -> Check {
    let title = "Start at login";
    let id = "start_at_login";
    let (enabled, target, running) = env.login().await;
    match (settings.start_at_login, enabled) {
        (false, Ok(false)) => check(id, true, false, title, "off, as set".into(), None),
        (false, Err(why)) if why.starts_with(NOT_INSTALLED) => check(id, true, false, title, "off, as set (the service is not installed)".into(), None),
        (false, Ok(true)) => check(id, false, false, title, "the service is on but the setting says off".into(), Some("Turn Start at login on and off again (in Settings) to apply it.".into())),
        (true, Ok(true)) if target == Some(false) && running => check(id, true, false, title, "on — started by your desktop".into(), None),
        (true, Ok(true)) if target == Some(false) => check(
            id,
            false,
            false,
            title,
            "the service is on, but this session does not start graphical-session.target, so it will not start at login".into(),
            Some("Start Vitality from your compositor or desktop's autostart instead; on Hyprland, for example: `exec-once = systemctl --user start vitality-client`.".into()),
        ),
        (true, Ok(true)) => check(id, true, false, title, "on".into(), None),
        (true, Ok(false)) => check(id, false, false, title, "the service is off but the setting says on".into(), Some("Turn Start at login off and on again (in Settings) to apply it.".into())),
        (_, Err(why)) => check(id, false, false, title, why, Some("Start Vitality from your desktop's autostart instead.".into())),
    }
}

/// Whether any essential check fails (then Setup is shown, and `doctor` exits 1).
pub fn essential_failure(checks: &[Check]) -> bool {
    checks.iter().any(|c| c.essential && !c.ok)
}

/// `doctor`'s output: one line per check (✓/✗), the fix under each failed one; and the exit
/// code (1 when an essential check fails).
pub fn report(checks: &[Check]) -> (String, i32) {
    let mut out = String::from("Vitality doctor\n");
    for c in checks {
        out += &format!("{} {} · {}\n", if c.ok { "✓" } else { "✗" }, c.title, c.detail);
        if let Some(fix) = &c.fix {
            out += &format!("    Fix: {fix}\n");
        }
    }
    let code = if essential_failure(checks) {
        out += "Something essential is missing: Vitality cannot capture until it is fixed.\n";
        1
    } else {
        0
    };
    (out, code)
}

/// The machine itself.
pub struct MachineEnv {
    pub input: std::sync::Arc<dyn crate::activity::InputSource>,
    pub screens: Box<dyn Fn() -> Result<Vec<ScreenStatus>, String> + Send + Sync>,
    pub captures: std::path::PathBuf,
    pub login: std::sync::Arc<dyn StartAtLogin>,
    http: reqwest::Client,
}

impl MachineEnv {
    pub fn new(
        input: std::sync::Arc<dyn crate::activity::InputSource>,
        screens: Box<dyn Fn() -> Result<Vec<ScreenStatus>, String> + Send + Sync>,
        captures: std::path::PathBuf,
        login: std::sync::Arc<dyn StartAtLogin>,
    ) -> MachineEnv {
        let http = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(5))
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .expect("an HTTP client");
        MachineEnv { input, screens, captures, login, http }
    }
}

#[zbus::proxy(
    interface = "org.freedesktop.portal.ScreenCast",
    default_service = "org.freedesktop.portal.Desktop",
    default_path = "/org/freedesktop/portal/desktop"
)]
trait ScreenCastPortal {
    /// The portal names its property in lower case (`version`), not zbus's default `Version`.
    #[zbus(property, name = "version")]
    fn version(&self) -> zbus::Result<u32>;
}

/// The ScreenCast portal's version over `conn` (5 s at most).
pub async fn portal_version(conn: &zbus::Connection) -> Result<u32, String> {
    let ask = async {
        let proxy = ScreenCastPortalProxy::new(conn).await.map_err(|e| e.to_string())?;
        proxy.version().await.map_err(|e| format!("no ScreenCast portal on the session bus ({e})"))
    };
    match tokio::time::timeout(std::time::Duration::from_secs(5), ask).await {
        Ok(r) => r,
        Err(_) => Err("the screen-share portal did not answer within 5 s".into()),
    }
}

#[async_trait]
impl HealthEnv for MachineEnv {
    fn input(&self) -> Access {
        self.input.accessible()
    }

    fn in_input_group(&self) -> bool {
        in_group("input")
    }

    fn env(&self, key: &str) -> Option<String> {
        std::env::var(key).ok().filter(|v| !v.is_empty())
    }

    async fn portal(&self) -> Result<u32, String> {
        let conn = tokio::time::timeout(std::time::Duration::from_secs(5), zbus::Connection::session())
            .await
            .map_err(|_| "the session D-Bus did not answer within 5 s".to_string())?
            .map_err(|e| format!("no session D-Bus: {e}"))?;
        portal_version(&conn).await
    }

    fn screens(&self) -> Result<Vec<ScreenStatus>, String> {
        (self.screens)()
    }

    async fn tailscale(&self) -> Result<Tailscale, String> {
        let run = tokio::process::Command::new("tailscale").args(["status", "--json"]).kill_on_drop(true).output();
        let out = tokio::time::timeout(std::time::Duration::from_secs(3), run)
            .await
            .map_err(|_| "tailscale did not answer within 3 s".to_string())?
            .map_err(|e| {
                if e.kind() == std::io::ErrorKind::NotFound {
                    "Tailscale is not installed".to_string()
                } else {
                    format!("cannot run tailscale: {e}")
                }
            })?;
        parse_tailscale(&out.stdout)
    }

    async fn get(&self, url: &str) -> Result<u16, String> {
        self.http.get(url).send().await.map(|r| r.status().as_u16()).map_err(short)
    }

    async fn post_empty(&self, url: &str) -> Result<u16, String> {
        let form = reqwest::multipart::Form::new();
        self.http.post(url).multipart(form).send().await.map(|r| r.status().as_u16()).map_err(short)
    }

    fn disk_free(&self) -> Option<u64> {
        crate::paths::free_bytes(&self.captures)
    }

    async fn login(&self) -> (Result<bool, String>, Option<bool>, bool) {
        let login = self.login.clone();
        tokio::task::spawn_blocking(move || (login.enabled(), login.session_target_active(), login.running_as_service()))
            .await
            .unwrap_or_else(|e| (Err(e.to_string()), None, false))
    }
}

fn short(e: reqwest::Error) -> String {
    if e.is_timeout() {
        "no answer in time".into()
    } else if e.is_connect() {
        "no connection".into()
    } else {
        e.to_string()
    }
}

/// `tailscale status --json`: `BackendState`, and `Self.HostName` (or the first label of
/// `Self.DNSName`) as this device's name.
pub fn parse_tailscale(json: &[u8]) -> Result<Tailscale, String> {
    let v: serde_json::Value = serde_json::from_slice(json).map_err(|_| "tailscale gave no status (is tailscaled running?)".to_string())?;
    let state = v["BackendState"].as_str().unwrap_or("unknown").to_string();
    let me = &v["Self"];
    let device = me["DNSName"]
        .as_str()
        .and_then(|d| d.split('.').next())
        .filter(|d| !d.is_empty())
        .or_else(|| me["HostName"].as_str())
        .map(str::to_string);
    Ok(Tailscale { running: state == "Running", device, state })
}

/// Whether this user belongs to `group` in the user database (NSS: files, sssd, userdb…),
/// primary group included; a new membership shows here before the session has it.
fn in_group(group: &str) -> bool {
    let Ok(name) = std::ffi::CString::new(group) else { return false };
    // SAFETY: getgrnam and getpwuid return pointers into static storage or null, read at once;
    // getgrouplist fills a buffer of the size it is given.
    unsafe {
        let g = libc::getgrnam(name.as_ptr());
        if g.is_null() {
            return false;
        }
        let gid = (*g).gr_gid;
        let pw = libc::getpwuid(libc::getuid());
        if pw.is_null() {
            return false;
        }
        let mut groups = vec![0 as libc::gid_t; 256];
        let mut n = groups.len() as libc::c_int;
        if libc::getgrouplist((*pw).pw_name, (*pw).pw_gid, groups.as_mut_ptr(), &mut n) < 0 {
            groups.resize(n.max(0) as usize, 0);
            if libc::getgrouplist((*pw).pw_name, (*pw).pw_gid, groups.as_mut_ptr(), &mut n) < 0 {
                return false;
            }
        }
        groups[..n.max(0) as usize].contains(&gid)
    }
}

pub mod service;

#[cfg(test)]
mod tests;
