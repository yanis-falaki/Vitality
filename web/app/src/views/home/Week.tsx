// "Where your time went this week": per striving, and per activity within it (folded until opened), the
// time of its actions in the last 7 days as a solid bar, the 7 days before as a thin ghost bar beneath,
// with the hours and the difference. Activities serving no striving are grouped under "Not yet serving a
// striving". Time is the actions' spans: an action serving two activities counts for each (and once for
// a striving both serve). Without actions in the last 7 days, the 7 days up to the last with evidence.
import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import type { Action } from "../../api/client";
import { addDays } from "../../lib/calendar";
import { dayLabel, minutes } from "../../lib/format";
import type { Model } from "../../lib/model";
import type { Now } from "../../lib/now";

const UNSERVED = "";     // the group of activities serving no striving
const NO_ACTIVITY = "";  // actions with no activity yet, inside that group

interface Row { id: string; now: number; before: number }          // minutes in this week and the week before
interface Group extends Row { activities: Row[] }

export function Week({ model, now }: { model: Model; now: Now }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const started = (a: Action) => Date.parse(a.start) <= now.ms;
  const recent = model.days.filter((d) => d <= now.today && d > addDays(now.today, -7));
  const last = [...model.days].reverse().find((d) => d <= now.today);
  const end = recent.length || !last ? now.today : last;
  const fallback = end !== now.today;
  const groups = weigh(model, end, started);
  const max = Math.max(1, ...groups.flatMap((g) => [g.now, g.before]));
  const toggle = (id: string) => setOpen((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  return (
    <section className="home-section" aria-labelledby="home-week">
      <div className="home-section-head">
        <h2 className="eyebrow" id="home-week">{fallback ? "Where your time went" : "Where your time went this week"}</h2>
        <span className="home-key" aria-hidden>
          <i className="home-key-now" /> {fallback ? "those 7 days" : "last 7 days"} <i className="home-key-before" /> the 7 before
        </span>
      </div>
      {fallback && (
        <p className="home-quiet">
          Nothing in the last 7 days; the last 7 days with evidence ({dayLabel(addDays(end, -6))} – {dayLabel(end)}).
        </p>
      )}
      {groups.length === 0 && <p className="home-quiet">No actions yet.</p>}
      <div className="home-week" data-testid="home-week">
        {groups.map((g) => {
          const unfolded = open.has(g.id);
          return (
            <div key={g.id || "none"} className="home-week-group">
              <button type="button" className="home-week-row home-week-striving" aria-expanded={unfolded} onClick={() => toggle(g.id)}>
                <span className="home-week-name">
                  <span className="caret" aria-hidden>{unfolded ? "▾" : "▸"}</span>
                  <span className={g.id ? "home-week-title" : "home-week-title muted-title"}>
                    {g.id ? model.title(g.id) : "Not yet serving a striving"}
                  </span>
                </span>
                <Bars row={g} max={max} level={g.id ? "striving" : "none"} />
              </button>
              {unfolded && g.activities.map((a) => (
                <div key={a.id || "none"} className="home-week-row home-week-activity">
                  <span className="home-week-name">
                    {a.id
                      ? <Link to={`/reader?page=${encodeURIComponent(a.id)}`} className="home-week-title">{model.title(a.id)}</Link>
                      : <span className="home-week-title muted-title">No activity yet</span>}
                  </span>
                  <Bars row={a} max={max} level="activity" />
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {groups.length > 0 && <p className="home-note">Time from the spans of actions; an action serving two activities counts for each.</p>}
    </section>
  );
}

/** This week's bar, last week's ghost beneath, the hours and the difference. */
function Bars({ row, max, level }: { row: Row; max: number; level: "striving" | "activity" | "none" }) {
  const delta = row.now - row.before;
  return (
    <span className="home-week-bars">
      <span className="home-week-track" aria-hidden>
        <i className={`home-week-now level-${level}`} style={{ "--w": `${(row.now / max) * 100}%` } as CSSProperties} />
        <i className="home-week-before" style={{ "--w": `${(row.before / max) * 100}%` } as CSSProperties} />
      </span>
      <span className="home-week-hours" title={`${h(row.now)} in the last 7 days, ${h(row.before)} the 7 days before`}>
        {h(row.now)}
        <span className="home-week-delta">
          {row.before === 0 ? "" : Math.abs(delta) < 6 ? "±0" : `${delta > 0 ? "+" : "−"}${h(Math.abs(delta)).replace(" h", "")}`}
        </span>
      </span>
    </span>
  );
}

// Minutes as hours, to one decimal ("0.4 h"); under six minutes, "0 h".
const h = (mins: number) => `${Math.round(mins / 6) / 10} h`;

/** Each striving's (and the unserved group's) minutes in the 7 days to `end` and the 7 before, with its activities. */
function weigh(model: Model, end: string, started: (a: Action) => boolean): Group[] {
  const from = addDays(end, -6), before = addDays(end, -13);
  const groups = new Map<string, Group>();
  const group = (id: string) => {
    let g = groups.get(id);
    if (!g) groups.set(id, (g = { id, now: 0, before: 0, activities: [] }));
    return g;
  };
  const row = (g: Group, id: string) => {
    let r = g.activities.find((x) => x.id === id);
    if (!r) g.activities.push((r = { id, now: 0, before: 0 }));
    return r;
  };
  for (const d of model.days) {
    if (d < before || d > end) continue;
    const key = d >= from ? "now" : "before";
    for (const a of model.actionsOn(d)) {
      if (!started(a)) continue;
      const m = minutes(a.start, a.end);
      const strivings = new Set<string>();
      const activities = a.part_of.length ? a.part_of : [NO_ACTIVITY];
      for (const act of activities) {
        const serves = act ? model.activity(act)?.part_of ?? [] : [];
        for (const s of serves.length ? serves : [UNSERVED]) {
          row(group(s), act)[key] += m;
          strivings.add(s);
        }
      }
      strivings.forEach((s) => (group(s)[key] += m));
    }
  }
  const order = (a: Row, b: Row) => b.now - a.now || b.before - a.before;
  const out = [...groups.values()].filter((g) => g.now + g.before > 0);
  out.forEach((g) => g.activities.sort(order));
  // The strivings by this week's time; the unserved group last.
  return out.sort((a, b) => (a.id === UNSERVED ? 1 : b.id === UNSERVED ? -1 : order(a, b)));
}
