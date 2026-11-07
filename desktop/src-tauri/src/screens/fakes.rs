//! Fakes of the portal and the frame source, for tests of `Screens` and the runtime.
//!
//! `FakePortal`: a pick gives node n and token "tok-n" (n counting up from 1); reopening with
//! "tok-n" gives node n again, unless the token is in `refused` or `cancel_picks` is set.
//! `FakeFeeds`: remembers each feed's slot by node; `push(node, image)` delivers a frame.

use std::collections::{HashMap, HashSet};
use std::os::fd::OwnedFd;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use async_trait::async_trait;
use image::RgbImage;

use super::feed::{Feed, FeedFactory, Latest, Slot};
use super::portal::{Opened, Portal, PortalError};

#[derive(Default)]
pub struct FakePortal {
    next: AtomicU32,
    pub refused: Mutex<HashSet<String>>,
    pub cancel_picks: AtomicBool,
    pub calls: Mutex<Vec<Option<String>>>,
}

fn null_fd() -> OwnedFd {
    std::fs::File::open("/dev/null").unwrap().into()
}

#[async_trait]
impl Portal for FakePortal {
    async fn open(&self, token: Option<&str>) -> Result<Opened, PortalError> {
        self.calls.lock().unwrap().push(token.map(str::to_string));
        let node_id = match token {
            Some(t) if self.refused.lock().unwrap().contains(t) => return Err(PortalError::Refused),
            Some(t) => t.trim_start_matches("tok-").parse().map_err(|_| PortalError::Refused)?,
            None if self.cancel_picks.load(Ordering::SeqCst) => return Err(PortalError::Refused),
            None => self.next.fetch_add(1, Ordering::SeqCst) + 1,
        };
        Ok(Opened { node_id, fd: null_fd(), restore_token: Some(format!("tok-{node_id}")), keep: Box::new(()) })
    }
}

#[derive(Default)]
pub struct FakeFeeds {
    pub slots: Mutex<HashMap<u32, Slot>>,
}

struct FakeFeed;
impl Feed for FakeFeed {}

impl FeedFactory for FakeFeeds {
    fn start(&self, node_id: u32, _fd: OwnedFd, slot: Slot) -> Result<Box<dyn Feed>, String> {
        self.slots.lock().unwrap().insert(node_id, slot);
        Ok(Box::new(FakeFeed))
    }
}

impl FakeFeeds {
    pub fn push(&self, node_id: u32, image: RgbImage) {
        self.push_at(node_id, image, Instant::now());
    }

    pub fn push_at(&self, node_id: u32, image: RgbImage, at: Instant) {
        let slots = self.slots.lock().unwrap();
        let slot = slots.get(&node_id).expect("no feed for this node");
        *slot.lock().unwrap() = Some(Latest::new(image, at));
    }
}
