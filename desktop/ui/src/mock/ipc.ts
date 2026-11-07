// A fake core for screenshots and manual checks in a browser (`npm run dev`, then
// /mock.html?scenario=two|duplicate|needs-pick|stopped|empty|paused|idle|offline|rejected|setup|
// setup-done|health-fail|screen-not-live|loading, and &page=…, &step=…, &setup=1): answers the app's Tauri commands
// from the fixtures (fixtures.ts, written from the Rust types) and draws made-up previews.
// Never shipped: only mock.html loads it.
import { mockIPC } from "@tauri-apps/api/mocks";
import type { ScreensView, StatusView } from "../core";
import { freed, rules, settings as defaultSettings, statusActive, statusIdle, statusPage, statusPaused } from "../fixtures";
import type { Check, Settings } from "../core";

type Scenario = "two" | "duplicate" | "needs-pick" | "empty" | "paused" | "stopped" | "idle" | "offline" | "rejected" | "setup" | "setup-done" | "health-fail" | "screen-not-live" | "loading";

const ck = (id: string, ok: boolean, essential: boolean, title: string, detail: string, fix: string | null = null): Check => ({ id, ok, essential, title, detail, fix: ok ? null : fix });

/** The full list of checks for a scenario (Setup and Status read them). */
function allChecks(s: Scenario, screens: number): Check[] {
  const input = s === "setup" || s === "health-fail"
    ? ck("input_access", false, true, "Keyboard and mouse", "not allowed to read /dev/input: you are not in the input group", "Run `sudo usermod -aG input $USER`, then log out and in again.")
    : ck("input_access", true, true, "Keyboard and mouse", "input devices are readable");
  const accepted = s === "setup"
    ? ck("device_accepted", false, false, "This device accepted", "the server does not accept \"desktop\"", "On the server, add \"desktop\" to allowed_clients.")
    : ck("device_accepted", true, false, "This device accepted", "the server accepts \"desktop\"");
  return [
    input,
    ck("desktop_session", true, true, "Desktop session", "wayland session (WAYLAND_DISPLAY=wayland-1)"),
    ck("portal_available", true, true, "Screen-share portal", "ScreenCast portal, version 5"),
    screens > 0 ? ck("screens_open", true, true, "Screens", screens === 1 ? "1 screen, live" : `${screens} screens, all live`) : ck("screens_open", false, true, "Screens", "no screen set up", "Open Screens and add a screen."),
    ck("tailscale", true, false, "Tailscale", "running as \"desktop\""),
    ck("server_health", true, false, "Server answers", "http://vitality-librarian:8100 is up"),
    accepted,
    ck("disk_space", true, false, "Disk space", "96.0 GB free"),
    s === "setup" ? ck("start_at_login", false, false, "Start at login", "the service is off but the setting says on", "Save Settings again to apply it.") : ck("start_at_login", true, false, "Start at login", "on"),
  ];
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
    case "setup":
      return { screens: [], duplicates: [] };
    case "setup-done":
      return { screens: [live("s1", "Main")], duplicates: [] };
    case "duplicate":
      return { screens: [live("s1", "Main"), live("s2", "Screen 2", 0.6)], duplicates: [["s1", "s2"]] };
    case "stopped":
      return { screens: [live("s1", "Main"), { id: "s2", name: "Side", state: "stopped", last_frame_age_s: null }], duplicates: [] };
    case "screen-not-live":
      return { screens: [live("s1", "Main"), { id: "s2", name: "Side", state: "needs_pick", last_frame_age_s: null }], duplicates: [] };
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
  let settings: Settings = { ...defaultSettings };
  const problems = new URLSearchParams(location.search).has("problems") ? ["change_levels = 300: must be a whole number from 1 to 254 (using 16)"] : [];
  const live = () => screens.screens.filter((x) => x.state === "live").length;
  const checksNow = () => {
    const c = allChecks(scenario, live());
    // A configured screen that is not live: a warning, not essential.
    return screens.screens.length > 0 && live() < screens.screens.length
      ? c.map((x) => (x.id === "screens_open" ? { ...x, ok: false, essential: false, detail: "Side is not live", fix: "Open Screens and pick Side again." } : x))
      : c;
  };
  const attention = screens.screens.filter((s) => s.state === "needs_pick" || s.state === "stopped").map((s) => s.name);
  const base = {
    ...statusActive,
    screens: screens.screens.length,
    screens_live: screens.screens.length - attention.length,
    screens_attention: attention,
    health_warnings: attention.map((n) => `Screens: ${n} is not live`),
    setup_done: scenario !== "setup" && scenario !== "empty",
  };
  const soon = (s: number) => new Date(Date.now() + s * 1000).toISOString();
  let status: StatusView =
    scenario === "paused"
      ? { ...statusPaused, setup_done: true, pause: { paused: true, until: null } }
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
          return { ...statusPage, status, checks: checksNow() };
        case "checks":
          return scenario === "loading" ? null : checksNow();
        case "checks_run":
        case "server_test":
          return checksNow();
        case "setup_finish":
          status = { ...status, setup_done: true };
          return status;
        case "settings_get":
          return { settings, problems };
        case "settings_save": {
          const next = a.settings as Settings;
          const errors = [];
          if (!(next.end_after_idle_s > 0)) errors.push({ field: "end_after_idle_s", message: "must be more than 0 s and at most a day" });
          if (!(next.change_levels >= 1 && next.change_levels <= 254)) errors.push({ field: "change_levels", message: "must be a whole number from 1 to 254" });
          if (errors.length) return { errors, settings, login_error: null };
          settings = next;
          (window as unknown as { savedSettings: Settings }).savedSettings = settings;
          return { errors: [], settings, login_error: null };
        }
        case "free_up_space":
          return freed;
        case "how_it_works":
          return rules;
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
