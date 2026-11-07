//! The tray icon (StatusNotifierItem, through `ksni`; portable across GNOME with its
//! AppIndicator extension, KDE, Hyprland and Sway bars).
//!
//! The icon: a filled dot while recording, a hollow ring when paused, grey with a mark when
//! something needs attention. Left click toggles pause. The menu: the state line, Pause for
//! 30 min / 1 h / until tomorrow (or Resume), Open Vitality, Quit. `look(status, shots_today)`
//! (pure) decides icon and line; `start(core, act)` shows the icon and keeps it current; menu
//! choices come back as `TrayAction`s to `act`. The icon registers whenever a tray host
//! (`org.kde.StatusNotifierWatcher`) appears, also if it starts after the app (Waybar).

use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Local, Utc};

use crate::runtime::Core;
use crate::state::PauseFor;
use crate::types::{StatusView, UploadState};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayIcon {
    Recording,
    Paused,
    Attention,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayAction {
    TogglePause,
    Pause(PauseFor),
    Resume,
    Open,
    Quit,
}

/// What needs the owner, if anything (the first of: input, screens, the server).
pub fn attention(s: &StatusView) -> Option<String> {
    let names = s.screens_attention.join(", ");
    attention_kind(s)
        .map(|why| if why.starts_with("a screen") && !names.is_empty() { format!("{names}: pick again in Vitality") } else { why.to_string() })
        .or_else(|| s.health_warnings.first().cloned())
}

fn attention_kind(s: &StatusView) -> Option<&'static str> {
    if s.input == "permission_denied" {
        Some("no access to keyboard and mouse")
    } else if s.input == "no_devices" {
        Some("no keyboard or mouse found")
    } else if s.screens == 0 {
        Some("no screen set up")
    } else if !s.screens_attention.is_empty() || s.screens_live < s.screens {
        Some("a screen needs to be picked again or has stopped")
    } else if matches!(s.upload.state, UploadState::Rejected { .. }) {
        Some("the server refuses this device")
    } else if s.upload.last_failure.is_some() {
        Some("the server refused an interaction for good")
    } else {
        None
    }
}

/// The icon and the state line for `s`.
pub fn look(s: &StatusView, shots_today: u32) -> (TrayIcon, String) {
    let local = |t: &DateTime<Utc>| t.with_timezone(&Local).format("%H:%M").to_string();
    if s.pause.paused {
        let line = match &s.pause.until {
            Some(u) => format!("Paused until {}", local(u)),
            None => "Paused until resumed".into(),
        };
        return (TrayIcon::Paused, line);
    }
    if let Some(why) = attention(s) {
        return (TrayIcon::Attention, format!("Needs attention: {why}"));
    }
    let shots = if shots_today == 1 { "1 shot".to_string() } else { format!("{shots_today} shots") };
    (TrayIcon::Recording, format!("Recording · {shots} today"))
}

struct VitalityTray {
    icon: TrayIcon,
    line: String,
    paused: bool,
    act: Arc<dyn Fn(TrayAction) + Send + Sync>,
}

impl ksni::Tray for VitalityTray {
    fn id(&self) -> String {
        "vitality-client".into()
    }

    fn title(&self) -> String {
        "Vitality".into()
    }

    fn icon_pixmap(&self) -> Vec<ksni::Icon> {
        [22, 32, 48].into_iter().map(|size| pixmap(self.icon, size)).collect()
    }

    fn tool_tip(&self) -> ksni::ToolTip {
        ksni::ToolTip { title: "Vitality".into(), description: self.line.clone(), ..Default::default() }
    }

    fn watcher_online(&self) {
        eprintln!("vitality-client: a tray host appeared; the tray icon is shown");
    }

    fn watcher_offline(&self, reason: ksni::OfflineReason) -> bool {
        eprintln!("vitality-client: no tray host for now ({reason:?}); the icon appears when one starts");
        true // keep waiting for one
    }

    fn activate(&mut self, _x: i32, _y: i32) {
        (self.act)(TrayAction::TogglePause);
    }

    fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
        use ksni::menu::{MenuItem, StandardItem};
        let item = |label: &str, action: TrayAction| -> MenuItem<Self> {
            StandardItem {
                label: label.into(),
                activate: Box::new(move |t: &mut Self| (t.act)(action)),
                ..Default::default()
            }
            .into()
        };
        let mut items: Vec<MenuItem<Self>> = vec![
            StandardItem { label: self.line.clone(), enabled: false, ..Default::default() }.into(),
            MenuItem::Separator,
        ];
        if self.paused {
            items.push(item("Resume", TrayAction::Resume));
        }
        items.push(item("Pause for 30 min", TrayAction::Pause(PauseFor::Minutes(30))));
        items.push(item("Pause for 1 h", TrayAction::Pause(PauseFor::Minutes(60))));
        items.push(item("Pause until tomorrow", TrayAction::Pause(PauseFor::UntilTomorrow)));
        items.push(MenuItem::Separator);
        items.push(item("Open Vitality", TrayAction::Open));
        items.push(item("Quit", TrayAction::Quit));
        items
    }
}

/// Shows the tray icon and keeps it in step with the core (at once, then at most every 2 s). Without a tray
/// host on the desktop it says so on stderr and does nothing more.
pub async fn start(core: Arc<Core>, act: Arc<dyn Fn(TrayAction) + Send + Sync>) {
    use ksni::TrayMethods;
    let status = core.status();
    let (icon, line) = look(&status, core.store.today().shots);
    let tray = VitalityTray { icon, line, paused: status.pause.paused, act };
    let handle = match tray.assume_sni_available(true).spawn().await {
        Ok(h) => h,
        Err(e) => {
            eprintln!("vitality-client: no tray icon ({e}); open the window from the app launcher");
            return;
        }
    };
    let mut changes = core.subscribe();
    tokio::spawn(async move {
        // Redraw at once on a change (a click must show immediately); changes that follow
        // within 2 s are gathered into one more redraw at the end of that window.
        let mut burst = false;
        loop {
            if burst {
                tokio::time::sleep(Duration::from_secs(2)).await;
                burst = false;
                if changes.try_recv().is_err() {
                    continue; // nothing more happened in the window
                }
                while changes.try_recv().is_ok() {}
            } else {
                let _ = changes.recv().await;
                burst = true;
            }
            let status = core.status();
            let shots = tokio::task::spawn_blocking({
                let store = core.store.clone();
                move || store.today().shots
            })
            .await
            .unwrap_or(0);
            let (icon, line) = look(&status, shots);
            if handle
                .update(move |t| {
                    t.icon = icon;
                    t.line = line;
                    t.paused = status.pause.paused;
                })
                .await
                .is_none()
            {
                return; // the tray is gone
            }
        }
    });
}

/// The icon drawn at `size` px, ARGB32 in network byte order.
pub fn pixmap(icon: TrayIcon, size: i32) -> ksni::Icon {
    const TEAL: [u8; 3] = [0x00, 0x92, 0x82]; // the theme's --activity
    const GREY: [u8; 3] = [0x8f, 0x88, 0x7d]; // a grey that reads on light and dark bars
    let s = size as f32;
    let (c, r) = (s / 2.0, s * 0.40);
    let ring = (s * 0.12).max(2.0);
    let mut data = Vec::with_capacity((size * size * 4) as usize);
    for y in 0..size {
        for x in 0..size {
            let (px, py) = (x as f32 + 0.5, y as f32 + 0.5);
            let d = ((px - c).powi(2) + (py - c).powi(2)).sqrt();
            let disk = (r - d + 0.5).clamp(0.0, 1.0);
            let (rgb, alpha) = match icon {
                TrayIcon::Recording => (TEAL, disk),
                TrayIcon::Paused => (GREY, disk.min((d - (r - ring) + 0.5).clamp(0.0, 1.0))),
                TrayIcon::Attention => {
                    // An exclamation mark cut out of a grey disk.
                    let bar = (px - c).abs() < s * 0.07 && py > s * 0.24 && py < s * 0.58;
                    let dot = ((px - c).powi(2) + (py - s * 0.70).powi(2)).sqrt() < s * 0.08;
                    (GREY, if bar || dot { 0.0 } else { disk })
                }
            };
            data.extend_from_slice(&[(alpha * 255.0).round() as u8, rgb[0], rgb[1], rgb[2]]);
        }
    }
    ksni::Icon { width: size, height: size, data }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::samples;

    #[test]
    fn the_icon_and_line_follow_the_status() {
        let mut s = samples::status();
        (s.screens_live, s.screens_attention, s.health_warnings) = (2, vec![], vec![]);
        assert_eq!(look(&s, 214), (TrayIcon::Recording, "Recording · 214 shots today".into()));
        let mut warned = s.clone();
        warned.health_warnings = vec!["Disk space: 1.2 GB free, under the warning level of 5 GB".into()];
        assert_eq!(look(&warned, 0).1, "Needs attention: Disk space: 1.2 GB free, under the warning level of 5 GB", "health warnings reach the tray");
        assert_eq!(look(&s, 1).1, "Recording · 1 shot today");
        let paused = samples::status_paused();
        let (icon, line) = look(&paused, 0);
        assert_eq!(icon, TrayIcon::Paused);
        assert!(line.starts_with("Paused until "), "{line}");
        let mut idle = samples::status_idle();
        assert_eq!(look(&idle, 0), (TrayIcon::Attention, "Needs attention: no keyboard or mouse found".into()));
        idle.input = "ok".into();
        assert_eq!(look(&idle, 0).1, "Needs attention: no screen set up");
        assert_eq!(look(&samples::status(), 0).1, "Needs attention: Screen 2: pick again in Vitality");
        let mut s = samples::status();
        (s.screens_live, s.screens_attention, s.health_warnings) = (2, vec![], vec![]);
        s.upload.last_failure = Some("x: too large for the server (413)".into());
        assert_eq!(look(&s, 0).1, "Needs attention: the server refused an interaction for good");
    }

    fn alpha_at(icon: &ksni::Icon, x: i32, y: i32) -> u8 {
        icon.data[((y * icon.width + x) * 4) as usize]
    }

    #[test]
    fn dot_ring_and_mark() {
        let rec = pixmap(TrayIcon::Recording, 32);
        assert_eq!(rec.data.len(), 32 * 32 * 4);
        assert_eq!(alpha_at(&rec, 16, 16), 255);
        assert_eq!(alpha_at(&rec, 0, 0), 0);
        let paused = pixmap(TrayIcon::Paused, 32);
        assert_eq!(alpha_at(&paused, 16, 16), 0, "hollow");
        assert_eq!(alpha_at(&paused, 16, 4), 255, "the ring");
        let att = pixmap(TrayIcon::Attention, 32);
        assert_eq!(alpha_at(&att, 16, 12), 0, "the mark");
        assert_eq!(alpha_at(&att, 9, 16), 255);
    }
}
