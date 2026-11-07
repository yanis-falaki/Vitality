// Sample values of the types the core sends, written by the Rust test `types::tests`
// (`UPDATE_FIXTURES=1 cargo test types` rewrites it). Typed with core.ts's mirrors, so the
// type check fails when Rust and TypeScript drift apart. Also the fake core's data.
import type { ScreensView, StatusView } from "./core";

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
