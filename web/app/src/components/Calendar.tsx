// A small month calendar behind a button, for jumping to any day: the days with actions are tinted
// by how many (the three softest steps of the action colour) and can be picked; the others are plain. The
// months step from the first to the last with actions. Closes on a pick, Escape or a click outside.
import { useEffect, useRef, useState } from "react";
import { addMonths, monthGrid, monthLabel, monthOf, shade } from "../lib/calendar";
import { dayLabel, plural } from "../lib/format";

interface Props {
  counts: Map<string, number>;   // actions (or `unit`s) per day, for the days that have any
  first: string;                 // the first and last days with any
  unit?: string;                 // what is counted: "action" (the default) or "interaction"
  last: string;
  current: string;               // the day shown
  onPick(day: string): void;
}

const WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"];

export function CalendarButton({ counts, first, last, current, unit, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", key); };
  }, [open]);
  return (
    <div className="calendar-anchor" ref={box}>
      <button type="button" className="button calendar-button" aria-expanded={open} aria-haspopup="dialog"
        aria-label="Choose a day" title="Choose a day" onClick={() => setOpen((o) => !o)}>
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden>
          <rect x="1.75" y="2.75" width="12.5" height="11.5" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M1.75 6.5h12.5M5 1.25v3M11 1.25v3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <Month counts={counts} first={first} last={last} current={current} unit={unit}
          onPick={(d) => { setOpen(false); onPick(d); }} />
      )}
    </div>
  );
}

function Month({ counts, first, last, current, unit = "action", onPick }: Props) {
  const [month, setMonth] = useState(monthOf(current));
  const max = Math.max(1, ...counts.values());
  const earlier = month > monthOf(first), later = month < monthOf(last);
  return (
    <div className="calendar" role="dialog" aria-label="Choose a day">
      <div className="calendar-head">
        <button type="button" className="calendar-step" disabled={!earlier} aria-label="Previous month"
          onClick={() => setMonth(addMonths(month, -1))}>‹</button>
        <span className="calendar-month">{monthLabel(month)}</span>
        <button type="button" className="calendar-step" disabled={!later} aria-label="Next month"
          onClick={() => setMonth(addMonths(month, 1))}>›</button>
      </div>
      <div className="calendar-grid" role="grid">
        {WEEKDAYS.map((w, i) => <span key={i} className="calendar-weekday" aria-hidden>{w}</span>)}
        {monthGrid(month).flat().map((d, i) => {
          if (!d) return <span key={`blank-${i}`} />;
          const n = counts.get(d) ?? 0;
          const text = Number(d.slice(8));
          return n > 0 ? (
            <button type="button" key={d} data-day={d} data-step={shade(n, max, 3)}
              className={`calendar-day${d === current ? " on" : ""}`} aria-current={d === current ? "date" : undefined}
              aria-label={`${dayLabel(d)}, ${plural(n, unit)}`} title={plural(n, unit)} onClick={() => onPick(d)}>{text}</button>
          ) : <span key={d} className="calendar-day none">{text}</span>;
        })}
      </div>
    </div>
  );
}
