// An interaction's screenshots as the views show them. A client that captures several screens takes a
// shot of each (`screen`, numbered from 1, top-left first, and which one held the focused window).
import type { Shot } from "../api/client";

/** A few shots to stand for an interaction in a list: with several screens, each screen's latest (by
 * screen); with one, up to three spread over the interaction (the first, the middle, the last). */
export function thumbsOf(shots: Shot[]): Shot[] {
  if (shots.some((s) => s.screen != null)) {
    const latest = new Map<number, Shot>();
    for (const s of shots) if (s.screen != null) latest.set(s.screen, s);
    return [...latest.entries()].sort((a, b) => a[0] - b[0]).slice(0, 4).map(([, s]) => s);
  }
  if (shots.length <= 3) return shots;
  return [shots[0], shots[Math.floor((shots.length - 1) / 2)], shots[shots.length - 1]];
}

/** "Screen 2 · focused", or "" for a shot of the only screen. */
export function shotLabel(shot: Shot): string {
  if (shot.screen == null) return "";
  return `Screen ${shot.screen}${shot.focused ? " · focused" : ""}`;
}
