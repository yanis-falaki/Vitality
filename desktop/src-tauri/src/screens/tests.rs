//! `Screens` with the fake portal and frame source.

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

use image::{Rgb, RgbImage};

use super::fakes::{FakeFeeds, FakePortal};
use super::*;

struct Setup {
    _dir: tempfile::TempDir,
    path: std::path::PathBuf,
    portal: Arc<FakePortal>,
    feeds: Arc<FakeFeeds>,
}

impl Setup {
    fn new() -> Setup {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("screens.json");
        Setup { _dir: dir, path, portal: Arc::default(), feeds: Arc::default() }
    }

    fn screens(&self) -> Screens {
        Screens::new(&self.path, self.portal.clone(), self.feeds.clone(), Threshold::default())
    }
}

fn gray(level: u8) -> RgbImage {
    RgbImage::from_pixel(320, 180, Rgb([level, level, level]))
}

fn states(s: &Screens) -> Vec<(String, ScreenState)> {
    s.statuses().into_iter().map(|st| (st.name, st.state)).collect()
}

#[tokio::test]
async fn added_screens_are_named_screen_n_and_go_live() {
    let setup = Setup::new();
    let s = setup.screens();
    let a = s.add().await.unwrap();
    s.add().await.unwrap();
    assert_eq!(states(&s), vec![("Screen 1".into(), ScreenState::Live), ("Screen 2".into(), ScreenState::Live)]);
    assert!(s.latest(&a).is_none());
    setup.feeds.push(1, gray(80));
    assert_eq!(s.latest(&a).unwrap().image.width(), 320);
    assert_eq!(s.thumbs().len(), 1);
    assert!(s.statuses()[0].last_frame_age_s.is_some());
}

#[tokio::test]
async fn a_cancelled_pick_adds_nothing() {
    let setup = Setup::new();
    let s = setup.screens();
    setup.portal.cancel_picks.store(true, Ordering::SeqCst);
    assert_eq!(s.add().await, Err(ScreensError::Portal(PortalError::Refused)));
    assert_eq!(s.count(), 0);
}

#[tokio::test]
async fn refusal_of_one_of_two_screens_at_start_leaves_the_other_capturing() {
    // Review Focus 1.
    let setup = Setup::new();
    {
        let s = setup.screens();
        s.add().await.unwrap();
        s.add().await.unwrap();
        s.rename(&s.ids()[0], "Main").unwrap();
    }
    setup.portal.refused.lock().unwrap().insert("tok-2".into());
    let s = setup.screens(); // a restart
    s.open_all().await;
    assert_eq!(states(&s), vec![("Main".into(), ScreenState::Live), ("Screen 2".into(), ScreenState::NeedsPick)]);
    assert_eq!(*setup.portal.calls.lock().unwrap().last().unwrap(), Some("tok-2".to_string()));
    setup.feeds.push(1, gray(80));
    let ids = s.ids();
    assert!(s.latest(&ids[0]).is_some(), "the accepted screen captures");
    assert_eq!(s.thumbs().keys().collect::<Vec<_>>(), vec![&ids[0]]);
    // Picking it again makes it live, with a new saved token.
    s.repick(&ids[1]).await.unwrap();
    assert_eq!(states(&s)[1].1, ScreenState::Live);
    assert_eq!(Registry::load(&setup.path).entries()[1].restore_token.as_deref(), Some("tok-3"));
}

#[tokio::test]
async fn a_cancelled_repick_keeps_the_screen_as_it_was() {
    let setup = Setup::new();
    let s = setup.screens();
    let id = s.add().await.unwrap();
    setup.portal.cancel_picks.store(true, Ordering::SeqCst);
    assert!(s.repick(&id).await.is_err());
    assert_eq!(states(&s)[0].1, ScreenState::Live);
}

#[tokio::test]
async fn rename_and_remove() {
    let setup = Setup::new();
    let s = setup.screens();
    let a = s.add().await.unwrap();
    let b = s.add().await.unwrap();
    s.rename(&b, "Side").unwrap();
    assert!(s.rename(&b, " ").is_err());
    s.remove(&a).unwrap();
    assert_eq!(states(&s), vec![("Side".into(), ScreenState::Live)]);
    assert_eq!(s.remove(&a), Err(ScreensError::NotFound));
    assert_eq!(Registry::load(&setup.path).entries().len(), 1);
}

#[tokio::test]
async fn screen_names_go_into_shots_only_with_more_than_one_screen() {
    let setup = Setup::new();
    let s = setup.screens();
    let a = s.add().await.unwrap();
    assert_eq!(s.shot_name(&a), None);
    let b = s.add().await.unwrap();
    s.rename(&b, "Side").unwrap();
    assert_eq!(s.shot_name(&a).as_deref(), Some("Screen 1"));
    assert_eq!(s.shot_name(&b).as_deref(), Some("Side"));
}

#[tokio::test]
async fn two_screens_alike_for_ten_seconds_are_duplicates() {
    let setup = Setup::new();
    let s = setup.screens();
    let a = s.add().await.unwrap();
    let b = s.add().await.unwrap();
    let t0 = Instant::now();
    setup.feeds.push(1, gray(90));
    setup.feeds.push(2, gray(92)); // within the threshold
    for secs in [0, 5, 9] {
        s.track_duplicates(t0 + Duration::from_secs(secs));
        assert!(s.duplicates().is_empty(), "at {secs} s");
    }
    s.track_duplicates(t0 + Duration::from_secs(10));
    assert_eq!(s.duplicates(), vec![(a.clone(), b.clone())]);
    assert_eq!(s.view().duplicates.len(), 1);
    // One of them changes: no longer duplicates, and the 10 s start again.
    setup.feeds.push(2, gray(200));
    s.track_duplicates(t0 + Duration::from_secs(11));
    assert!(s.duplicates().is_empty());
    setup.feeds.push(2, gray(90));
    s.track_duplicates(t0 + Duration::from_secs(12));
    s.track_duplicates(t0 + Duration::from_secs(21));
    assert!(s.duplicates().is_empty());
    s.track_duplicates(t0 + Duration::from_secs(22));
    assert_eq!(s.duplicates().len(), 1);
}

#[tokio::test]
async fn preview_is_a_jpeg_about_480_wide() {
    let setup = Setup::new();
    let s = setup.screens();
    let a = s.add().await.unwrap();
    assert!(s.preview(&a).is_none());
    setup.feeds.push(1, RgbImage::from_pixel(1920, 1080, Rgb([10, 140, 120])));
    let jpeg = s.preview(&a).unwrap();
    assert_eq!(&jpeg[..2], &[0xff, 0xd8]);
    let decoded = image::load_from_memory(&jpeg).unwrap();
    assert_eq!((decoded.width(), decoded.height()), (480, 270));
}
