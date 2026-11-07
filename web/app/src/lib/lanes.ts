// A day's actions as Day draws them (and Home's "Today so far"): sittings, and a colour per activity.
// Day's Evidence mode groups the day's interactions into sittings by the same rule.
import type { Action } from "../api/client";
import type { Model } from "./model";

/** Minutes without an action that end a sitting. */
export const GAP = 30;
/** The lane of actions with no activity yet. */
export const NONE = "";
const LANE_COLOURS = 8;

/** A sitting: its span, and its actions (or, in Day's Evidence mode, its interactions). */
export interface Sitting<T extends Span = Action> { start: string; end: string; actions: T[] }
interface Span { start: string; end: string }

/** The actions (or interactions; by start) as sittings: a gap of more than GAP minutes starts a new one. */
export function sittings<T extends Span = Action>(actions: T[]): Sitting<T>[] {
  const out: Sitting<T>[] = [];
  for (const a of actions) {
    const last = out[out.length - 1];
    if (last && (Date.parse(a.start) - Date.parse(last.end)) / 60000 <= GAP) {
      last.actions.push(a);
      if (Date.parse(a.end) > Date.parse(last.end)) last.end = a.end;
    } else out.push({ start: a.start, end: a.end, actions: [a] });
  }
  return out;
}

/** The lanes of a day: its activities in order of first appearance, "no activity yet" last. */
export function lanesOf(actions: Action[]): string[] {
  return [...new Set(actions.map((a) => a.part_of[0] ?? NONE))].sort((a, b) => (a === NONE ? 1 : b === NONE ? -1 : 0));
}

/** The colours of a day's lanes (`--lane-1` … `--lane-8`; none: grey). Each activity keeps the colour of
 * its place in the model, so it is the same on most days; when two of the day's activities would share
 * one (the model has more activities than colours), the later takes the next colour free that day. */
export function lanePalette(model: Model, lanes: string[]): Map<string, string> {
  const out = new Map<string, string>([[NONE, "var(--lane-none)"]]);
  const taken = new Set<number>();
  for (const id of lanes.filter((l) => l !== NONE)) {
    const i = Math.max(0, model.activities.findIndex((a) => a.id === id));
    let c = i % LANE_COLOURS;
    for (let k = 0; k < LANE_COLOURS && taken.has(c); k++) c = (c + 1) % LANE_COLOURS;
    taken.add(c);
    out.set(id, `var(--lane-${c + 1})`);
  }
  return out;
}
