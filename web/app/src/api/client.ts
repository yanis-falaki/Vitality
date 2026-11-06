// The API, as typed queries. The types come from schema.ts, generated from the API's OpenAPI schema
// (`npm run api`; see README.md), so a change to the API shows up here as a type error.
import { useQuery } from "@tanstack/react-query";
import type { components } from "./schema";

type S = components["schemas"];
export type ApiModel = S["Model"];
export type Striving = S["Striving"];
export type Activity = S["Activity"];
export type Action = S["Action"];
export type InteractionSummary = S["InteractionSummary"];
export type Evidence = S["Evidence"];
export type Modeler = S["Modeler"];
export type Session = S["Session"];

async function get<T>(path: string): Promise<T> {
  const reply = await fetch(path, { headers: { Accept: "application/json" } });
  if (!reply.ok) throw new Error(`${path}: ${reply.status}`);
  return (await reply.json()) as T;
}

/** The whole model; re-read every minute, since the modeler may commit at any time. */
export function useApiModel() {
  return useQuery({ queryKey: ["model"], queryFn: () => get<ApiModel>("/api/model"), refetchInterval: 60_000 });
}

/** One interaction's evidence (observation, operations, screenshots), or null when unknown. */
export function useEvidence(interaction: string | null) {
  return useQuery({
    queryKey: ["evidence", interaction],
    enabled: interaction !== null,
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const reply = await fetch(`/api/evidence/${interaction}`);
      if (reply.status === 404) return null;
      if (!reply.ok) throw new Error(`evidence: ${reply.status}`);
      return (await reply.json()) as Evidence;
    },
  });
}

/** The modeler's state, polled every 30 seconds. */
export function useModeler() {
  return useQuery({ queryKey: ["modeler"], queryFn: () => get<Modeler>("/api/modeler"), refetchInterval: 30_000 });
}

/** Where a screenshot is served. */
export const shotUrl = (interaction: string, file: string) => `/captures/${interaction}/${file}`;
