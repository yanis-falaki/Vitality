// What capture is doing, in the terms of the rules (spec §6–7), from a `StatusView`:
// "Active interaction since 8:02 p.m. · 14 shots", "Idle · passive check in 31 s",
// "Paused until 9:00 p.m.". Shared by the sidebar and the Status page.
import type { StatusView } from "../core";
import { clock, plural, until } from "./format";

export interface CaptureLine {
  text: string;
  /** What it means, in a few words. */
  detail: string;
  tone: "ok" | "busy" | "off" | "warn";
}

export function captureLine(s: StatusView, now: number): CaptureLine {
  if (s.pause.paused)
    return {
      text: s.pause.until ? `Paused until ${clock(s.pause.until)}` : "Paused until you resume",
      detail: "Nothing is captured and no checks run (R5).",
      tone: "warn",
    };
  const c = s.capture;
  switch (c.mode) {
    case "active":
      return {
        text: `Active interaction since ${clock(c.since!)} · ${plural(c.shots, "shot")}`,
        detail: "You are using the keyboard or mouse: shots of every screen regularly while you keep going (R2), and a closing one when you stop (R3).",
        tone: "busy",
      };
    case "passive":
      return {
        text: `Passive interaction since ${clock(c.since!)} · ${plural(c.shots, "shot")}`,
        detail: "No input, but a screen keeps changing (a video, a call): a shot at each check that sees a change.",
        tone: "busy",
      };
    case "stopped":
      return { text: "Stopping", detail: "Vitality is quitting.", tone: "off" };
    default:
      return {
        text: c.next_check_at ? `Idle · passive check ${until(c.next_check_at, now)}` : "Idle",
        detail: "Waiting for keyboard or mouse input; while idle, the screens are checked for changes.",
        tone: "ok",
      };
  }
}
