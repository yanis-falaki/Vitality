//! Screens: the owner's screen-shares, each one portal session plus a name.
//!
//! `Screens` is shared (`Arc`) by the runtime and the interface's commands:
//! - `open_all()` at start reopens each saved screen from its token, never showing the picker:
//!   a screen without a token, whose token is refused, or whose restore takes longer than 8 s
//!   (the portal may be showing a picker) becomes `NeedsPick`; the others keep capturing;
//! - a feed that ends or fails makes its screen `Stopped` (its last frame is dropped, so it is
//!   never shot); `reopen_due(now)`, on every tick, reopens it from its token with a backoff
//!   (5 s doubling to 5 min);
//! - `add()` (the portal's picker, the only way besides `repick` that it is shown; the new
//!   screen is named "Screen N"), `repick(id)`,
//!   `rename(id, name)`, `remove(id)`;
//! - `latest(id)`, `thumbs()`, `statuses()`, `preview(id)` (JPEG about 480 px wide),
//!   `shot_name(id)` (the name to write in a shot: only with more than one screen);
//! - `track_duplicates(now)` on every tick and `duplicates()`: pairs whose thumbs have stayed
//!   within the change threshold for 10 s (probably one monitor shared twice).
//!
//! The portal and the frame source are traits (`portal::Portal`, `feed::FeedFactory`), so tests
//! use fakes. Frames and previews stay in memory.

pub mod feed;
pub mod portal;
pub mod registry;

use std::any::Any;
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use crate::policy::change::{changed, Thumb, Threshold};
use crate::types::{ScreenId, ScreenState, ScreenStatus, ScreensView};
use feed::{FeedFactory, Latest, Slot};
use portal::{Opened, Portal, PortalError};
use registry::Registry;

/// How long two screens must look the same to be reported as duplicates.
pub const DUPLICATE_AFTER: Duration = Duration::from_secs(10);
/// How long a restore from a saved token may take at start before the screen needs a pick.
pub const RESTORE_TIMEOUT: Duration = Duration::from_secs(8);
const REOPEN_FIRST: Duration = Duration::from_secs(5);
const REOPEN_MAX: Duration = Duration::from_secs(300);

pub struct Screens {
    registry: Mutex<Registry>,
    portal: Arc<dyn Portal>,
    feeds: Arc<dyn FeedFactory>,
    live: Mutex<HashMap<ScreenId, Live>>,
    /// Since when each pair (in the owner's order) has looked the same.
    same_since: Mutex<BTreeMap<(ScreenId, ScreenId), Instant>>,
    last_tracked: Mutex<Option<Instant>>,
    opened_all: std::sync::atomic::AtomicBool,
    threshold: Threshold,
    restore_timeout: Mutex<Duration>,
    reopen_first: Mutex<Duration>,
    me: Weak<Screens>,
    generation: std::sync::atomic::AtomicU64,
    /// The runtime to close portal sessions on (feeds end on threads of their own).
    rt: Option<tokio::runtime::Handle>,
}

struct Live {
    state: ScreenState,
    slot: Slot,
    /// Which feed this is (a late end of an older feed must not stop a newer one).
    generation: u64,
    /// While `Stopped`: when to reopen, the wait after that, and whether a reopen is running.
    reopen_at: Option<Instant>,
    backoff: Duration,
    reopening: bool,
    _feed: Option<Box<dyn feed::Feed>>,
    _keep: Option<Box<dyn Any + Send + Sync>>,
}

impl Live {
    fn with_state(state: ScreenState) -> Live {
        Live { state, slot: Slot::default(), generation: 0, reopen_at: None, backoff: Duration::ZERO, reopening: false, _feed: None, _keep: None }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ScreensError {
    NotFound,
    Portal(PortalError),
    Invalid(String),
}

impl std::fmt::Display for ScreensError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ScreensError::NotFound => f.write_str("no such screen"),
            ScreensError::Portal(e) => e.fmt(f),
            ScreensError::Invalid(why) => f.write_str(why),
        }
    }
}

fn io_err(e: std::io::Error) -> ScreensError {
    ScreensError::Invalid(e.to_string())
}

impl Screens {
    pub fn new(registry_path: &Path, portal: Arc<dyn Portal>, feeds: Arc<dyn FeedFactory>, threshold: Threshold) -> Arc<Screens> {
        Arc::new_cyclic(|me| Screens {
            registry: Mutex::new(Registry::load(registry_path)),
            portal,
            feeds,
            live: Mutex::new(HashMap::new()),
            same_since: Mutex::new(BTreeMap::new()),
            last_tracked: Mutex::new(None),
            opened_all: Default::default(),
            threshold,
            restore_timeout: Mutex::new(RESTORE_TIMEOUT),
            reopen_first: Mutex::new(REOPEN_FIRST),
            me: me.clone(),
            generation: Default::default(),
            rt: tokio::runtime::Handle::try_current().ok(),
        })
    }

    /// Shorter waits, for tests.
    #[cfg(test)]
    pub fn set_waits(&self, restore_timeout: Duration, reopen_first: Duration) {
        *self.restore_timeout.lock().unwrap() = restore_timeout;
        *self.reopen_first.lock().unwrap() = reopen_first;
    }

    /// Reopens a saved screen from its token without ever showing the picker: no token, a
    /// refusal or a restore slower than the timeout leave it `NeedsPick`.
    async fn restore(&self, id: &ScreenId, name: &str, token: Option<&str>) -> Result<Opened, PortalError> {
        let Some(token) = token else {
            eprintln!("vitality-client: screen {name:?} has no saved permission: pick it again");
            return Err(PortalError::Refused);
        };
        let timeout = *self.restore_timeout.lock().unwrap();
        match tokio::time::timeout(timeout, self.portal.open(Some(token))).await {
            Ok(Ok(opened)) => Ok(opened),
            Ok(Err(e)) => {
                eprintln!("vitality-client: screen {name:?} ({id}) could not be reopened: {e}");
                Err(e)
            }
            Err(_) => {
                eprintln!("vitality-client: reopening screen {name:?} took over {timeout:?} (a picker?): pick it again");
                Err(PortalError::Refused)
            }
        }
    }

    /// Reopens every saved screen from its token, one after the other (screens already open,
    /// e.g. added meanwhile, are left alone).
    pub async fn open_all(&self) {
        let entries: Vec<_> = {
            let live = self.live.lock().unwrap();
            self.registry.lock().unwrap().entries().iter().filter(|e| !live.contains_key(&e.id)).cloned().collect()
        };
        for entry in &entries {
            self.live.lock().unwrap().insert(entry.id.clone(), Live::with_state(ScreenState::Opening));
        }
        for entry in entries {
            let result = self.restore(&entry.id, &entry.name, entry.restore_token.as_deref()).await;
            // A refusal (or any failure) leaves the saved token: the owner may fix the portal.
            let _ = self.attach(&entry.id, result);
        }
        self.opened_all.store(true, std::sync::atomic::Ordering::SeqCst);
    }

    /// Whether `open_all` has finished.
    #[allow(dead_code)] // the `screens_open` health check (milestone 3) and tests
    pub fn opened_all(&self) -> bool {
        self.opened_all.load(std::sync::atomic::Ordering::SeqCst)
    }

    /// Asks the portal's picker for a new screen; it is added at the end as "Screen N".
    pub async fn add(&self) -> Result<ScreenId, ScreensError> {
        let opened = self.portal.open(None).await.map_err(ScreensError::Portal)?;
        let entry = self.registry.lock().unwrap().add(opened.restore_token.clone()).map_err(io_err)?;
        self.attach(&entry.id, Ok(opened))?;
        Ok(entry.id)
    }

    /// Picks a screen again with the portal's picker (its old share is replaced).
    pub async fn repick(&self, id: &ScreenId) -> Result<(), ScreensError> {
        if self.registry.lock().unwrap().get(id).is_none() {
            return Err(ScreensError::NotFound);
        }
        let previous = self.live.lock().unwrap().get(id).map(|l| l.state);
        self.live.lock().unwrap().entry(id.clone()).or_insert_with(|| Live::with_state(ScreenState::Opening)).state = ScreenState::Opening;
        let result = self.portal.open(None).await;
        if let Err(e) = result {
            // Cancelled: the screen stays as it was.
            if let Some(live) = self.live.lock().unwrap().get_mut(id) {
                live.state = previous.unwrap_or(ScreenState::NeedsPick);
            }
            return Err(ScreensError::Portal(e));
        }
        self.attach(id, result)
    }

    pub fn rename(&self, id: &ScreenId, name: &str) -> Result<(), ScreensError> {
        match self.registry.lock().unwrap().rename(id, name) {
            Ok(true) => Ok(()),
            Ok(false) => Err(ScreensError::NotFound),
            Err(e) => Err(io_err(e)),
        }
    }

    pub fn remove(&self, id: &ScreenId) -> Result<(), ScreensError> {
        let gone = self.live.lock().unwrap().remove(id);
        drop(gone); // stops its feed and closes its portal session (outside the lock)
        self.same_since.lock().unwrap().retain(|(a, b), _| a != id && b != id);
        match self.registry.lock().unwrap().remove(id) {
            Ok(true) => Ok(()),
            Ok(false) => Err(ScreensError::NotFound),
            Err(e) => Err(io_err(e)),
        }
    }

    /// Starts the feed of an opened share (and saves its new token), or marks the screen as
    /// needing a new pick.
    fn attach(&self, id: &ScreenId, result: Result<Opened, PortalError>) -> Result<(), ScreensError> {
        let live = match result {
            Ok(opened) => {
                if opened.restore_token.is_some() {
                    // Never replace a saved permission with none.
                    self.registry.lock().unwrap().set_token(id, opened.restore_token.clone()).map_err(io_err)?;
                }
                let slot = Slot::default();
                let generation = self.generation.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
                let (me, who) = (self.me.clone(), id.clone());
                let on_end: feed::OnEnd = Box::new(move |why: String| {
                    if let Some(screens) = me.upgrade() {
                        screens.feed_ended(&who, generation, &why);
                    }
                });
                match self.feeds.start(opened.node_id, opened.fd, slot.clone(), on_end) {
                    Ok(feed) => Live { state: ScreenState::Live, slot, generation, _feed: Some(feed), _keep: Some(opened.keep), ..Live::with_state(ScreenState::Live) },
                    Err(why) => {
                        eprintln!("vitality-client: cannot read frames of screen {id}: {why}");
                        Live::with_state(ScreenState::NeedsPick)
                    }
                }
            }
            Err(_) => Live::with_state(ScreenState::NeedsPick),
        };
        self.live.lock().unwrap().insert(id.clone(), live);
        Ok(())
    }

    /// A feed ended or failed: the screen is `Stopped`, its last frame dropped, and it is
    /// reopened from its token later (`reopen_due`).
    fn feed_ended(&self, id: &ScreenId, generation: u64, why: &str) {
        let first = *self.reopen_first.lock().unwrap();
        let old = {
            let mut live = self.live.lock().unwrap();
            let Some(l) = live.get_mut(id).filter(|l| l.generation == generation && l.state == ScreenState::Live) else { return };
            eprintln!("vitality-client: the share of screen {id} stopped ({why}); reopening it");
            *l.slot.lock().unwrap() = None;
            l.state = ScreenState::Stopped;
            l.reopen_at = Some(Instant::now() + first);
            l.backoff = first;
            (l._feed.take(), l._keep.take())
        };
        // Dropped elsewhere: the feed's own thread may be the one calling. On the runtime when
        // there is one, so the portal session is closed too.
        match &self.rt {
            Some(rt) => drop(rt.spawn_blocking(move || drop(old))),
            None => drop(std::thread::spawn(move || drop(old))),
        }
    }

    /// Reopens `Stopped` screens whose wait is over (from their tokens, never with a picker).
    pub fn reopen_due(&self, now: Instant) {
        let due: Vec<ScreenId> = {
            let mut live = self.live.lock().unwrap();
            live.iter_mut()
                .filter(|(_, l)| l.state == ScreenState::Stopped && !l.reopening && l.reopen_at.is_some_and(|t| t <= now))
                .map(|(id, l)| {
                    l.reopening = true;
                    id.clone()
                })
                .collect()
        };
        for id in due {
            let Some(me) = self.me.upgrade() else { return };
            let Ok(rt) = tokio::runtime::Handle::try_current() else { return };
            rt.spawn(async move {
                let entry = me.registry.lock().unwrap().get(&id).cloned();
                let Some(entry) = entry else { return };
                match me.restore(&id, &entry.name, entry.restore_token.as_deref()).await {
                    Ok(opened) => {
                        let _ = me.attach(&id, Ok(opened));
                    }
                    Err(PortalError::Refused) => {
                        let _ = me.attach(&id, Err(PortalError::Refused));
                    }
                    Err(_) => {
                        // The portal is not back yet: wait longer.
                        if let Some(l) = me.live.lock().unwrap().get_mut(&id) {
                            l.reopening = false;
                            l.backoff = (l.backoff * 2).min(REOPEN_MAX);
                            l.reopen_at = Some(Instant::now() + l.backoff);
                        }
                    }
                }
            });
        }
    }

    /// The screen's newest frame (only while it is live: a stopped feed's frame is stale).
    pub fn latest(&self, id: &ScreenId) -> Option<Latest> {
        self.live.lock().unwrap().get(id).filter(|l| l.state == ScreenState::Live).and_then(|l| l.slot.lock().unwrap().clone())
    }

    /// The names of the screens that need the owner (to be picked again, or stopped).
    pub fn needing_attention(&self) -> Vec<String> {
        self.statuses().into_iter().filter(|s| matches!(s.state, ScreenState::NeedsPick | ScreenState::Stopped)).map(|s| s.name).collect()
    }

    /// The newest thumb of every live screen that has a frame.
    pub fn thumbs(&self) -> BTreeMap<ScreenId, Thumb> {
        let live = self.live.lock().unwrap();
        live.iter()
            .filter(|(_, l)| l.state == ScreenState::Live)
            .filter_map(|(id, l)| l.slot.lock().unwrap().as_ref().map(|f| (id.clone(), f.thumb.clone())))
            .collect()
    }

    /// Every saved screen in the owner's order, with its state.
    pub fn statuses(&self) -> Vec<ScreenStatus> {
        let entries = self.registry.lock().unwrap().entries().to_vec();
        let live = self.live.lock().unwrap();
        entries
            .into_iter()
            .map(|e| {
                let l = live.get(&e.id);
                let age = l.and_then(|l| l.slot.lock().unwrap().as_ref().map(|f| f.at.elapsed().as_secs_f64()));
                ScreenStatus {
                    state: l.map(|l| l.state).unwrap_or(ScreenState::Opening),
                    last_frame_age_s: age.map(|a| (a * 10.0).round() / 10.0),
                    id: e.id,
                    name: e.name,
                }
            })
            .collect()
    }

    pub fn view(&self) -> ScreensView {
        ScreensView { screens: self.statuses(), duplicates: self.duplicates() }
    }

    pub fn count(&self) -> usize {
        self.registry.lock().unwrap().entries().len()
    }

    /// The order of the screens (for shots: one file per screen, in this order).
    pub fn ids(&self) -> Vec<ScreenId> {
        self.registry.lock().unwrap().entries().iter().map(|e| e.id.clone()).collect()
    }

    /// The name to write in a shot of `id`: its name with more than one screen, else none.
    pub fn shot_name(&self, id: &ScreenId) -> Option<String> {
        let registry = self.registry.lock().unwrap();
        if registry.entries().len() < 2 {
            return None;
        }
        registry.get(id).map(|e| e.name.clone())
    }

    /// A JPEG of the newest frame, about 480 px wide.
    pub fn preview(&self, id: &ScreenId) -> Option<Vec<u8>> {
        let latest = self.latest(id)?;
        let img = &latest.image;
        let w = 480.min(img.width()).max(1);
        let h = ((img.height() as u64 * w as u64) / img.width().max(1) as u64).max(1) as u32;
        let small = image::imageops::resize(img.as_ref(), w, h, image::imageops::FilterType::Triangle);
        let mut out = vec![];
        image::codecs::jpeg::JpegEncoder::new_with_quality(&mut out, 78).encode_image(&small).ok()?;
        Some(out)
    }

    /// Updates which pairs of screens look the same; call on every tick.
    pub fn track_duplicates(&self, now: Instant) {
        let thumbs = self.thumbs();
        let order = self.ids(); // pairs in the owner's order
        let ids: Vec<&ScreenId> = order.iter().filter(|id| thumbs.contains_key(*id)).collect();
        let mut same = self.same_since.lock().unwrap();
        let mut seen = vec![];
        for (i, a) in ids.iter().enumerate() {
            for b in &ids[i + 1..] {
                let pair = ((*a).clone(), (*b).clone());
                if changed(&thumbs[*a], &thumbs[*b], &self.threshold) {
                    same.remove(&pair);
                } else {
                    same.entry(pair.clone()).or_insert(now);
                    seen.push(pair);
                }
            }
        }
        same.retain(|pair, _| seen.contains(pair));
        self.last_tracked.lock().unwrap().replace(now);
    }

    /// Pairs that have looked the same for at least 10 s at the last `track_duplicates`.
    pub fn duplicates(&self) -> Vec<(ScreenId, ScreenId)> {
        let Some(now) = *self.last_tracked.lock().unwrap() else { return vec![] };
        self.same_since
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, since)| now.duration_since(**since) >= DUPLICATE_AFTER)
            .map(|(pair, _)| pair.clone())
            .collect()
    }
}

#[cfg(test)]
pub mod fakes;
#[cfg(test)]
mod tests;
