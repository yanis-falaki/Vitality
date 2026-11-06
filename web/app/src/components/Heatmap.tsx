// When a page's actions happened, as a calendar heatmap: one small cell per day of the model, a
// column per week (Monday on top), month names above; shaded in four soft steps of the page's level
// colour by how many of its actions fell on that day. A day with some opens Day for that date. The
// strip scrolls sideways inside itself when the model is longer than the page is wide, starting at
// the most recent weeks.
import { useLayoutEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import type { Action } from "../api/client";
import { addDays, daysBetween, monthShort, shade, weekStart } from "../lib/calendar";
import { day, dayLabel, plural } from "../lib/format";
import type { Model } from "../lib/model";

interface Props { model: Model; actions: Action[]; level: "striving" | "activity" }

export function Heatmap({ model, actions, level }: Props) {
  const navigate = useNavigate();
  const scroller = useRef<HTMLDivElement>(null);
  const counts = useMemo(() => {
    const out = new Map<string, number>();
    for (const a of actions) out.set(day(a.start), (out.get(day(a.start)) ?? 0) + 1);
    return out;
  }, [actions]);
  const first = model.days[0], last = model.lastDay;
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [first, last]);
  if (!actions.length || !first || !last) return null;

  const start = weekStart(first);
  const weeks = Math.floor(daysBetween(start, last) / 7) + 1;
  const max = Math.max(...counts.values());
  const cells = [];
  for (let i = 0; i < weeks * 7; i++) {
    const d = addDays(start, i);
    const n = counts.get(d) ?? 0;
    const inside = d >= first && d <= last;
    const label = `${dayLabel(d)} · ${n ? plural(n, "action") : "none"}`;
    cells.push(n > 0
      ? <button type="button" key={d} className="heat-cell" data-step={shade(n, max)} title={label} aria-label={label}
          onClick={() => navigate(`/day/${d}`)} />
      : <i key={d} className={`heat-cell${inside ? "" : " outside"}`} title={inside ? label : undefined} />);
  }
  // A month's name over the week holding its first day (and the first week, for the first month).
  const months: { col: number; text: string }[] = [];
  for (let w = 0; w < weeks; w++) {
    const week = [0, 1, 2, 3, 4, 5, 6].map((k) => addDays(start, w * 7 + k));
    const opens = week.find((d) => d.endsWith("-01") && d >= first);
    if (opens) months.push({ col: w, text: monthShort(opens) });
    else if (w === 0) months.push({ col: 0, text: monthShort(first) });
  }
  const days = counts.size;
  return (
    <figure className={`heat heat-${level}`} aria-label="When its actions happened">
      <div className="heat-scroll" ref={scroller}>
        <div className="heat-grid" style={{ gridTemplateColumns: `repeat(${weeks}, var(--heat-cell))` }}>
          {months.map((m, i) => {
            const next = months[i + 1]?.col ?? weeks;
            return next - m.col >= 2 || i === months.length - 1
              ? <span key={m.col} className="heat-month" style={{ gridColumn: `${m.col + 1} / span ${Math.max(1, next - m.col)}` }}>{m.text}</span>
              : null;
          })}
          <div className="heat-cells" style={{ gridColumn: `1 / span ${weeks}` }}>{cells}</div>
        </div>
      </div>
      <figcaption className="heat-caption">
        <span>{plural(actions.length, "action")} on {plural(days, "day")}</span>
        <span className="heat-key" aria-hidden>less{[1, 2, 3, 4].map((s) => <i key={s} className="heat-cell" data-step={s} />)}more</span>
      </figcaption>
    </figure>
  );
}
