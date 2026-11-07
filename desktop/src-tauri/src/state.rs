//! The pause the owner asked for, kept in `state.json` so it survives a restart:
//! `{"paused": true, "paused_until": "2026-11-07T21:00:00Z", "setup_done": true}` (`paused_until: null` = until
//! resumed). `PauseState::load/save`, `active(now)` (expired pauses are over), and
//! `tomorrow_morning(now)`: 06:00 local time the next day, for "Pause until tomorrow".

use std::path::Path;

use chrono::{DateTime, Duration, Local, NaiveTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};

use crate::paths::write_atomic;
use crate::types::PauseView;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PauseState {
    pub paused: bool,
    pub paused_until: Option<DateTime<Utc>>,
    /// Setup has been finished once: from then on it is shown only when an essential check
    /// fails.
    #[serde(default)]
    pub setup_done: bool,
}

/// How long to pause.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PauseFor {
    UntilResumed,
    Minutes(u32),
    UntilTomorrow,
}

impl PauseState {
    pub fn load(path: &Path) -> PauseState {
        std::fs::read(path).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
    }

    pub fn save(&self, path: &Path) {
        let bytes = serde_json::to_vec_pretty(self).expect("serialisable");
        if let Err(e) = write_atomic(path, &bytes) {
            eprintln!("vitality-client: cannot save the pause to {}: {e}", path.display());
        }
    }

    pub fn starting(how: PauseFor, now: DateTime<Utc>) -> PauseState {
        let until = match how {
            PauseFor::UntilResumed => None,
            PauseFor::Minutes(m) => Some(now + Duration::minutes(m as i64)),
            PauseFor::UntilTomorrow => Some(tomorrow_morning(now)),
        };
        PauseState { paused: true, paused_until: until, setup_done: false }
    }

    /// Whether the pause still holds at `now`.
    pub fn active(&self, now: DateTime<Utc>) -> bool {
        self.paused && self.paused_until.is_none_or(|u| now < u)
    }

    pub fn view(&self, now: DateTime<Utc>) -> PauseView {
        if self.active(now) {
            PauseView { paused: true, until: self.paused_until }
        } else {
            PauseView::default()
        }
    }
}

/// 06:00 local time on the day after `now`'s local day.
pub fn tomorrow_morning(now: DateTime<Utc>) -> DateTime<Utc> {
    let day = now.with_timezone(&Local).date_naive() + Duration::days(1);
    let six = day.and_time(NaiveTime::from_hms_opt(6, 0, 0).unwrap());
    Local.from_local_datetime(&six).earliest().map(|t| t.with_timezone(&Utc)).unwrap_or(now + Duration::hours(12))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_expiry() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let now = Utc.with_ymd_and_hms(2026, 11, 7, 20, 0, 0).unwrap();
        assert_eq!(PauseState::load(&path), PauseState::default());
        let p = PauseState::starting(PauseFor::Minutes(30), now);
        p.save(&path);
        let again = PauseState::load(&path);
        assert_eq!(again, p);
        assert!(again.active(now + Duration::minutes(29)));
        assert!(!again.active(now + Duration::minutes(30)));
        assert_eq!(again.view(now + Duration::minutes(31)), PauseView::default());
        assert!(PauseState::starting(PauseFor::UntilResumed, now).active(now + Duration::days(9)));
    }

    #[test]
    fn tomorrow_is_six_in_the_morning_local_time() {
        let now = Utc.with_ymd_and_hms(2026, 11, 7, 20, 0, 0).unwrap();
        let t = tomorrow_morning(now).with_timezone(&Local);
        assert_eq!(t.time(), NaiveTime::from_hms_opt(6, 0, 0).unwrap());
        assert_eq!(t.date_naive(), now.with_timezone(&Local).date_naive() + Duration::days(1));
    }
}
