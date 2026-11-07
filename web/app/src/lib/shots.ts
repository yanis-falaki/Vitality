// An interaction's screenshots as the views show them. With several screens the desktop app takes a
// shot of each at every moment, named as the owner named the screen (`screen_name`); a passive shot
// was taken while no one used the device.
import type { Shot } from "../api/client";

/** A few shots to stand for an interaction in a list: with several screens, each screen's latest (in
 * the order the screens first appear); with one, up to three spread over the interaction (the first,
 * the middle, the last). */
export function thumbsOf(shots: Shot[]): Shot[] {
  if (shots.some((s) => s.screen_name != null)) {
    const latest = new Map<string, Shot>();
    for (const s of shots) if (s.screen_name != null) latest.set(s.screen_name, s);
    return [...latest.values()].slice(0, 4);
  }
  if (shots.length <= 3) return shots;
  return [shots[0], shots[Math.floor((shots.length - 1) / 2)], shots[shots.length - 1]];
}

/** "Left monitor", "Left monitor · passive", "passive", or "" for an ordinary shot of the only screen. */
export function shotLabel(shot: Shot): string {
  return [shot.screen_name, shot.passive ? "passive" : null].filter(Boolean).join(" · ");
}

/** Whether every shot of an interaction was passive (taken while no one used the device). */
export function allPassive(shots: Shot[]): boolean {
  return shots.length > 0 && shots.every((s) => s.passive);
}
