// When Setup is shown instead of the pages (spec §8, Review Focus 5): on the first run (Setup
// never finished), and afterwards while an essential check fails. The checks run only after the
// saved screens have reopened, so until they come (null) nothing is decided: the startup state
// never opens Setup. A configured screen that is not live is a warning, not essential.
import type { Check, StatusView } from "../core";

export function needsSetup(status: StatusView | undefined, checks: Check[] | null | undefined): boolean | undefined {
  if (!status || !checks) return undefined;
  return !status.setup_done || checks.some((c) => c.essential && !c.ok);
}

export const check = (checks: Check[] | null | undefined, id: string) => checks?.find((c) => c.id === id);

/** The step to open Setup at: the first one whose essential check fails (Welcome on a first run). */
export function firstFailingStep(status: StatusView | undefined, checks: Check[] | null | undefined): number {
  if (!status?.setup_done || !checks) return 0;
  const failing = (id: string) => checks.some((c) => c.id === id && c.essential && !c.ok);
  if (failing("input_access") || failing("desktop_session") || failing("portal_available")) return 1;
  if (failing("screens_open")) return 3;
  return 0;
}
