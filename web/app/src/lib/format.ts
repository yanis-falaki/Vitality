// Formatting times and text for display. Times in the files are local with their offset, so the
// text before the offset is the owner's wall clock: it is shown as written, never converted.

/** "2026-11-03" from an ISO time. */
export const day = (iso: string) => iso.slice(0, 10);

/** "9:04 a.m." from an ISO time. */
export function clock(iso: string): string {
  const h = Number(iso.slice(11, 13));
  const m = iso.slice(14, 16);
  return `${((h + 11) % 12) + 1}:${m} ${h < 12 ? "a.m." : "p.m."}`;
}

/** "Tue, Nov 3" from a day. */
export function dayLabel(d: string): string {
  return new Date(`${d}T12:00:00`).toLocaleDateString("en", { weekday: "short", month: "short", day: "numeric" });
}

/** "Tuesday, November 3" from a day. */
export function dayLong(d: string): string {
  return new Date(`${d}T12:00:00`).toLocaleDateString("en", { weekday: "long", month: "long", day: "numeric" });
}

/** Whole minutes from `a` to `b` (at least 1). */
export const minutes = (a: string, b: string) =>
  Math.max(1, Math.round((Date.parse(b) - Date.parse(a)) / 60000));

/** Hours from minutes, to one decimal: "1.4 h". */
export const hours = (mins: number) => `${Math.round(mins / 6) / 10} h`;

/** The part of the day an ISO time falls in. */
export function partOfDay(iso: string): string {
  const h = Number(iso.slice(11, 13));
  return h < 5 ? "Night" : h < 12 ? "Morning" : h < 17 ? "Afternoon" : h < 21 ? "Evening" : "Late evening";
}

/** `text` cut to `n` characters, with an ellipsis. */
export const short = (text: string, n: number) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

/** "1 action" / "3 actions". */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
