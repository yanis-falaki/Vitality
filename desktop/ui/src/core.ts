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
export type StopReason = "paused" | "locked" | "sleeping" | "quit";

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

export const core = {
  status: () => invoke<StatusView>("status"),
  pause: (minutes: number | null) => invoke<StatusView>("pause", { minutes }),
  resume: () => invoke<StatusView>("resume"),
  screensList: () => invoke<ScreensView>("screens_list"),
  screensAdd: () => invoke<ScreensView>("screens_add"),
  screensRepick: (id: ScreenId) => invoke<ScreensView>("screens_repick", { id }),
  screensRename: (id: ScreenId, name: string) => invoke<ScreensView>("screens_rename", { id, name }),
  screensRemove: (id: ScreenId) => invoke<ScreensView>("screens_remove", { id }),
  /** A `data:image/jpeg` URL of the screen's newest frame, about 480 px wide, or null. */
  screenPreview: (id: ScreenId) => invoke<string | null>("screen_preview", { id }),
};

/** Calls `handler` whenever the core says its state changed; returns the function that stops it. */
export function onStateChanged(handler: () => void): Promise<UnlistenFn> {
  return listen("state-changed", () => handler());
}
