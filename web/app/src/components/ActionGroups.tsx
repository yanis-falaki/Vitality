// A list of actions that stays short however long the history: up to FLAT actions, by day as
// before; more, grouped by month and then by week, newest first, every group collapsed but the most
// recent (and the one holding the selection). Each view draws its own rows and day headings, so the
// Reader's timeline, the inspector's rows and the Cards' list keep their looks.
import { useState, type ReactNode } from "react";
import type { Action } from "../api/client";
import { addDays, monthLabel, monthOf, weekStart } from "../lib/calendar";
import { day, dayLabel, plural } from "../lib/format";
import { useSelection } from "../lib/selection";

/** Up to this many actions, a list is not grouped. */
export const FLAT = 20;

interface Props {
  actions: Action[];                 // by start
  row(a: Action): ReactNode;         // one action (with its key)
  dayHead(d: string): ReactNode;     // a day's heading (with its key)
  className: string;                 // the list's own container class
}

/** "3 actions", or for a long list "212 actions since Oct 21". */
export function actionsTitle(actions: Action[]): string {
  if (actions.length <= FLAT) return plural(actions.length, "action");
  const first = day(actions[0].start), last = day(actions[actions.length - 1].start);
  const since = new Date(`${first}T12:00:00Z`).toLocaleDateString("en", {
    month: "short", day: "numeric", timeZone: "UTC", ...(first.slice(0, 4) !== last.slice(0, 4) ? { year: "numeric" } : {}),
  });
  return `${plural(actions.length, "action")} since ${since}`;
}

export function ActionGroups({ actions, row, dayHead, className }: Props) {
  const { sel } = useSelection();
  const [toggled, setToggled] = useState<Map<string, boolean>>(new Map());
  if (actions.length <= FLAT) return <div className={className}>{byDay(actions, row, dayHead)}</div>;

  const months = groups(actions, (a) => monthOf(day(a.start)));
  const held = actions.find((a) => a.id === sel);
  const holds = (key: string) => !!held && (monthOf(day(held.start)) === key || weekStart(day(held.start)) === key);
  const open = (key: string, latest: boolean) => toggled.get(key) ?? (latest || holds(key));
  const toggle = (key: string, now: boolean) => setToggled((t) => new Map(t).set(key, !now));
  return (
    <div className={`${className} groups`}>
      {months.map(([month, inMonth], m) => {
        const monthOpen = open(month, m === 0);
        return (
          <section className="group group-month" key={month}>
            <GroupHead label={monthLabel(month)} count={inMonth.length} open={monthOpen} onToggle={() => toggle(month, monthOpen)} />
            {monthOpen && groups(inMonth, (a) => weekStart(day(a.start))).map(([week, inWeek], w) => {
              const weekOpen = open(week, m === 0 && w === 0);
              return (
                <section className="group group-week" key={week}>
                  <GroupHead label={weekLabel(week)} count={inWeek.length} open={weekOpen} onToggle={() => toggle(week, weekOpen)} />
                  {weekOpen && groups(inWeek, (a) => day(a.start)).map(([, inDay]) => byDay(inDay, row, dayHead))}
                </section>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

function GroupHead({ label, count, open, onToggle }: { label: string; count: number; open: boolean; onToggle(): void }) {
  return (
    <button type="button" className="group-head" aria-expanded={open} onClick={onToggle}>
      <span className="caret" aria-hidden>{open ? "▾" : "▸"}</span>
      <span className="group-label">{label}</span>
      <span className="group-count">{plural(count, "action")}</span>
    </button>
  );
}

// Rows under their day's heading, in time order.
function byDay(actions: Action[], row: Props["row"], dayHead: Props["dayHead"]): ReactNode[] {
  const out: ReactNode[] = [];
  let last = "";
  for (const a of actions) {
    const d = day(a.start);
    if (d !== last) out.push(dayHead(d));
    last = d;
    out.push(row(a));
  }
  return out;
}

// Consecutive runs of `actions` by `key`, newest run first (each run keeps its time order).
function groups(actions: Action[], key: (a: Action) => string): [string, Action[]][] {
  const out: [string, Action[]][] = [];
  for (const a of actions) {
    const k = key(a);
    if (out.length && out[out.length - 1][0] === k) out[out.length - 1][1].push(a);
    else out.push([k, [a]]);
  }
  return out.reverse();
}

// "Oct 19 – 25", or "Oct 26 – Nov 1" across a month's end.
function weekLabel(monday: string): string {
  const a = dayLabel(monday).replace(/^\w+, /, ""), sunday = addDays(monday, 6);
  const b = dayLabel(sunday).replace(/^\w+, /, "");
  return monday.slice(5, 7) === sunday.slice(5, 7) ? `${a} – ${b.split(" ")[1]}` : `${a} – ${b}`;
}
