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
  "input": "permission_denied",
  "pause": {
    "paused": true,
    "until": "2026-11-07T21:00:00Z"
  },
  "screens": 2,
  "screens_attention": [],
  "screens_live": 2,
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
  "input": "no_devices",
  "pause": {
    "paused": false,
    "until": null
  },
  "screens": 0,
  "screens_attention": [],
  "screens_live": 0,
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
      "text": "start active interaction 2026-11-07/20-02-00"
    },
    {
      "at": "2026-11-07T20:02:00Z",
      "text": "R1 shot activity · Main (0000.webp), Screen 2 (0000-2.webp)"
    },
    {
      "at": "2026-11-07T20:02:04Z",
      "text": "R2 skipped: no screen changed (R4)"
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
    "id": "R1",
    "text": "Any keyboard or mouse input after at least 2 s without input starts an active interaction, with a shot of every screen.",
    "title": "Start active"
  },
  {
    "id": "R2",
    "text": "2 s after the last input, a shot.",
    "title": "Settle"
  },
  {
    "id": "R3",
    "text": "While you are continuously active, a shot at least every 10 s.",
    "title": "Maximum gap"
  },
  {
    "id": "R4",
    "text": "A shot includes a screen only if it changed since that screen's own last shot: more than 0.5 % of a small grayscale copy (64 × 36) moved by more than 16 levels. A shot in which no screen changed is skipped. R1 shots include every screen.",
    "title": "Which screens"
  },
  {
    "id": "R5",
    "text": "After the R2 shot, if no input follows within 2 s, the interaction ends and is uploaded.",
    "title": "End active"
  },
  {
    "id": "R6",
    "text": "While idle, every 45 s, each screen is compared with its last shot by R4's rule; if any changed, a shot of the changed screens, marked passive.",
    "title": "Passive check"
  },
  {
    "id": "R7",
    "text": "The first passive shot starts a passive interaction; every passive shot until it ends belongs to it. It ends on any input (R1 then starts an active one), after 3 checks in a row without change, or on pause. It holds only passive shots.",
    "title": "Passive interaction"
  },
  {
    "id": "R8",
    "text": "While paused, nothing is captured and no checks run; an open interaction ends. A locked screen is not a pause: the lock screen is captured like any other. When the machine sleeps, an open interaction ends as it wakes, with no shot for the time asleep.",
    "title": "Stop"
  },
  {
    "id": "R9",
    "text": "When an interaction's shots reach 40 MiB (or 900 shots), it ends and a new one of the same kind starts at once with a shot of every screen, so every upload stays under the server's limit.",
    "title": "Size budget"
  }
];

export const settings: Settings = {
  "change_fraction": 0.005,
  "change_levels": 16,
  "disk_warning_gb": 5.0,
  "max_gap_s": 10.0,
  "max_interaction_mib": 40,
  "passive_every_s": 45.0,
  "passive_quiet_checks": 3,
  "server": "http://vitality-librarian:8100",
  "settle_s": 2.0,
  "start_at_login": true,
  "start_gap_s": 2.0,
  "webp_quality": 85.0
};

export const freed: Freed = {
  "bytes": 48000000,
  "interactions": 12
};
