//! The health checks (spec §8): one list behind Setup, Status and `vitality-client doctor`.
//!
//! `checks(env, settings)` runs them all through `HealthEnv` (the machine, so tests use fakes)
//! and returns `Check`s in a fixed order, each with a fix text when it fails:
//! `input_access`, `desktop_session`, `portal_available`, `screens_open` (the essential ones:
//! Setup is shown while one fails), then the server in three parts, `tailscale` (running, and
//! this device's name), `server_health` (`GET /health` answers 200) and `device_accepted` (a
//! `POST /interactions` with an empty body: 403 = this device is not accepted, 411/422 =
//! accepted; nothing is stored either way), then `disk_space` and `start_at_login`.
//! With no server set, the three server parts are one `server` check that passes.
//! `report(checks)` is `doctor`'s text and exit code. `MachineEnv` is the real environment.

use async_trait::async_trait;

use crate::activity::Access;
use crate::settings::{Settings, StartAtLogin};
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
    fn start_at_login(&self) -> &dyn StartAtLogin;
}

fn check(id: &str, ok: bool, essential: bool, title: &str, detail: String, fix: Option<String>) -> Check {
    Check { id: id.into(), ok, essential, title: title.into(), detail, fix: if ok { None } else { fix } }
}

pub async fn checks(env: &dyn HealthEnv, settings: &Settings) -> Vec<Check> {
    let mut out = vec![input_access(env), desktop_session(env), portal_available(env).await, screens_open(env)];
    out.extend(server(env, settings).await);
    out.push(disk_space(env, settings));
    out.push(start_at_login(env, settings));
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
        Ok(s) => {
            let stuck: Vec<&str> = s.iter().filter(|x| x.state != ScreenState::Live).map(|x| x.name.as_str()).collect();
            if stuck.is_empty() {
                let n = s.len();
                check("screens_open", true, true, title, if n == 1 { "1 screen, live".into() } else { format!("{n} screens, all live") }, None)
            } else {
                let names = stuck.join(", ");
                let verb = if stuck.len() == 1 { "is" } else { "are" };
                check("screens_open", false, true, title, format!("{names} {verb} not live"), Some(format!("Open Screens and pick {names} again.")))
            }
        }
    }
}

async fn server(env: &dyn HealthEnv, settings: &Settings) -> Vec<Check> {
    let Some(server) = settings.server() else {
        return vec![check("server", true, false, "Server", "no server set: captures stay on this machine".into(), None)];
    };
    let ts = env.tailscale().await;
    let device = ts.as_ref().ok().and_then(|t| t.device.clone());
    let tailscale = match &ts {
        Ok(t) if t.running => check("tailscale", true, false, "Tailscale", format!("running{}", device.as_ref().map(|d| format!(" as \"{d}\"")).unwrap_or_default()), None),
        Ok(t) => check("tailscale", false, false, "Tailscale", format!("not running ({})", t.state), Some("Start Tailscale: `sudo tailscale up`.".into())),
        Err(why) => check("tailscale", false, false, "Tailscale", why.clone(), Some("Install and start Tailscale (`sudo systemctl enable --now tailscaled`, then `sudo tailscale up`).".into())),
    };
    let health = match env.get(&format!("{server}/health")).await {
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
    let name = device.clone().unwrap_or_else(crate::upload::device_name);
    let accepted = match env.post_empty(&format!("{server}{}", crate::upload::INTERACTIONS_PATH)).await {
        Ok(411 | 422) => check("device_accepted", true, false, "This device accepted", format!("the server accepts \"{name}\""), None),
        Ok(403) => check(
            "device_accepted",
            false,
            false,
            "This device accepted",
            format!("the server does not accept \"{name}\""),
            Some(format!("On the server, add \"{name}\" to allowed_clients.")),
        ),
        Ok(code) => check("device_accepted", false, false, "This device accepted", format!("the server answered {code}"), Some("Check the server's log.".into())),
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

fn start_at_login(env: &dyn HealthEnv, settings: &Settings) -> Check {
    let title = "Start at login";
    match env.start_at_login().enabled() {
        Ok(on) if on == settings.start_at_login => {
            check("start_at_login", true, false, title, if on { "on".into() } else { "off, as set".into() }, None)
        }
        Ok(on) => check(
            "start_at_login",
            false,
            false,
            title,
            format!("the service is {} but the setting says {}", if on { "on" } else { "off" }, if settings.start_at_login { "on" } else { "off" }),
            Some("Turn Start at login off and on again (in Settings) to apply it.".into()),
        ),
        Err(why) => check("start_at_login", false, false, title, why, Some("Start Vitality from your desktop's autostart instead.".into())),
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
    pub login: Box<dyn StartAtLogin>,
    http: reqwest::Client,
}

impl MachineEnv {
    pub fn new(
        input: std::sync::Arc<dyn crate::activity::InputSource>,
        screens: Box<dyn Fn() -> Result<Vec<ScreenStatus>, String> + Send + Sync>,
        captures: std::path::PathBuf,
        login: Box<dyn StartAtLogin>,
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
    #[zbus(property)]
    fn version(&self) -> zbus::Result<u32>;
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
        let conn = zbus::Connection::session().await.map_err(|e| format!("no session D-Bus: {e}"))?;
        let proxy = ScreenCastPortalProxy::new(&conn).await.map_err(|e| e.to_string())?;
        // The portal may be started on demand; give up after 5 s rather than D-Bus's 2 min.
        match tokio::time::timeout(std::time::Duration::from_secs(5), proxy.version()).await {
            Ok(Ok(v)) => Ok(v),
            Ok(Err(e)) => Err(format!("no ScreenCast portal on the session bus ({e})")),
            Err(_) => Err("the screen-share portal did not answer within 5 s".into()),
        }
    }

    fn screens(&self) -> Result<Vec<ScreenStatus>, String> {
        (self.screens)()
    }

    async fn tailscale(&self) -> Result<Tailscale, String> {
        let out = tokio::process::Command::new("tailscale").args(["status", "--json"]).output().await.map_err(|e| {
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

    fn start_at_login(&self) -> &dyn StartAtLogin {
        self.login.as_ref()
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

/// Whether this user belongs to `group` (by the group database).
fn in_group(group: &str) -> bool {
    let Ok(name) = std::ffi::CString::new(group) else { return false };
    // SAFETY: getgrnam returns a pointer into static storage or null; read at once.
    let gid = unsafe {
        let g = libc::getgrnam(name.as_ptr());
        if g.is_null() {
            return false;
        }
        (*g).gr_gid
    };
    let user = std::env::var("USER").unwrap_or_default();
    std::fs::read_to_string("/etc/group")
        .map(|t| {
            t.lines().any(|l| {
                let f: Vec<&str> = l.split(':').collect();
                f.len() >= 4 && f[2].parse::<u32>().ok() == Some(gid) && f[3].split(',').any(|m| m == user)
            })
        })
        .unwrap_or(false)
        || unsafe { libc::getegid() } == gid
}

#[cfg(test)]
mod tests;
