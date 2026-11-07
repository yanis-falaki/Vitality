// A fake core for screenshots and manual checks in a browser (`npm run dev`, then
// /mock.html?scenario=two|duplicate|needs-pick|stopped|empty|paused): answers the app's Tauri commands
// from the fixtures (fixtures.ts, written from the Rust types) and draws made-up previews.
// Never shipped: only mock.html loads it.
import { mockIPC } from "@tauri-apps/api/mocks";
import type { ScreensView, StatusView } from "../core";
import { statusActive, statusIdle, statusPage, statusPaused } from "../fixtures";

type Scenario = "two" | "duplicate" | "needs-pick" | "empty" | "paused" | "stopped" | "idle" | "offline" | "rejected";

/** The checks a scenario shows: only the failing ones matter to Status. */
function scenarioChecks(s: Scenario) {
  return s === "needs-pick" || s === "stopped" ? statusPage.checks : statusPage.checks.filter((c) => c.ok);
}

/** A made-up desktop: a window with a title bar and lines of "text", in the given hue. */
function fakeScreen(hue: number, seed: number): string {
  const c = document.createElement("canvas");
  c.width = 480;
  c.height = 270;
  const g = c.getContext("2d")!;
  g.fillStyle = `hsl(${hue} 30% 22%)`;
  g.fillRect(0, 0, 480, 270);
  g.fillStyle = "#f4f2ed";
  g.fillRect(40, 30, 400, 210);
  g.fillStyle = `hsl(${hue} 25% 85%)`;
  g.fillRect(40, 30, 400, 20);
  g.fillStyle = "#3a3835";
  for (let i = 0; i < 11; i++) {
    const w = 120 + ((seed * 37 + i * 53) % 230);
    g.fillRect(58, 64 + i * 15, w, 6);
  }
  g.fillStyle = "rgba(0,0,0,0.35)";
  g.fillRect(0, 254, 480, 16);
  return c.toDataURL("image/jpeg", 0.85);
}

function screensFor(s: Scenario): ScreensView {
  const live = (id: string, name: string, age = 0.4) => ({ id, name, state: "live" as const, last_frame_age_s: age });
  switch (s) {
    case "empty":
      return { screens: [], duplicates: [] };
    case "duplicate":
      return { screens: [live("s1", "Main"), live("s2", "Screen 2", 0.6)], duplicates: [["s1", "s2"]] };
    case "stopped":
      return { screens: [live("s1", "Main"), { id: "s2", name: "Side", state: "stopped", last_frame_age_s: null }], duplicates: [] };
    case "needs-pick":
      return {
        screens: [live("s1", "Main"), { id: "s2", name: "Side", state: "needs_pick", last_frame_age_s: null }, live("s3", "Laptop", 42)],
        duplicates: [],
      };
    default:
      return { screens: [live("s1", "Main"), live("s2", "Side", 0.9)], duplicates: [] };
  }
}

export function installFakeCore(scenario: Scenario) {
  let screens = screensFor(scenario);
  const attention = screens.screens.filter((s) => s.state === "needs_pick" || s.state === "stopped").map((s) => s.name);
  const base = { ...statusActive, screens: screens.screens.length, screens_live: screens.screens.length - attention.length, screens_attention: attention };
  const soon = (s: number) => new Date(Date.now() + s * 1000).toISOString();
  let status: StatusView =
    scenario === "paused"
      ? { ...statusPaused, pause: { paused: true, until: null } }
      : scenario === "idle"
        ? { ...base, capture: { ...statusIdle.capture, next_check_at: soon(31) } }
        : scenario === "offline"
          ? { ...base, capture: { ...statusIdle.capture, next_check_at: soon(12) }, upload: { ...statusIdle.upload, pending: 4 } }
          : scenario === "rejected"
            ? { ...base, upload: statusPaused.upload }
            : base;
  const previews: Record<string, string> = {
    s1: fakeScreen(200, 1),
    s2: scenario === "duplicate" ? fakeScreen(200, 1) : fakeScreen(30, 2),
    s3: fakeScreen(140, 3),
  };
  mockIPC(
    (cmd, args) => {
      const a = (args ?? {}) as Record<string, unknown>;
      switch (cmd) {
        case "status":
          return status;
        case "status_page":
          return { ...statusPage, status, checks: scenarioChecks(scenario) };
        case "pause":
          status = { ...status, pause: { paused: true, until: new Date(Date.now() + 1800e3).toISOString() } };
          return status;
        case "resume":
          status = { ...status, pause: { paused: false, until: null } };
          return status;
        case "screens_list":
          return screens;
        case "screens_rename":
          screens = { ...screens, screens: screens.screens.map((s) => (s.id === a.id ? { ...s, name: String(a.name) } : s)) };
          return screens;
        case "screens_remove":
          screens = { screens: screens.screens.filter((s) => s.id !== a.id), duplicates: [] };
          return screens;
        case "screens_add": {
          const id = `s${screens.screens.length + 1}`;
          previews[id] = fakeScreen(280, 4);
          screens = { ...screens, screens: [...screens.screens, { id, name: `Screen ${screens.screens.length + 1}`, state: "live", last_frame_age_s: 0.2 }] };
          return screens;
        }
        case "screens_repick":
          screens = { ...screens, screens: screens.screens.map((s) => (s.id === a.id ? { ...s, state: "live", last_frame_age_s: 0.3 } : s)) };
          return screens;
        case "screen_preview":
          return previews[String(a.id)] ?? null;
        default:
          throw new Error(`the fake core has no command ${cmd}`);
      }
    },
    { shouldMockEvents: true },
  );
}
