// Day: one day as lanes of activity (web spec, §6, §10). A heading between arrows to the previous and
// next days with actions (also the ← and → keys), a calendar to jump to any day, and one summary line;
// lanes, one per activity of the day, whose columns are the day's sittings (a gap over 30 minutes
// starts a new one), sized by length so idle time is squeezed out; the selected action's
// screenshots as a strip; activity filters (or a lane's name) that filter the list and dim the other
// lanes; and the day's actions in a list grouped by sitting. The pane fits the window: only the
// strip, the list (and on a phone the lanes, sideways) scroll.
import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { shotUrl, useEvidenceOf, type Action } from "../api/client";
import { CalendarButton } from "../components/Calendar";
import { useLightbox } from "../components/Lightbox";
import { WithModel } from "../components/Loading";
import { ViewFrame } from "../components/Shell";
import { Swatch } from "../components/Swatch";
import { clock, day, dayLabel, dayLong, hours, minutes, partOfDay, plural, short } from "../lib/format";
import { lanePalette, lanesOf, NONE, sittings, type Sitting } from "../lib/lanes";
import type { Model } from "../lib/model";
import { useSelection } from "../lib/selection";
import "./day.css";


export function Day() {
  return <WithModel>{(model) => <DayView model={model} />}</WithModel>;
}

function DayView({ model }: { model: Model }) {
  const { date } = useParams();
  const navigate = useNavigate();
  const { sel, select } = useSelection();
  const current = date && model.days.includes(date) ? date : model.days[model.days.length - 1];
  const [only, setOnly] = useState<string | null>(null);   // the activity the list is filtered to
  useEffect(() => setOnly(null), [current]);

  // An action of another day, selected from the inspector, moves to its day.
  const selected = sel ? model.action(sel) : undefined;
  useEffect(() => {
    if (selected && day(selected.start) !== current) {
      navigate({ pathname: `/day/${day(selected.start)}`, search: `?sel=${encodeURIComponent(selected.id)}` }, { replace: true });
    }
  }, [selected, current, navigate]);

  const actions = useMemo(() => (current ? model.actionsOn(current) : []), [model, current]);
  // The first action of the day is shown until something else is chosen.
  useEffect(() => {
    if (actions.length && !(selected && day(selected.start) === current) && !(sel && !selected)) select(actions[0].id, { quiet: true });
  }, [actions, current, selected, sel, select]);

  if (!current) return <ViewFrame model={model}><div className="notice">No actions yet.</div></ViewFrame>;
  const sits = sittings(actions);
  const lanes = lanesOf(actions);
  const palette = lanePalette(model, lanes);
  const total = actions.reduce((n, a) => n + minutes(a.start, a.end), 0);
  const choose = (id: string | null) => {
    const next = only === id ? null : id;
    setOnly(next);
    if (next) select(next);
  };
  const focus = selected && day(selected.start) === current ? selected : undefined;

  return (
    <ViewFrame model={model} empty="Select an action to see its evidence.">
      <div className="day-left">
        <div className="day-head">
          <DayNav model={model} current={current} />
          <span className="day-sub">{plural(actions.length, "action")} · {hours(total)} across {plural(sits.length, "sitting")}</span>
        </div>
        <Lanes model={model} palette={palette} sits={sits} lanes={lanes} only={only} sel={focus?.id ?? null} onChoose={choose} />
        <Strip model={model} action={focus} />
        <div className="day-filters" role="group" aria-label="Show the actions of">
          <button type="button" className={`chip${only === null ? " on" : ""}`} onClick={() => setOnly(null)}>All {actions.length} actions</button>
          {lanes.map((id) => (
            <button type="button" key={id || "none"} className={`chip${only === id ? " on" : ""}`} onClick={() => choose(id)}>
              <Swatch model={model} id={id} palette={palette} />
              <span className="chip-text">{id ? short(model.title(id), 34) : "No activity yet"} · {actions.filter((a) => (a.part_of[0] ?? NONE) === id).length}</span>
            </button>
          ))}
        </div>
        <List model={model} palette={palette} sits={sits} only={only} sel={focus?.id ?? null} />
      </div>
    </ViewFrame>
  );
}

/** "‹ Tuesday, November 3 ›": the days with actions before and after, and a calendar for any other. */
function DayNav({ model, current }: { model: Model; current: string }) {
  const navigate = useNavigate();
  const at = model.days.indexOf(current);
  const prev = model.days[at - 1], next = model.days[at + 1];
  const counts = useMemo(() => new Map(model.days.map((d) => [d, model.actionsOn(d).length])), [model]);
  const go = (d: string | undefined) => { if (d) navigate(`/day/${d}`); };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || (e.target as HTMLElement).closest("input, textarea, [role=dialog]")) return;
      if (e.key === "ArrowLeft") go(prev);
      if (e.key === "ArrowRight") go(next);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const year = current.slice(0, 4) !== model.lastDay?.slice(0, 4) ? `, ${current.slice(0, 4)}` : "";
  return (
    <div className="day-nav">
      <button type="button" className="day-step" disabled={!prev} onClick={() => go(prev)}
        aria-label={prev ? `Previous day: ${dayLabel(prev)}` : "No earlier day"} title={prev ? dayLabel(prev) : undefined}>‹</button>
      <h1>{dayLong(current)}{year}</h1>
      <button type="button" className="day-step" disabled={!next} onClick={() => go(next)}
        aria-label={next ? `Next day: ${dayLabel(next)}` : "No later day"} title={next ? dayLabel(next) : undefined}>›</button>
      <CalendarButton counts={counts} first={model.days[0]} last={model.days[model.days.length - 1]} current={current}
        onPick={(d) => navigate(`/day/${d}`)} />
    </div>
  );
}

interface LanesProps { model: Model; palette: Map<string, string>; sits: Sitting[]; lanes: string[]; only: string | null; sel: string | null; onChoose(id: string): void }

/** A lane per activity; its columns are the sittings, each as wide as it is long (at least 14 minutes). */
function Lanes({ model, palette, sits, lanes, only, sel, onChoose }: LanesProps) {
  const { select } = useSelection();
  const lengths = sits.map((s) => Math.max(14, minutes(s.start, s.end) + 2));
  const width = (i: number) => ({ flexGrow: lengths[i] });
  return (
    <div className="day-lanes" data-testid="lanes">
      <div className="day-lanes-inner" style={{ minWidth: `${130 + lengths.reduce((a, b) => a + b, 0) * 3}px` }}>
        <div className="day-lane day-axis">
          <div className="day-lane-name" />
          <div className="day-tracks">
            {sits.map((s, i) => (
              <div className="day-track day-axis-track" key={s.start} style={width(i)}>
                <span>{clock(s.start)}</span>{lengths[i] > 40 && <span>{clock(s.end)}</span>}
              </div>
            ))}
          </div>
        </div>
        {lanes.map((id) => (
          <div className={`day-lane${only !== null && only !== id ? " dim" : ""}`} key={id || "none"}>
            <button type="button" className={`day-lane-name${only === id ? " on" : ""}`} onClick={() => onChoose(id)}
              title={id ? model.title(id) : "No activity yet"}>
              <Swatch model={model} id={id} palette={palette} />
              <span>{id ? model.title(id) : "No activity yet"}</span>
            </button>
            <div className="day-tracks">
              {sits.map((s, i) => {
                const t0 = Date.parse(s.start);
                return (
                  <div className="day-track" key={s.start} style={width(i)}>
                    {s.actions.filter((a) => (a.part_of[0] ?? NONE) === id).map((a) => (
                      <button type="button" key={a.id} className={`day-bar${sel === a.id ? " on" : ""}`}
                        style={{ left: `${((Date.parse(a.start) - t0) / 60000 / lengths[i]) * 100}%`, width: `${(minutes(a.start, a.end) / lengths[i]) * 100}%`, "--c": palette.get(id) } as CSSProperties}
                        title={`${clock(a.start)} · ${a.summary}`} aria-label={`${clock(a.start)} ${a.summary}`} onClick={() => select(a.id)} />
                    ))}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** The selected action's screenshots, in order, as a strip to scroll through. */
function Strip({ model, action }: { model: Model; action: Action | undefined }) {
  const { show } = useLightbox();
  const names = action ? model.interactionsOf(action) : [];
  const evidence = useEvidenceOf(names);
  const shots = names.flatMap((name, i) => (evidence[i]?.screenshots ?? []).map((file) => ({
    url: shotUrl(name, file), at: evidence[i]!.start, seconds: Number(file.match(/^\d+/)?.[0] ?? 0),
  })));
  const urls = shots.map((s) => s.url);
  return (
    <div className="day-strip">
      <h3 className="eyebrow">{action ? `${plural(shots.length, "screenshot")} · ${action.summary}` : "Screenshots"}</h3>
      <div className="day-strip-row">
        {shots.map((s, i) => (
          <figure key={s.url}>
            <button type="button" className="thumb" onClick={() => show(urls, i)} aria-label={`Screenshot ${i + 1}`}>
              <img src={s.url} alt="" loading="lazy" />
            </button>
            <figcaption>{clock(`${s.at.slice(0, 11)}${localTime(s.at, s.seconds)}`)}</figcaption>
          </figure>
        ))}
        {action && shots.length === 0 && <span className="muted">No screenshots for this action.</span>}
      </div>
    </div>
  );
}

// The wall-clock "HH:MM" `seconds` after the local time `iso` (as written in the file, offset ignored).
function localTime(iso: string, seconds: number): string {
  const m = Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16)) + Math.floor((Number(iso.slice(17, 19)) + seconds) / 60);
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

function List({ model, palette, sits, only, sel }: { model: Model; palette: Map<string, string>; sits: Sitting[]; only: string | null; sel: string | null }) {
  const { select } = useSelection();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (sel) box.current?.querySelector(`[data-id="${CSS.escape(sel)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);
  const shown = (a: Action) => only === null || (a.part_of[0] ?? NONE) === only;
  return (
    <div className="day-list" ref={box}>
      {sits.filter((s) => s.actions.some(shown)).map((s) => (
        <Fragment key={s.start}>
          <h3 className="day-sitting">{partOfDay(s.start)} · {clock(s.start)} – {clock(s.end)}</h3>
          {s.actions.filter(shown).map((a) => (
            <button type="button" key={a.id} data-id={a.id} className={`day-row${sel === a.id ? " on" : ""}`} onClick={() => select(a.id)}>
              <span className="time">{clock(a.start)}</span>
              <Swatch model={model} id={a.part_of[0] ?? NONE} palette={palette} />
              <span className="day-row-text">
                {a.summary}
                <span className="day-row-activity">{a.part_of[0] ? model.title(a.part_of[0]) : "No activity yet"}</span>
              </span>
              <span className="day-row-len">{minutes(a.start, a.end)} min</span>
            </button>
          ))}
        </Fragment>
      ))}
    </div>
  );
}
