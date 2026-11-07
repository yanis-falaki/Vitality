// The model as the views use it: the API's lists, indexed by id and by link.
//
// `indexModel(api)` is built once per fetch; `useModel()` returns it (or the query's loading/error
// state). Items are looked up by id with `kind`, `title` and the typed getters. An interaction is
// known by its name (`laptop/2026-11-03/14-22-05`), from the actions that cite it; its summary
// (`useInteractions`) and evidence (`useEvidence`) are fetched where a view needs them, and an
// action's body and operations with `useAction`.
import { useMemo } from "react";
import { useApiModel, type Action, type Activity, type ApiModel, type Striving } from "../api/client";
import { addDays } from "./calendar";
import { day } from "./format";

/** Whether `id` names an interaction (`laptop/2026-11-03/14-22-05`), cited by an action or not. */
export const isInteraction = (id: string) => /^[A-Za-z0-9][A-Za-z0-9._-]*\/\d{4}-\d\d-\d\d\/\d\d-\d\d-\d\d(-\d+)?$/.test(id);

/** Activities not seen for longer than this (before the model's last day) are "older". */
export const STALE_DAYS = 30;

export type Kind = "striving" | "activity" | "action" | "interaction";

export const LEVEL: Record<Kind, { label: string; plural: string }> = {
  striving: { label: "Striving", plural: "strivings" },
  activity: { label: "Activity", plural: "activities" },
  action: { label: "Action", plural: "actions" },
  interaction: { label: "Evidence", plural: "interactions" },
};

export interface Model {
  api: ApiModel;
  strivings: Striving[];
  activities: Activity[];
  actions: Action[];                               // by start
  kind(id: string): Kind | undefined;
  title(id: string): string;
  striving(id: string): Striving | undefined;
  activity(id: string): Activity | undefined;
  action(id: string): Action | undefined;
  /** When an interaction started, as local time: its name's UTC time at the offset of an action citing it. */
  interactionStart(name: string): string | undefined;
  activitiesOf(striving: string): Activity[];      // activities whose part_of names it
  actionsOf(activity: string): Action[];           // by start
  interactionsOf(action: Action): string[];        // the interactions it cites, in order
  actionsCiting(interaction: string): Action[];
  actionsOn(day: string): Action[];                // by start
  unassigned: Activity[];                          // serving no striving yet
  days: string[];                                  // local days with actions, ascending
  lastDay: string | undefined;                     // the model's latest day with actions
  /** Whether an activity was last seen more than STALE_DAYS before the model's last day. */
  stale(a: Activity): boolean;
  /** The interaction or action a dated reference (`2026-11-03/14-22-05`) in a body points at. */
  resolveRef(ref: string): string | undefined;
}

export function indexModel(api: ApiModel): Model {
  const kinds = new Map<string, Kind>();
  const strivings = new Map(api.strivings.map((s) => [s.id, s]));
  const activities = new Map(api.activities.map((a) => [a.id, a]));
  const actions = new Map(api.actions.map((a) => [a.id, a]));
  api.strivings.forEach((s) => kinds.set(s.id, "striving"));
  api.activities.forEach((a) => kinds.set(a.id, "activity"));
  api.actions.forEach((a) => kinds.set(a.id, "action"));

  const activitiesOf = new Map<string, Activity[]>();
  for (const a of api.activities) for (const s of a.part_of) push(activitiesOf, s, a);
  const actionsOf = new Map<string, Action[]>();
  const citing = new Map<string, Action[]>();
  const byDay = new Map<string, Action[]>();
  for (const a of api.actions) {
    for (const act of a.part_of) push(actionsOf, act, a);
    for (const n of a.interactions) {
      push(citing, n, a);
      if (!kinds.has(n)) kinds.set(n, "interaction");
    }
    push(byDay, day(a.start), a);
  }
  // A dated reference names an interaction without its device, or an action.
  const byTime = new Map<string, string>();
  for (const name of citing.keys()) byTime.set(name.slice(name.indexOf("/") + 1), name);
  const days = [...byDay.keys()].sort();
  const lastDay = days[days.length - 1];
  const staleBefore = lastDay ? addDays(lastDay, -STALE_DAYS) : "";

  const model: Model = {
    api,
    strivings: api.strivings,
    activities: api.activities,
    actions: api.actions,
    kind: (id) => kinds.get(id),
    title(id) {
      const k = kinds.get(id);
      if (k === "striving") return strivings.get(id)!.title;
      if (k === "activity") return activities.get(id)!.title;
      if (k === "action") return actions.get(id)!.summary;
      if (k === "interaction") {
        const start = model.interactionStart(id);
        return start ? `Evidence · ${start.slice(0, 10)} ${start.slice(11, 16)}` : id;
      }
      return id;
    },
    striving: (id) => strivings.get(id),
    activity: (id) => activities.get(id),
    action: (id) => actions.get(id),
    interactionStart(name) {
      const by = citing.get(name)?.[0];
      return by ? localTime(name, by.start) : undefined;
    },
    activitiesOf: (s) => activitiesOf.get(s) ?? [],
    actionsOf: (a) => actionsOf.get(a) ?? [],
    interactionsOf: (a) => a.interactions,
    actionsCiting: (i) => citing.get(i) ?? [],
    actionsOn: (d) => byDay.get(d) ?? [],
    unassigned: api.activities.filter((a) => a.part_of.length === 0),
    days,
    lastDay,
    stale: (a) => day(a.last_seen) < staleBefore,
    resolveRef: (ref) => byTime.get(ref) ?? (actions.has(ref) ? ref : undefined),
  };
  return model;
}

// An interaction's name holds its start in UTC (`laptop/2026-11-03/14-22-05`); shown at the offset of
// `like` (a local time of the same day, e.g. an action citing it) as that wall clock would read it.
function localTime(name: string, like: string): string | undefined {
  const m = name.match(/(\d{4}-\d\d-\d\d)\/(\d\d)-(\d\d)-(\d\d)/);
  const offset = like.match(/([+-])(\d\d):(\d\d)$/);
  if (!m || !offset) return undefined;
  const sign = offset[1] === "+" ? 1 : -1;
  const ms = Date.parse(`${m[1]}T${m[2]}:${m[3]}:${m[4]}Z`) + sign * (Number(offset[2]) * 60 + Number(offset[3])) * 60_000;
  return `${new Date(ms).toISOString().slice(0, 19)}${like.slice(-6)}`;
}

/** Activities most recently seen first: those seen within STALE_DAYS, and the older ones. */
export function byRecency(model: Model, activities: Activity[]): { recent: Activity[]; older: Activity[] } {
  const sorted = [...activities].sort((a, b) => (a.last_seen < b.last_seen ? 1 : a.last_seen > b.last_seen ? -1 : 0));
  return { recent: sorted.filter((a) => !model.stale(a)), older: sorted.filter((a) => model.stale(a)) };
}

/** The indexed model, with the query's state. */
export function useModel(): { model: Model | undefined; error: unknown; loading: boolean } {
  const query = useApiModel();
  const model = useMemo(() => (query.data ? indexModel(query.data) : undefined), [query.data]);
  return { model, error: query.error, loading: query.isLoading };
}

function push<K, V>(map: Map<K, V[]>, key: K, value: V) {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}
