//! Keyboard and mouse activity from Linux input devices (`/dev/input/event*`, readable by the
//! `input` group).
//!
//! `InputSource` is what the runtime uses: `subscribe()` gives a stream of input times (at most
//! ten a second), `accessible()` says whether any device could be read. `EvdevInput` is the real
//! one: it opens every device that reports keys or pointer axes (absolute axes only on
//! touchpads, touch screens and tablets, not accelerometers or joysticks), re-scans every 30 s for new
//! ones (hotplug), and reads each on its own thread. Device listing and opening go through
//! `Devices`, so tests inject their own. `FakeInput` is for tests of the runtime.

use std::collections::HashSet;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

/// Whether input can be read.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Access {
    /// At least one input device is open.
    Ok,
    /// No input device was found.
    NoDevices,
    /// Devices exist but none could be opened: the user is not in the `input` group.
    PermissionDenied,
}

impl Access {
    pub fn as_str(self) -> &'static str {
        match self {
            Access::Ok => "ok",
            Access::NoDevices => "no_devices",
            Access::PermissionDenied => "permission_denied",
        }
    }
}

pub trait InputSource: Send + Sync {
    /// A new stream of input times.
    fn subscribe(&self) -> mpsc::Receiver<Instant>;
    fn accessible(&self) -> Access;
}

/// Where input devices are found and how they are opened.
pub trait Devices: Send + Sync + 'static {
    /// The candidate device paths (`/dev/input/event*`).
    fn list(&self) -> io::Result<Vec<PathBuf>>;
    /// Opens `path`; `Ok(None)` if it is not a keyboard or pointer.
    fn open(&self, path: &Path) -> io::Result<Option<Box<dyn Device>>>;
}

/// One open input device.
pub trait Device: Send {
    /// Blocks until the device reports input (a key, a movement); an error means it is gone.
    fn wait_input(&mut self) -> io::Result<()>;
}

/// Lets through at most one time per `every` (so at most ten a second for 100 ms).
#[derive(Debug)]
pub struct Coalescer {
    every: Duration,
    last: Option<Instant>,
}

impl Coalescer {
    pub fn new(every: Duration) -> Coalescer {
        Coalescer { every, last: None }
    }

    /// Whether input at `at` is passed on.
    pub fn admit(&mut self, at: Instant) -> bool {
        match self.last {
            Some(last) if at.saturating_duration_since(last) < self.every => false,
            _ => {
                self.last = Some(at);
                true
            }
        }
    }
}

/// The real input source over `Devices` (`SystemDevices` for `/dev/input`).
#[derive(Clone)]
pub struct EvdevInput {
    inner: Arc<Inner>,
}

struct Inner {
    devices: Box<dyn Devices>,
    open: Mutex<HashSet<PathBuf>>,
    denied: Mutex<HashSet<PathBuf>>,
    subscribers: Mutex<Vec<mpsc::Sender<Instant>>>,
    coalescer: Mutex<Coalescer>,
}

impl EvdevInput {
    /// Opens the devices now and re-scans every `rescan_every` on a background thread.
    pub fn start(devices: Box<dyn Devices>, rescan_every: Duration) -> EvdevInput {
        let input = EvdevInput::new(devices);
        input.scan();
        let again = input.clone();
        std::thread::Builder::new()
            .name("input-rescan".into())
            .spawn(move || loop {
                std::thread::sleep(rescan_every);
                again.scan();
            })
            .expect("cannot start the input re-scan thread");
        input
    }

    fn new(devices: Box<dyn Devices>) -> EvdevInput {
        EvdevInput {
            inner: Arc::new(Inner {
                devices,
                open: Mutex::new(HashSet::new()),
                denied: Mutex::new(HashSet::new()),
                subscribers: Mutex::new(vec![]),
                coalescer: Mutex::new(Coalescer::new(Duration::from_millis(100))),
            }),
        }
    }

    /// Opens every listed device not open yet; each gets a reader thread.
    pub fn scan(&self) {
        let paths = match self.inner.devices.list() {
            Ok(p) => p,
            Err(e) => {
                eprintln!("vitality-client: cannot list input devices: {e}");
                return;
            }
        };
        for path in paths {
            if self.inner.open.lock().unwrap().contains(&path) {
                continue;
            }
            match self.inner.devices.open(&path) {
                Ok(Some(device)) => {
                    self.inner.denied.lock().unwrap().remove(&path);
                    self.inner.open.lock().unwrap().insert(path.clone());
                    self.read(path, device);
                }
                Ok(None) => {}
                Err(e) if e.kind() == io::ErrorKind::PermissionDenied => {
                    self.inner.denied.lock().unwrap().insert(path);
                }
                Err(_) => {} // gone between listing and opening
            }
        }
    }

    fn read(&self, path: PathBuf, mut device: Box<dyn Device>) {
        let inner = self.inner.clone();
        let name = format!("input-{}", path.file_name().and_then(|n| n.to_str()).unwrap_or("?"));
        let spawned = std::thread::Builder::new().name(name).spawn(move || {
            while device.wait_input().is_ok() {
                inner.emit(Instant::now());
            }
            inner.open.lock().unwrap().remove(&path); // unplugged: a later scan may reopen it
        });
        if let Err(e) = spawned {
            eprintln!("vitality-client: cannot start an input reader: {e}");
        }
    }

    /// The paths open now (for tests and `doctor`).
    #[allow(dead_code)] // `doctor` comes in milestone 3
    pub fn open_paths(&self) -> Vec<PathBuf> {
        let mut paths: Vec<PathBuf> = self.inner.open.lock().unwrap().iter().cloned().collect();
        paths.sort();
        paths
    }
}

impl Inner {
    fn emit(&self, at: Instant) {
        if !self.coalescer.lock().unwrap().admit(at) {
            return;
        }
        self.subscribers.lock().unwrap().retain(|tx| !matches!(tx.try_send(at), Err(mpsc::error::TrySendError::Closed(_))));
    }
}

impl InputSource for EvdevInput {
    fn subscribe(&self) -> mpsc::Receiver<Instant> {
        let (tx, rx) = mpsc::channel(64);
        self.inner.subscribers.lock().unwrap().push(tx);
        rx
    }

    fn accessible(&self) -> Access {
        if !self.inner.open.lock().unwrap().is_empty() {
            Access::Ok
        } else if !self.inner.denied.lock().unwrap().is_empty() {
            Access::PermissionDenied
        } else {
            Access::NoDevices
        }
    }
}

/// `/dev/input/event*` through the `evdev` crate.
pub struct SystemDevices;

impl Devices for SystemDevices {
    fn list(&self) -> io::Result<Vec<PathBuf>> {
        let mut paths = vec![];
        for entry in std::fs::read_dir("/dev/input")? {
            let path = entry?.path();
            if path.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("event")) {
                paths.push(path);
            }
        }
        paths.sort();
        Ok(paths)
    }

    fn open(&self, path: &Path) -> io::Result<Option<Box<dyn Device>>> {
        let device = evdev::raw_stream::RawDevice::open(path)?;
        use evdev::KeyCode;
        let keys = device.supported_keys();
        let has = |k: KeyCode| keys.is_some_and(|set| set.contains(k));
        let any_key = keys.is_some_and(|k| k.iter().next().is_some());
        let rel = device.supported_relative_axes().is_some_and(|a| a.iter().next().is_some());
        let abs = device.supported_absolute_axes().is_some_and(|a| a.iter().next().is_some());
        // Absolute axes count only on pointers (touchpads, touch screens, tablets): an
        // accelerometer or a joystick moves by itself and would never let an interaction end.
        let pointer = [KeyCode::BTN_TOUCH, KeyCode::BTN_LEFT, KeyCode::BTN_TOOL_PEN, KeyCode::BTN_TOOL_FINGER].into_iter().any(has);
        Ok(if counts_as_input(any_key, rel, abs, pointer) { Some(Box::new(EvdevDevice(device))) } else { None })
    }
}

/// Whether a device with these capabilities is a keyboard or a pointer (`pointer`: it has a
/// touch, left-button or tool button).
fn counts_as_input(keys: bool, rel: bool, abs: bool, pointer: bool) -> bool {
    rel || (abs && pointer) || (keys && !abs)
}

struct EvdevDevice(evdev::raw_stream::RawDevice);

impl Device for EvdevDevice {
    fn wait_input(&mut self) -> io::Result<()> {
        use evdev::EventType;
        loop {
            let input = self.0.fetch_events()?.any(|e| matches!(e.event_type(), EventType::KEY | EventType::RELATIVE | EventType::ABSOLUTE));
            if input {
                return Ok(());
            }
        }
    }
}

/// An input source for tests: `send(at)` delivers `at` to every subscriber, uncoalesced.
#[cfg(test)]
#[derive(Default)]
pub struct FakeInput {
    pub access: Mutex<Option<Access>>,
    subscribers: Mutex<Vec<mpsc::Sender<Instant>>>,
}

#[cfg(test)]
impl FakeInput {
    pub fn send(&self, at: Instant) {
        for tx in self.subscribers.lock().unwrap().iter() {
            let _ = tx.try_send(at);
        }
    }
}

#[cfg(test)]
impl InputSource for FakeInput {
    fn subscribe(&self) -> mpsc::Receiver<Instant> {
        let (tx, rx) = mpsc::channel(64);
        self.subscribers.lock().unwrap().push(tx);
        rx
    }

    fn accessible(&self) -> Access {
        self.access.lock().unwrap().unwrap_or(Access::Ok)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::mpsc as std_mpsc;

    /// Devices from a list the test changes; each opened device reports input whenever the test
    /// sends to its channel, and disappears when the channel is dropped.
    #[derive(Default, Clone)]
    struct FakeDevices {
        paths: Arc<Mutex<Vec<PathBuf>>>,
        denied: Arc<Mutex<HashSet<PathBuf>>>,
        not_input: Arc<Mutex<HashSet<PathBuf>>>,
        feeds: Arc<Mutex<HashMap<PathBuf, std_mpsc::Sender<()>>>>,
    }

    struct FakeDevice(std_mpsc::Receiver<()>);

    impl Device for FakeDevice {
        fn wait_input(&mut self) -> io::Result<()> {
            self.0.recv().map_err(|_| io::Error::other("unplugged"))
        }
    }

    impl Devices for FakeDevices {
        fn list(&self) -> io::Result<Vec<PathBuf>> {
            Ok(self.paths.lock().unwrap().clone())
        }
        fn open(&self, path: &Path) -> io::Result<Option<Box<dyn Device>>> {
            if self.denied.lock().unwrap().contains(path) {
                return Err(io::Error::from(io::ErrorKind::PermissionDenied));
            }
            if self.not_input.lock().unwrap().contains(path) {
                return Ok(None);
            }
            let (tx, rx) = std_mpsc::channel();
            self.feeds.lock().unwrap().insert(path.to_path_buf(), tx);
            Ok(Some(Box::new(FakeDevice(rx))))
        }
    }

    fn p(s: &str) -> PathBuf {
        PathBuf::from(format!("/dev/input/{s}"))
    }

    #[test]
    fn keyboards_mice_and_touchpads_count_accelerometers_and_joysticks_do_not() {
        assert!(counts_as_input(true, false, false, false)); // keyboard
        assert!(counts_as_input(true, true, false, true)); // mouse
        assert!(counts_as_input(true, false, true, true)); // touchpad, touch screen, tablet
        assert!(!counts_as_input(false, false, true, false)); // accelerometer
        assert!(!counts_as_input(true, false, true, false)); // joystick, gamepad
    }

    #[test]
    fn coalesces_to_at_most_ten_a_second() {
        let mut c = Coalescer::new(Duration::from_millis(100));
        let t0 = Instant::now();
        let passed = (0..100).filter(|i| c.admit(t0 + Duration::from_millis(i * 10))).count();
        assert_eq!(passed, 10); // 1 s of events every 10 ms
        assert!(c.admit(t0 + Duration::from_millis(1100)));
    }

    #[tokio::test]
    async fn input_from_a_device_reaches_subscribers_coalesced() {
        let devices = FakeDevices::default();
        devices.paths.lock().unwrap().push(p("event3"));
        let input = EvdevInput::new(Box::new(devices.clone()));
        let mut rx = input.subscribe();
        input.scan();
        let feed = devices.feeds.lock().unwrap()[&p("event3")].clone();
        for _ in 0..20 {
            feed.send(()).unwrap(); // a burst, all within a few ms
        }
        let first = tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.unwrap();
        assert!(first.is_some());
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(rx.try_recv().is_err(), "a burst within 100 ms is one input");
        assert_eq!(input.accessible(), Access::Ok);
    }

    #[test]
    fn permission_denied_is_reported_as_such() {
        let devices = FakeDevices::default();
        devices.paths.lock().unwrap().extend([p("event0"), p("event1")]);
        devices.denied.lock().unwrap().extend([p("event0"), p("event1")]);
        let input = EvdevInput::new(Box::new(devices));
        input.scan();
        assert_eq!(input.accessible(), Access::PermissionDenied);
    }

    #[test]
    fn no_devices_and_non_input_devices() {
        let devices = FakeDevices::default();
        let input = EvdevInput::new(Box::new(devices.clone()));
        input.scan();
        assert_eq!(input.accessible(), Access::NoDevices);
        devices.paths.lock().unwrap().push(p("event9"));
        devices.not_input.lock().unwrap().insert(p("event9")); // e.g. a lid switch without keys
        input.scan();
        assert_eq!(input.accessible(), Access::NoDevices);
    }

    #[test]
    fn rescan_picks_up_a_new_device_and_reopens_an_unplugged_one() {
        let devices = FakeDevices::default();
        devices.paths.lock().unwrap().push(p("event2"));
        let input = EvdevInput::new(Box::new(devices.clone()));
        input.scan();
        assert_eq!(input.open_paths(), vec![p("event2")]);
        devices.paths.lock().unwrap().push(p("event7")); // plugged in
        input.scan();
        assert_eq!(input.open_paths(), vec![p("event2"), p("event7")]);
        devices.feeds.lock().unwrap().remove(&p("event7")); // unplugged: its reader ends
        let deadline = Instant::now() + Duration::from_secs(2);
        while input.open_paths().len() != 1 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(input.open_paths(), vec![p("event2")]);
        input.scan(); // still listed (plugged back in): opened again
        assert_eq!(input.open_paths(), vec![p("event2"), p("event7")]);
    }

    #[test]
    fn access_turns_ok_once_a_device_opens() {
        let devices = FakeDevices::default();
        devices.paths.lock().unwrap().push(p("event0"));
        devices.denied.lock().unwrap().insert(p("event0"));
        let input = EvdevInput::new(Box::new(devices.clone()));
        input.scan();
        assert_eq!(input.accessible(), Access::PermissionDenied);
        devices.denied.lock().unwrap().clear(); // e.g. ACLs changed
        input.scan();
        assert_eq!(input.accessible(), Access::Ok);
    }
}
