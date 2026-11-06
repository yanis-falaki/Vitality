// The model as the views use it: the API's lists, indexed by id and by link.
//
// `indexModel(api)` is built once per fetch; `useModel()` returns it (or the query's loading/error
// state). Items are looked up by id with `kind`, `title` and the typed getters. An interaction is
// known by its name (`laptop/2026-11-03/14-22-05`); its evidence is fetched separately.
import { useMemo } from "react";
import { useApiModel, type Action, type Activity, type ApiModel, type InteractionSummary, type Striving } from "../api/client";
import { day } from "./format";

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
  interaction(name: string): InteractionSummary | undefined;
  activitiesOf(striving: string): Activity[];      // activities whose part_of names it
  actionsOf(activity: string): Action[];           // by start
  interactionsOf(action: Action): string[];        // the interactions it cites, in order
  actionsCiting(interaction: string): Action[];
  unassigned: Activity[];                          // serving no striving yet
  days: string[];                                  // local days with actions, ascending
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
  Object.keys(api.interactions).forEach((i) => kinds.set(i, "interaction"));

  const activitiesOf = new Map<string, Activity[]>();
  for (const a of api.activities) for (const s of a.part_of) push(activitiesOf, s, a);
  const actionsOf = new Map<string, Action[]>();
  const citing = new Map<string, Action[]>();
  const interactionsOf = new Map<string, string[]>();
  for (const a of api.actions) {
    for (const act of a.part_of) push(actionsOf, act, a);
    const names = [...new Set(a.operations.map((r) => r.split("#")[0]))];
    interactionsOf.set(a.id, names);
    for (const n of names) push(citing, n, a);
  }
  // A dated reference names an interaction without its device, or an action.
  const byTime = new Map<string, string>();
  for (const name of Object.keys(api.interactions)) byTime.set(name.slice(name.indexOf("/") + 1), name);

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
      if (k === "interaction") return api.interactions[id].first_operation;
      return id;
    },
    striving: (id) => strivings.get(id),
    activity: (id) => activities.get(id),
    action: (id) => actions.get(id),
    interaction: (name) => api.interactions[name],
    activitiesOf: (s) => activitiesOf.get(s) ?? [],
    actionsOf: (a) => actionsOf.get(a) ?? [],
    interactionsOf: (a) => interactionsOf.get(a.id) ?? [],
    actionsCiting: (i) => citing.get(i) ?? [],
    unassigned: api.activities.filter((a) => a.part_of.length === 0),
    days: [...new Set(api.actions.map((a) => day(a.start)))].sort(),
    resolveRef: (ref) => byTime.get(ref) ?? (actions.has(ref) ? ref : undefined),
  };
  return model;
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
