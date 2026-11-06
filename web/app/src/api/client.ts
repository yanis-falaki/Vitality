// The API, as typed queries. The types come from schema.ts, generated from the API's OpenAPI schema
// (`npm run api`; see README.md), so a change to the API shows up here as a type error.
import { useQueries, useQuery } from "@tanstack/react-query";
import type { components } from "./schema";

type S = components["schemas"];
export type ApiModel = S["Model"];
export type Striving = S["Striving"];
export type Activity = S["Activity"];
export type Action = S["Action"];
export type ActionDetail = S["ActionDetail"];
export type InteractionSummary = S["InteractionSummary"];
export type Interactions = Record<string, InteractionSummary>;
export type Evidence = S["Evidence"];
export type Modeler = S["Modeler"];
export type Session = S["Session"];

async function get<T>(path: string): Promise<T> {
  const reply = await fetch(path, { headers: { Accept: "application/json" } });
  if (!reply.ok) throw new Error(`${path}: ${reply.status}`);
  return (await reply.json()) as T;
}

/** The model (actions without their bodies); re-read every minute, since the modeler may commit at any
 * time. The server answers an unchanged model with a 304, so the browser keeps the copy it has. */
export function useApiModel() {
  return useQuery({ queryKey: ["model"], queryFn: () => get<ApiModel>("/api/model"), refetchInterval: 60_000 });
}

/** One action with its body and operation references (null when unknown). */
export function useAction(id: string | null) {
  return useQuery({
    queryKey: ["action", id],
    enabled: id !== null,
    staleTime: 60_000,
    queryFn: async (): Promise<ActionDetail | null> => {
      const reply = await fetch(`/api/actions/${id}`);
      if (reply.status === 404) return null;
      if (!reply.ok) throw new Error(`action: ${reply.status}`);
      return (await reply.json()) as ActionDetail;
    },
  });
}

/** The summaries of the cited interactions that started from day `from` (to the last, or `to`). */
export function useInteractions(from: string | null, to: string | null, enabled = true) {
  const query = new URLSearchParams();
  if (from) query.set("from", from);
  if (to) query.set("to", to);
  return useQuery({
    queryKey: ["interactions", from, to],
    enabled,
    staleTime: 60_000,
    queryFn: () => get<Interactions>(`/api/interactions?${query}`),
  });
}

const evidenceQuery = (interaction: string | null) => ({
  queryKey: ["evidence", interaction],
  enabled: interaction !== null,
  staleTime: 5 * 60_000,
  queryFn: async (): Promise<Evidence | null> => {
    const reply = await fetch(`/api/evidence/${interaction}`);
    if (reply.status === 404) return null;
    if (!reply.ok) throw new Error(`evidence: ${reply.status}`);
    return (await reply.json()) as Evidence;
  },
});

/** One interaction's evidence (observation, operations, screenshots), or null when unknown. */
export function useEvidence(interaction: string | null) {
  return useQuery(evidenceQuery(interaction));
}

/** The evidence of several interactions, in their order (an entry is undefined while it loads). */
export function useEvidenceOf(interactions: string[]): (Evidence | null | undefined)[] {
  return useQueries({ queries: interactions.map(evidenceQuery) }).map((q) => q.data);
}

/** The modeler's state, polled every 30 seconds. */
export function useModeler() {
  return useQuery({ queryKey: ["modeler"], queryFn: () => get<Modeler>("/api/modeler"), refetchInterval: 30_000 });
}

/** Where a screenshot is served. */
export const shotUrl = (interaction: string, file: string) => `/captures/${interaction}/${file}`;
