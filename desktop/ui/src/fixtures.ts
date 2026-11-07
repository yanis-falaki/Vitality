// Sample values of the types the core sends, written by the Rust test `types::tests`
// (`UPDATE_FIXTURES=1 cargo test types` rewrites it). Typed with core.ts's mirrors, so the
// type check fails when Rust and TypeScript drift apart. Also the fake core's data.
import type { Freed, RuleText, ScreensView, Settings, StatusPage, StatusView } from "./core";

export const screens: ScreensView = {
  "duplicates": [
    [
      "s1",
      "s3"
    ]
  ],
  "screens": [
    {
      "id": "s1",
      "last_frame_age_s": 0.4,
      "name": "Main",
      "state": "live"
    },
    {
      "id": "s2",
      "last_frame_age_s": null,
      "name": "Screen 2",
      "state": "needs_pick"
    },
    {
      "id": "s3",
      "last_frame_age_s": null,
      "name": "Screen 3",
      "state": "opening"
    },
    {
      "id": "s4",
      "last_frame_age_s": null,
      "name": "Screen 4",
      "state": "stopped"
    }
  ]
};

export const statusActive: StatusView = {
  "capture": {
    "mode": "active",
    "next_check_at": null,
    "shots": 14,
    "since": "2026-11-07T20:02:00Z",
    "stopped_by": null
  },
  "health_warnings": [
    "Screens: Screen 2 is not live"
  ],
  "input": "ok",
  "pause": {
    "paused": false,
    "until": null
  },
  "screens": 2,
  "screens_attention": [
    "Screen 2"
  ],
  "screens_live": 1,
  "setup_done": true,
  "upload": {
    "failed": 0,
    "last_failure": null,
    "last_upload": "2026-11-07T20:01:30Z",
    "pending": 0,
    "state": "ok"
  }
};

export const statusPaused: StatusView = {
  "capture": {
    "mode": "stopped",
    "next_check_at": null,
    "shots": 0,
    "since": null,
    "stopped_by": "paused"
  },
  "health_warnings": [
    "Keyboard and mouse: not allowed to read /dev/input: you are not in the input group"
  ],
  "input": "permission_denied",
  "pause": {
    "paused": true,
    "until": "2026-11-07T21:00:00Z"
  },
  "screens": 2,
  "screens_attention": [],
  "screens_live": 2,
  "setup_done": true,
  "upload": {
    "device": "desktop",
    "failed": 1,
    "last_failure": "2026-11-07/17-00-00: too large for the server (413)",
    "last_upload": null,
    "pending": 3,
    "state": "rejected"
  }
};

export const statusIdle: StatusView = {
  "capture": {
    "mode": "idle",
    "next_check_at": "2026-11-07T20:03:15Z",
    "shots": 0,
    "since": null,
    "stopped_by": null
  },
  "health_warnings": [],
  "input": "no_devices",
  "pause": {
    "paused": false,
    "until": null
  },
  "screens": 0,
  "screens_attention": [],
  "screens_live": 0,
  "setup_done": false,
  "upload": {
    "failed": 0,
    "last_failure": null,
    "last_upload": null,
    "pending": 1,
    "state": "offline"
  }
};

export const statusPage: StatusPage = {
  "checks": [
    {
      "detail": "4 input devices open",
      "essential": true,
      "fix": null,
      "id": "input_access",
      "ok": true,
      "title": "Keyboard and mouse"
    },
    {
      "detail": "Screen 2 needs to be picked again",
      "essential": true,
      "fix": "Open Screens and pick Screen 2 again.",
      "id": "screens_open",
      "ok": false,
      "title": "Screens"
    },
    {
      "detail": "accepted as \"desktop\"",
      "essential": false,
      "fix": null,
      "id": "device_accepted",
      "ok": true,
      "title": "This device accepted by the server"
    }
  ],
  "disk": {
    "captures_bytes": 1843200000,
    "free_bytes": 96000000000,
    "low": false
  },
  "recent_decisions": [
    {
      "at": "2026-11-07T20:02:00Z",
      "text": "Screenshot of 2 screens — you started using the computer · 2026-11-07/20-02-00"
    },
    {
      "at": "2026-11-07T20:02:00Z",
      "text": "Screenshot of 2 screens — you've been busy for 10 s"
    },
    {
      "at": "2026-11-07T20:02:04Z",
      "text": "Checked idle screens — nothing changed"
    }
  ],
  "status": {
    "capture": {
      "mode": "active",
      "next_check_at": null,
      "shots": 14,
      "since": "2026-11-07T20:02:00Z",
      "stopped_by": null
    },
    "health_warnings": [
      "Screens: Screen 2 is not live"
    ],
    "input": "ok",
    "pause": {
      "paused": false,
      "until": null
    },
    "screens": 2,
    "screens_attention": [
      "Screen 2"
    ],
    "screens_live": 1,
    "setup_done": true,
    "upload": {
      "failed": 0,
      "last_failure": null,
      "last_upload": "2026-11-07T20:01:30Z",
      "pending": 0,
      "state": "ok"
    }
  },
  "today": {
    "interactions": 37,
    "shots": 214
  }
};

export const rules: RuleText[] = [
  {
    "id": "start",
    "text": "Any keyboard or mouse input while no interaction is open starts an active interaction, with a shot of every screen.",
    "title": "Start"
  },
  {
    "id": "busy",
    "text": "While you keep using the keyboard or mouse, a shot of every screen at least every 10 s.",
    "title": "While you're busy"
  },
  {
    "id": "end",
    "text": "After 3 s without input, a closing shot of every screen, and the interaction ends and is uploaded.",
    "title": "End"
  },
  {
    "id": "idle",
    "text": "While idle, every 45 s, each screen is compared with its last shot: it changed if more than 0.5 % of a small grayscale copy (64 × 36) moved by more than 16 levels. If any screen changed, a passive shot of every screen. The first one starts a passive interaction; it ends on any input (then an active one starts) or after 3 checks in a row without change. Passive and active shots never share an interaction.",
    "title": "While you're idle"
  },
  {
    "id": "pause",
    "text": "While paused, nothing is captured and no checks run; an open interaction ends. A locked screen is not a pause: the lock screen is captured like any other. When the machine sleeps, an open interaction ends as it wakes, with no shot for the time asleep.",
    "title": "Pause"
  },
  {
    "id": "size",
    "text": "When an interaction's shots reach 40 MiB (or 900 shots), it ends and a new one of the same kind starts at once with a shot of every screen, so every upload stays under the server's limit.",
    "title": "Size limit"
  }
];

export const settings: Settings = {
  "change_fraction": 0.005,
  "change_levels": 16,
  "disk_warning_gb": 5.0,
  "end_after_idle_s": 3.0,
  "max_gap_s": 10.0,
  "max_interaction_mib": 40,
  "passive_every_s": 45.0,
  "passive_quiet_checks": 3,
  "server": "http://vitality-librarian:8100",
  "start_at_login": true,
  "webp_quality": 85.0
};

export const freed: Freed = {
  "bytes": 48000000,
  "interactions": 12
};
