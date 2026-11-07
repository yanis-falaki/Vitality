//! The store in a temporary folder.

use chrono::{Duration, TimeZone};
use image::Rgb;

use super::*;

fn t(h: u32, m: u32, s: u32, ms: u32) -> DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 11, 7, h, m, s).unwrap() + Duration::milliseconds(ms as i64)
}

fn pacific() -> FixedOffset {
    FixedOffset::west_opt(8 * 3600).unwrap()
}

fn img(level: u8) -> RgbImage {
    RgbImage::from_pixel(64, 48, Rgb([level, 100, 200]))
}

fn store() -> (tempfile::TempDir, Store) {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::new(&dir.path().join("captures"), 85.0);
    (dir, s)
}

fn read_json(folder: &Path) -> serde_json::Value {
    serde_json::from_slice(&fs::read(folder.join(INTERACTION_FILE)).unwrap()).unwrap()
}

#[test]
fn an_interaction_is_written_in_the_protocols_layout() {
    let (_d, s) = store();
    let mut h = s.begin(t(17, 0, 0, 449), pacific(), "America/Vancouver").unwrap();
    assert_eq!(h.name(), "2026-11-07/17-00-00");
    assert_eq!(h.add_shot(None, &img(1), t(17, 0, 0, 449), Trigger::Activity).unwrap(), "0000.webp");
    assert_eq!(h.add_shot(None, &img(2), t(17, 0, 12, 900), Trigger::Activity).unwrap(), "0012.webp");
    let folder = h.finish().unwrap().unwrap();
    assert_eq!(folder, s.root().join("2026-11-07/17-00-00"));
    let shot = fs::read(folder.join("0012.webp")).unwrap();
    assert_eq!((&shot[0..4], &shot[8..12]), (&b"RIFF"[..], &b"WEBP"[..]));
    let json = read_json(&folder);
    assert_eq!(
        json,
        serde_json::json!({
            "name": "2026-11-07/17-00-00", "source": "screen",
            "start": "2026-11-07T17:00:00.449Z", "end": "2026-11-07T17:00:12.900Z",
            "local_start": "2026-11-07T09:00:00.449-08:00", "tz": "America/Vancouver",
            "shots": [
                {"file": "0000.webp", "ts": "2026-11-07T17:00:00.449Z", "trigger": "activity", "width": 64, "height": 48,
                 "bytes": fs::metadata(folder.join("0000.webp")).unwrap().len()},
                {"file": "0012.webp", "ts": "2026-11-07T17:00:12.900Z", "trigger": "activity", "width": 64, "height": 48,
                 "bytes": shot.len()},
            ]
        })
    );
    assert!(!folder.join(PARTIAL).exists());
}

#[test]
fn several_screens_in_one_second_and_their_names() {
    let (_d, s) = store();
    let mut h = s.begin(t(17, 0, 0, 0), pacific(), "America/Vancouver").unwrap();
    assert_eq!(h.add_shot(Some("Main"), &img(1), t(17, 0, 3, 0), Trigger::Passive).unwrap(), "0003.webp");
    assert_eq!(h.add_shot(Some("Side"), &img(2), t(17, 0, 3, 0), Trigger::Passive).unwrap(), "0003-2.webp");
    let json = read_json(&h.finish().unwrap().unwrap());
    let shots = json["shots"].as_array().unwrap();
    assert_eq!(shots[1]["file"], "0003-2.webp");
    assert_eq!((shots[0]["screen_name"].as_str(), shots[1]["screen_name"].as_str()), (Some("Main"), Some("Side")));
    assert_eq!(shots[0]["trigger"], "passive");
}

#[test]
fn a_taken_name_gets_a_suffix_and_sorts_by_number() {
    let (_d, s) = store();
    let names: Vec<String> = (0..11).map(|_| s.begin(t(17, 0, 0, 0), pacific(), "UTC").unwrap().name().to_string()).collect();
    assert_eq!(&names[..3], &["2026-11-07/17-00-00", "2026-11-07/17-00-00-2", "2026-11-07/17-00-00-3"]);
    let folders = s.folders().unwrap();
    assert!(folders.last().unwrap().ends_with("17-00-00-11"), "{folders:?}");
}

#[test]
fn an_interaction_without_shots_leaves_nothing() {
    let (_d, s) = store();
    let h = s.begin(t(17, 0, 0, 0), pacific(), "UTC").unwrap();
    let folder = h.folder().to_path_buf();
    assert_eq!(h.finish().unwrap(), None);
    assert!(!folder.exists());
}

#[test]
fn utc_local_start_is_written_with_z() {
    assert_eq!(format_local(t(1, 2, 3, 4).with_timezone(&FixedOffset::east_opt(0).unwrap())), "2026-11-07T01:02:03.004Z");
    assert_eq!(format_local(t(1, 2, 3, 4).with_timezone(&FixedOffset::east_opt(5 * 3600 + 1800).unwrap())), "2026-11-07T06:32:03.004+05:30");
}

#[test]
fn an_interrupted_interaction_is_finished_from_its_notes() {
    // Review Focus 2 (the store's half; the upload of it is in upload's tests).
    let (_d, s) = store();
    let mut h = s.begin(t(17, 0, 0, 449), pacific(), "America/Vancouver").unwrap();
    h.add_shot(Some("Main"), &img(1), t(17, 0, 0, 449), Trigger::Activity).unwrap();
    h.add_shot(Some("Side"), &img(2), t(17, 0, 4, 0), Trigger::Activity).unwrap();
    let folder = h.folder().to_path_buf();
    drop(h); // the app crashed: no interaction.json
    // A last note cut short by the crash.
    let mut f = fs::OpenOptions::new().append(true).open(folder.join(PARTIAL)).unwrap();
    std::io::Write::write_all(&mut f, b"{\"file\":\"0009.we").unwrap();
    assert!(s.pending().is_empty());
    assert_eq!(s.finish_interrupted().unwrap(), vec![folder.clone()]);
    let json = read_json(&folder);
    assert_eq!(json["start"], "2026-11-07T17:00:00.449Z");
    assert_eq!(json["end"], "2026-11-07T17:00:04.000Z");
    assert_eq!(json["tz"], "America/Vancouver");
    assert_eq!(json["shots"][1]["screen_name"], "Side");
    assert!(!folder.join(PARTIAL).exists());
    assert_eq!(s.pending(), vec![folder]);
}

#[test]
fn an_interrupted_interaction_without_notes_is_finished_from_its_files() {
    let (_d, s) = store();
    let mut h = s.begin(t(17, 0, 0, 449), pacific(), "America/Vancouver").unwrap();
    h.add_shot(None, &img(1), t(17, 0, 0, 449), Trigger::Activity).unwrap();
    h.add_shot(None, &img(2), t(17, 0, 7, 500), Trigger::Activity).unwrap();
    let folder = h.folder().to_path_buf();
    drop(h);
    fs::remove_file(folder.join(PARTIAL)).unwrap();
    s.finish_interrupted().unwrap();
    let json = read_json(&folder);
    assert_eq!(json["name"], "2026-11-07/17-00-00");
    assert_eq!(json["start"], "2026-11-07T17:00:00.000Z");
    let files: Vec<&str> = json["shots"].as_array().unwrap().iter().map(|s| s["file"].as_str().unwrap()).collect();
    assert_eq!(files, ["0000.webp", "0007.webp"]);
    assert_eq!(json["shots"][1]["ts"], "2026-11-07T17:00:07.000Z");
    assert_eq!(json["shots"][1]["width"], 64);
}

#[test]
fn an_interrupted_interaction_without_shots_is_removed() {
    let (_d, s) = store();
    let folder = s.begin(t(17, 0, 0, 0), pacific(), "UTC").unwrap().folder().to_path_buf();
    assert!(s.finish_interrupted().unwrap().is_empty());
    assert!(!folder.exists());
}

#[test]
fn pending_is_complete_unmarked_folders_oldest_first() {
    let (_d, s) = store();
    let mut folders = vec![];
    for (i, start) in [t(18, 0, 0, 0), t(17, 0, 0, 0), t(19, 0, 0, 0), t(20, 0, 0, 0)].into_iter().enumerate() {
        let mut h = s.begin(start, pacific(), "UTC").unwrap();
        h.add_shot(None, &img(i as u8), start, Trigger::Activity).unwrap();
        folders.push(h.finish().unwrap().unwrap());
    }
    s.mark(&folders[2], UPLOADED).unwrap();
    s.mark(&folders[3], FAILED).unwrap();
    s.begin(t(21, 0, 0, 0), pacific(), "UTC").unwrap(); // still open: not pending
    assert_eq!(s.pending(), vec![folders[1].clone(), folders[0].clone()]);
}

#[test]
fn today_counts_by_the_local_day() {
    let (_d, s) = store();
    // 03:00 UTC on Nov 8 is Nov 7 in Vancouver; 17:00 UTC Nov 7 too; 09:00 UTC Nov 7 is 01:00.
    for start in [t(17, 0, 0, 0), t(17, 0, 0, 0) + Duration::hours(10), t(9, 0, 0, 0) - Duration::hours(9)] {
        let mut h = s.begin(start, pacific(), "America/Vancouver").unwrap();
        h.add_shot(None, &img(1), start, Trigger::Activity).unwrap();
        h.add_shot(None, &img(2), start + Duration::seconds(3), Trigger::Activity).unwrap();
        h.finish().unwrap();
    }
    let nov7 = NaiveDate::from_ymd_opt(2026, 11, 7).unwrap();
    assert_eq!(s.day_counts(nov7), TodayCounts { interactions: 2, shots: 4 });
}

#[test]
fn free_up_space_deletes_only_uploaded_interactions() {
    let (_d, s) = store();
    let mut folders = vec![];
    for (i, start) in [t(17, 0, 0, 0), t(18, 0, 0, 0), t(19, 0, 0, 0)].into_iter().enumerate() {
        let mut h = s.begin(start, pacific(), "UTC").unwrap();
        h.add_shot(None, &img(i as u8), start, Trigger::Activity).unwrap();
        folders.push(h.finish().unwrap().unwrap());
    }
    s.mark(&folders[0], UPLOADED).unwrap();
    s.mark(&folders[2], FAILED).unwrap();
    let before = s.disk_bytes();
    let freed = s.free_up_space().unwrap();
    assert_eq!(freed.interactions, 1);
    assert_eq!(s.disk_bytes(), before - freed.bytes);
    assert!(!folders[0].exists() && folders[1].exists() && folders[2].exists());
    assert_eq!(s.pending(), vec![folders[1].clone()]);
}

#[test]
fn names_are_recognised() {
    assert!(is_time("17-00-00") && is_time("17-00-00-2") && !is_time("17-00-00-1") && !is_time("17-00"));
    assert!(is_shot_file("0000.webp") && is_shot_file("0012-2.webp") && is_shot_file("12345.webp"));
    assert!(!is_shot_file("012.webp") && !is_shot_file("0012-1.webp") && !is_shot_file("0012.png"));
}

#[test]
fn time_zone_name_is_an_iana_name() {
    let tz = local_tz_name();
    assert!(tz == "UTC" || tz.contains('/'), "{tz}");
}
