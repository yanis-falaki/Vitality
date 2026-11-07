// The typed link to the Rust core: the types it sends (mirrors of src-tauri/src/types.rs, checked by
// fixtures.ts) and one function per Tauri command, plus `onStateChanged` for the `state-changed` event.
// Components call these, never `invoke` or `listen` directly.
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type ScreenId = string;
/** `stopped`: its stream ended or failed; it is being reopened from its saved permission. */
export type ScreenState = "live" | "needs_pick" | "opening" | "stopped";

export interface ScreenStatus {
  id: ScreenId;
  name: string;
  state: ScreenState;
  /** Seconds since its newest frame arrived; null before the first. */
  last_frame_age_s: number | null;
}

export interface ScreensView {
  screens: ScreenStatus[];
  /** Pairs of screens whose pictures have looked the same for 10 s. */
  duplicates: [ScreenId, ScreenId][];
}

export type CaptureMode = "idle" | "active" | "passive" | "stopped";
export type StopReason = "paused" | "quit";

export interface CaptureView {
  mode: CaptureMode;
  since: string | null;
  shots: number;
  next_check_at: string | null;
  stopped_by: StopReason | null;
}

export interface PauseView {
  paused: boolean;
  /** null while paused means until resumed. */
  until: string | null;
}

export type UploadState =
  | { state: "ok" }
  | { state: "offline" }
  | { state: "rejected"; device: string }
  | { state: "disabled" };

export type UploadStatus = UploadState & {
  pending: number;
  last_upload: string | null;
  /** Interactions the server refused for good (400, 409, 413, 422). */
  failed: number;
  /** The latest such refusal since the app started, e.g. "2026-11-07/17-00-00: too large for the server (413)". */
  last_failure: string | null;
};

export interface StatusView {
  capture: CaptureView;
  pause: PauseView;
  upload: UploadStatus;
  screens: number;
  screens_live: number;
  /** Names of the screens to pick again, or stopped. */
  screens_attention: string[];
  input: "ok" | "no_devices" | "permission_denied";
}

export interface TodayCounts {
  shots: number;
  interactions: number;
}

export interface DiskView {
  captures_bytes: number;
  free_bytes: number;
  /** Free space is under the warning level. */
  low: boolean;
}

/** One health check: the same list serves Setup, Status and `vitality-client doctor`. */
export interface Check {
  id: string;
  ok: boolean;
  /** Setup is shown while an essential check fails. */
  essential: boolean;
  title: string;
  detail: string;
  fix: string | null;
}

export interface DecisionLine {
  at: string;
  text: string;
}

export interface StatusPage {
  status: StatusView;
  today: TodayCounts;
  disk: DiskView;
  checks: Check[];
  /** The last 50 decisions, oldest first. */
  recent_decisions: DecisionLine[];
}

/** `client.toml`: the server, every number of the rules, image quality, disk warning, start at login. */
export interface Settings {
  server: string;
  webp_quality: number;
  start_gap_s: number;
  settle_s: number;
  max_gap_s: number;
  change_fraction: number;
  change_levels: number;
  passive_every_s: number;
  passive_quiet_checks: number;
  max_interaction_mib: number;
  disk_warning_gb: number;
  start_at_login: boolean;
}

export interface FieldError {
  field: keyof Settings | string;
  message: string;
}

/** `settings_save`'s reply: errors (nothing saved), or the saved settings; `login_error` if start at login could not change. */
export interface SaveReply {
  errors: FieldError[];
  settings: Settings;
  login_error: string | null;
}

/** One rule in plain words, written by the core with the current numbers. */
export interface RuleText {
  id: string;
  title: string;
  text: string;
}

export interface Freed {
  interactions: number;
  bytes: number;
}

export const core = {
  status: () => invoke<StatusView>("status"),
  statusPage: () => invoke<StatusPage>("status_page"),
  pause: (minutes: number | null) => invoke<StatusView>("pause", { minutes }),
  resume: () => invoke<StatusView>("resume"),
  screensList: () => invoke<ScreensView>("screens_list"),
  screensAdd: () => invoke<ScreensView>("screens_add"),
  screensRepick: (id: ScreenId) => invoke<ScreensView>("screens_repick", { id }),
  screensRename: (id: ScreenId, name: string) => invoke<ScreensView>("screens_rename", { id, name }),
  screensRemove: (id: ScreenId) => invoke<ScreensView>("screens_remove", { id }),
  settingsGet: () => invoke<Settings>("settings_get"),
  settingsSave: (settings: Settings) => invoke<SaveReply>("settings_save", { settings }),
  freeUpSpace: () => invoke<Freed>("free_up_space"),
  howItWorks: () => invoke<RuleText[]>("how_it_works"),
  /** The health checks as last run. */
  checks: () => invoke<Check[]>("checks"),
  /** Runs them now; with `server`, the server parts test that address (not saved). */
  checksRun: (server: string | null) => invoke<Check[]>("checks_run", { server }),
  /** A `data:image/jpeg` URL of the screen's newest frame, about 480 px wide, or null. */
  screenPreview: (id: ScreenId) => invoke<string | null>("screen_preview", { id }),
};

/** Calls `handler` whenever the core says its state changed; returns the function that stops it. */
export function onStateChanged(handler: () => void): Promise<UnlistenFn> {
  return listen("state-changed", () => handler());
}
