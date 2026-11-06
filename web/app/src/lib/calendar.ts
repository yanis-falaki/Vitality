// Calendar arithmetic on local days ("2026-11-03"), for the heatmap, the Day calendar, the grouped
// action lists and the graph's time ranges. Days are handled as dates at noon UTC, so adding days
// never trips over a change of daylight saving time; nothing here looks at the browser's clock.

const MS_DAY = 86_400_000;

const at = (d: string) => Date.parse(`${d}T12:00:00Z`);
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

/** `d` plus `n` days (n may be negative). */
export const addDays = (d: string, n: number) => iso(at(d) + n * MS_DAY);

/** Whole days from `a` to `b`. */
export const daysBetween = (a: string, b: string) => Math.round((at(b) - at(a)) / MS_DAY);

/** 0 for Monday … 6 for Sunday. */
export const weekday = (d: string) => (new Date(at(d)).getUTCDay() + 6) % 7;

/** The Monday of `d`'s week. */
export const weekStart = (d: string) => addDays(d, -weekday(d));

/** "2026-11" of a day. */
export const monthOf = (d: string) => d.slice(0, 7);

/** "November 2026" from "2026-11" (or a day). */
export function monthLabel(m: string): string {
  return new Date(`${m.slice(0, 7)}-15T12:00:00Z`).toLocaleDateString("en", { month: "long", year: "numeric", timeZone: "UTC" });
}

/** "Nov" from "2026-11" (or a day). */
export function monthShort(m: string): string {
  return new Date(`${m.slice(0, 7)}-15T12:00:00Z`).toLocaleDateString("en", { month: "short", timeZone: "UTC" });
}

/** "2026-12" after "2026-11" (n months later; n may be negative). */
export function addMonths(m: string, n: number): string {
  const [y, mo] = m.split("-").map(Number);
  const t = y * 12 + (mo - 1) + n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
}

/** The days of a month's calendar page: whole weeks from Monday, with null outside the month. */
export function monthGrid(m: string): (string | null)[][] {
  const first = `${m}-01`;
  const weeks: (string | null)[][] = [];
  for (let d = weekStart(first); monthOf(d) <= m || weekday(d) !== 0; d = addDays(d, 1)) {
    if (weekday(d) === 0) weeks.push([]);
    weeks[weeks.length - 1].push(monthOf(d) === m ? d : null);
    if (weeks.length > 6) break;
  }
  return weeks;
}

/** How many steps (1 … `steps`) of shading `n` out of `max` gets; 0 for none. */
export function shade(n: number, max: number, steps = 4): number {
  return n <= 0 || max <= 0 ? 0 : Math.max(1, Math.ceil((n / max) * steps));
}
