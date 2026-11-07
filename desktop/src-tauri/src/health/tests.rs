//! Each check good and bad, through a fake machine.

use std::sync::Mutex;

use super::*;
use crate::settings::tests::FakeLogin;
use crate::settings::NOT_INSTALLED;
use crate::types::ScreenId;

struct Fake {
    input: Access,
    in_group: bool,
    env: Vec<(&'static str, &'static str)>,
    portal: Result<u32, String>,
    screens: Result<Vec<ScreenStatus>, String>,
    tailscale: Result<Tailscale, String>,
    health: Result<u16, String>,
    post: Result<u16, String>,
    disk: Option<u64>,
    login: FakeLogin,
    posted: Mutex<Vec<String>>,
}

fn screen(name: &str, state: ScreenState) -> ScreenStatus {
    ScreenStatus { id: ScreenId(name.to_lowercase()), name: name.into(), state, last_frame_age_s: Some(0.5) }
}

impl Fake {
    fn good() -> Fake {
        Fake {
            input: Access::Ok,
            in_group: true,
            env: vec![("WAYLAND_DISPLAY", "wayland-1"), ("XDG_SESSION_TYPE", "wayland")],
            portal: Ok(5),
            screens: Ok(vec![screen("Main", ScreenState::Live), screen("Side", ScreenState::Live)]),
            tailscale: Ok(Tailscale { running: true, device: Some("desktop".into()), state: "Running".into() }),
            health: Ok(200),
            post: Ok(422),
            disk: Some(80_000_000_000),
            login: FakeLogin { on: Mutex::new(Some(true)), missing: false, target: Some(true) },
            posted: Mutex::new(vec![]),
        }
    }
}

#[async_trait]
impl HealthEnv for Fake {
    fn input(&self) -> Access {
        self.input
    }
    fn in_input_group(&self) -> bool {
        self.in_group
    }
    fn env(&self, key: &str) -> Option<String> {
        self.env.iter().find(|(k, _)| *k == key).map(|(_, v)| v.to_string())
    }
    async fn portal(&self) -> Result<u32, String> {
        self.portal.clone()
    }
    fn screens(&self) -> Result<Vec<ScreenStatus>, String> {
        self.screens.clone()
    }
    async fn tailscale(&self) -> Result<Tailscale, String> {
        self.tailscale.clone()
    }
    async fn get(&self, _url: &str) -> Result<u16, String> {
        self.health.clone()
    }
    async fn post_empty(&self, url: &str) -> Result<u16, String> {
        self.posted.lock().unwrap().push(url.to_string());
        self.post.clone()
    }
    fn disk_free(&self) -> Option<u64> {
        self.disk
    }
    async fn login(&self) -> (Result<bool, String>, Option<bool>) {
        (self.login.enabled(), self.login.session_target_active())
    }
}

async fn run(f: &Fake) -> Vec<Check> {
    checks(f, &Settings::default(), Device::Probe).await
}

fn by_id<'a>(checks: &'a [Check], id: &str) -> &'a Check {
    checks.iter().find(|c| c.id == id).unwrap_or_else(|| panic!("no check {id}"))
}

#[tokio::test]
async fn a_good_machine_passes_every_check_in_order() {
    let f = Fake::good();
    let c = run(&f).await;
    let ids: Vec<&str> = c.iter().map(|c| c.id.as_str()).collect();
    assert_eq!(
        ids,
        ["input_access", "desktop_session", "portal_available", "screens_open", "tailscale", "server_health", "device_accepted", "disk_space", "start_at_login"]
    );
    assert!(c.iter().all(|c| c.ok && c.fix.is_none()), "{c:#?}");
    assert_eq!(by_id(&c, "device_accepted").detail, "the server accepts \"desktop\"");
    assert_eq!(by_id(&c, "screens_open").detail, "2 screens, all live");
    assert_eq!(*f.posted.lock().unwrap(), ["http://vitality-librarian:8100/interactions"]);
    assert!(!essential_failure(&c));
}

#[tokio::test]
async fn input_access_bad_three_ways() {
    let mut f = Fake { input: Access::PermissionDenied, in_group: false, ..Fake::good() };
    let c = run(&f).await;
    assert!(!by_id(&c, "input_access").ok);
    assert_eq!(by_id(&c, "input_access").fix.as_deref(), Some(INPUT_FIX));
    assert!(essential_failure(&c));
    f.in_group = true;
    assert_eq!(by_id(&run(&f).await, "input_access").fix.as_deref(), Some("Log out and in again."));
    f.input = Access::NoDevices;
    assert!(by_id(&run(&f).await, "input_access").detail.contains("no keyboard"));
}

#[tokio::test]
async fn desktop_session_needs_a_display_and_a_session_type() {
    let f = Fake { env: vec![("WAYLAND_DISPLAY", "wayland-1")], ..Fake::good() };
    assert!(!by_id(&run(&f).await, "desktop_session").ok);
    let f = Fake { env: vec![("XDG_SESSION_TYPE", "wayland")], ..Fake::good() };
    let c = run(&f).await;
    assert!(!by_id(&c, "desktop_session").ok && by_id(&c, "desktop_session").fix.as_ref().unwrap().contains("dbus-update-activation-environment"));
    let f = Fake { env: vec![("DISPLAY", ":0"), ("XDG_SESSION_TYPE", "x11")], ..Fake::good() };
    assert_eq!(by_id(&run(&f).await, "desktop_session").detail, "x11 session (DISPLAY=:0)");
}

#[tokio::test]
async fn portal_missing() {
    let f = Fake { portal: Err("no ScreenCast portal on the session bus".into()), ..Fake::good() };
    let c = run(&f).await;
    assert!(!by_id(&c, "portal_available").ok && essential_failure(&c));
}

#[tokio::test]
async fn screens_none_one_stuck_or_unknown() {
    let f = Fake { screens: Ok(vec![]), ..Fake::good() };
    assert_eq!(by_id(&run(&f).await, "screens_open").fix.as_deref(), Some("Open Screens and add a screen."));
    assert!(essential_failure(&run(&Fake { screens: Ok(vec![]), ..Fake::good() }).await), "no screen at all: Setup");
    let f = Fake { screens: Ok(vec![screen("Main", ScreenState::Live), screen("Side", ScreenState::NeedsPick)]), ..Fake::good() };
    let c = run(&f).await;
    assert_eq!(by_id(&c, "screens_open").detail, "Side is not live");
    assert!(!essential_failure(&c), "a configured screen that is not live is a warning, not Setup");
    let f = Fake { screens: Err("Vitality isn't running".into()), ..Fake::good() };
    assert!(!by_id(&run(&f).await, "screens_open").ok);
}

#[tokio::test]
async fn the_server_in_three_parts() {
    let f = Fake { tailscale: Ok(Tailscale { running: false, device: None, state: "Stopped".into() }), ..Fake::good() };
    let c = run(&f).await;
    assert_eq!(by_id(&c, "tailscale").detail, "not running (Stopped)");
    assert!(!essential_failure(&c), "the server is not essential: Setup lets it be skipped");
    let f = Fake { health: Err("no connection".into()), post: Err("no connection".into()), ..Fake::good() };
    let c = run(&f).await;
    assert!(!by_id(&c, "server_health").ok && !by_id(&c, "device_accepted").ok);
    let f = Fake { post: Ok(403), ..Fake::good() };
    let c = run(&f).await;
    let d = by_id(&c, "device_accepted");
    assert_eq!((d.ok, d.fix.as_deref()), (false, Some("On the server, add \"desktop\" to allowed_clients.")));
    for code in [411, 413, 500] {
        let f = Fake { post: Ok(code), ..Fake::good() };
        let d = by_id(&run(&f).await, "device_accepted").clone();
        assert!(!d.ok && d.detail.starts_with("cannot tell"), "{code}: only 422 proves acceptance");
    }
}

#[tokio::test]
async fn no_server_set_is_one_passing_check() {
    let f = Fake::good();
    let c = checks(&f, &Settings { server: "".into(), ..Settings::default() }, Device::Probe).await;
    assert!(by_id(&c, "server").ok);
    assert!(c.iter().all(|c| c.id != "tailscale"));
    assert!(f.posted.lock().unwrap().is_empty());
}

#[tokio::test]
async fn disk_space_and_start_at_login() {
    let f = Fake { disk: Some(1_000_000_000), ..Fake::good() };
    let c = run(&f).await;
    assert!(!by_id(&c, "disk_space").ok && by_id(&c, "disk_space").detail.contains("under the warning level"));
    let f = Fake { login: FakeLogin { on: Mutex::new(Some(false)), missing: false, target: Some(true) }, ..Fake::good() };
    assert!(!by_id(&run(&f).await, "start_at_login").ok);
    let f = Fake { login: FakeLogin { on: Mutex::new(None), missing: true, target: None }, ..Fake::good() };
    let c = by_id(&run(&f).await, "start_at_login").clone();
    assert!(!c.ok && !c.essential && c.detail.starts_with(NOT_INSTALLED));
    // The setting off and the service not installed: nothing to fix.
    let off = Settings { start_at_login: false, ..Settings::default() };
    assert!(by_id(&checks(&f, &off, Device::Probe).await, "start_at_login").ok);
    // Enabled, but the session never starts graphical-session.target (Hyprland without uwsm).
    let f = Fake { login: FakeLogin { on: Mutex::new(Some(true)), missing: false, target: Some(false) }, ..Fake::good() };
    let c = by_id(&run(&f).await, "start_at_login").clone();
    assert!(!c.ok && !c.essential && c.detail.contains("graphical-session.target"));
    assert!(c.fix.unwrap().contains("exec-once = systemctl --user start vitality-client"));
}

#[tokio::test]
async fn doctor_prints_each_check_and_exits_1_on_an_essential_failure() {
    let f = Fake { input: Access::PermissionDenied, in_group: false, post: Ok(403), ..Fake::good() };
    let (text, code) = report(&run(&f).await);
    assert_eq!(code, 1);
    assert!(text.starts_with("Vitality doctor\n✗ Keyboard and mouse · not allowed to read /dev/input"), "{text}");
    assert!(text.contains(&format!("    Fix: {INPUT_FIX}\n")), "{text}");
    assert!(text.contains("✓ Screens · 2 screens, all live\n"), "{text}");
    assert!(text.contains("✗ This device accepted · the server does not accept \"desktop\"\n    Fix: On the server, add \"desktop\" to allowed_clients.\n"), "{text}");
    let (good, code) = report(&run(&Fake::good()).await);
    assert_eq!(code, 0);
    assert!(!good.contains('✗'));
    let (non_essential, code) = report(&run(&Fake { post: Ok(403), ..Fake::good() }).await);
    assert!(non_essential.contains('✗'));
    assert_eq!(code, 0, "only essential failures make doctor fail");
}

#[test]
fn tailscale_status_json() {
    let t = parse_tailscale(br#"{"BackendState":"Running","Self":{"HostName":"Desktop-PC","DNSName":"desktop.tail1234.ts.net."}}"#).unwrap();
    assert_eq!(t, Tailscale { running: true, device: Some("desktop".into()), state: "Running".into() });
    let t = parse_tailscale(br#"{"BackendState":"NeedsLogin","Self":{"HostName":"laptop","DNSName":""}}"#).unwrap();
    assert_eq!((t.running, t.device.as_deref()), (false, Some("laptop")));
    assert!(parse_tailscale(b"").is_err());
}

#[tokio::test]
async fn the_device_probe_runs_only_when_asked() {
    let f = Fake::good();
    let earlier = run(&f).await;
    assert_eq!(f.posted.lock().unwrap().len(), 1);
    let previous = by_id(&earlier, "device_accepted").clone();
    let again = checks(&f, &Settings::default(), Device::Reuse(Some(previous.clone()))).await;
    assert_eq!(f.posted.lock().unwrap().len(), 1, "not probed again");
    assert_eq!(by_id(&again, "device_accepted"), &previous);
    let never = checks(&f, &Settings::default(), Device::Reuse(None)).await;
    assert_eq!(by_id(&never, "device_accepted").detail, "not tested yet");
}

/// A portal on a private D-Bus connection that has only the lower-case `version` property,
/// as the real ScreenCast portal does.
struct FakeScreenCast;

#[zbus::interface(name = "org.freedesktop.portal.ScreenCast")]
impl FakeScreenCast {
    #[zbus(property, name = "version")]
    fn version(&self) -> u32 {
        5
    }
}

#[tokio::test]
async fn the_portal_version_is_read_from_its_lower_case_property() {
    let (a, b) = tokio::net::UnixStream::pair().unwrap();
    let guid = zbus::Guid::generate();
    let server = zbus::connection::Builder::unix_stream(a)
        .server(guid)
        .unwrap()
        .p2p()
        .serve_at("/org/freedesktop/portal/desktop", FakeScreenCast)
        .unwrap()
        .build();
    let client = zbus::connection::Builder::unix_stream(b).p2p().build();
    let (server, client) = tokio::join!(server, client);
    let (_server, client) = (server.unwrap(), client.unwrap());
    assert_eq!(portal_version(&client).await, Ok(5));
}
