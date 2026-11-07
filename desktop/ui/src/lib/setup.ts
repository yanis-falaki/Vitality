// When Setup is shown instead of the pages (spec §8, Review Focus 5): no screen set up, or an
// essential health check failing. Until the checks have come, nothing is decided.
import type { Check, StatusView } from "../core";

export function needsSetup(status: StatusView | undefined, checks: Check[] | undefined): boolean | undefined {
  if (!status || !checks) return undefined;
  return status.screens === 0 || checks.some((c) => c.essential && !c.ok);
}

export const check = (checks: Check[] | undefined, id: string) => checks?.find((c) => c.id === id);
