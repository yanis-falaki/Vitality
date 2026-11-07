// Formatting for display: local clock times in the web app's style ("8:02 p.m."), ages, plurals.

/** "8:02 p.m." from an ISO time, in this machine's local time. */
export function clock(iso: string): string {
  const t = new Date(iso);
  const h = t.getHours();
  const m = String(t.getMinutes()).padStart(2, "0");
  return `${((h + 11) % 12) + 1}:${m} ${h < 12 ? "a.m." : "p.m."}`;
}

/** "1 shot" / "14 shots". */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "last frame 0 s ago", "last frame 3 min ago". */
export function frameAge(seconds: number): string {
  if (seconds < 90) return `last frame ${Math.round(seconds)} s ago`;
  if (seconds < 5400) return `last frame ${Math.round(seconds / 60)} min ago`;
  return `last frame ${Math.round(seconds / 3600)} h ago`;
}
