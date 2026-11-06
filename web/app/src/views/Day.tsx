// Day: one day as lanes of activity (web spec, §6). Day tabs; a heading with one summary line;
// lanes, one per activity of the day, whose columns are the day's sittings (a gap over 30 minutes
// starts a new one), sized by length so idle time is squeezed out; the selected action's
// screenshots as a strip; activity filters (or a lane's name) that filter the list and dim the other
// lanes; and the day's actions in a list grouped by sitting. The pane fits the window: only the
// strip, the list (and on a phone the lanes, sideways) scroll.
import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { shotUrl, useEvidenceOf, type Action } from "../api/client";
import { useLightbox } from "../components/Lightbox";
import { WithModel } from "../components/Loading";
import { ViewFrame } from "../components/Shell";
import { clock, day, dayLabel, dayLong, hours, minutes, partOfDay, plural, short } from "../lib/format";
import type { Model } from "../lib/model";
import { useSelection } from "../lib/selection";
import "./day.css";

const GAP = 30;            // minutes without an action that end a sitting
const NONE = "";           // the lane of actions with no activity yet
const LANE_COLOURS = 8;

/** The colour of an activity's lane (`--lane-1` … `--lane-8`, by its place in the model; none: grey). */
function laneColour(model: Model, id: string): string {
  const i = model.activities.findIndex((a) => a.id === id);
  return i < 0 ? "var(--lane-none)" : `var(--lane-${(i % LANE_COLOURS) + 1})`;
}

/** A small square in the lane's colour (dashed outline when the activity is tentative). */
function Swatch({ model, id }: { model: Model; id: string }) {
  const tentative = !!id && model.activity(id)?.status === "tentative";
  return <i className={`day-swatch${tentative ? " tentative" : ""}`} style={{ "--c": laneColour(model, id) } as CSSProperties} aria-hidden />;
}

interface Sitting { start: string; end: string; actions: Action[] }

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

  const actions = useMemo(() => model.actions.filter((a) => day(a.start) === current), [model, current]);
  // The first action of the day is shown until something else is chosen.
  useEffect(() => {
    if (actions.length && !(selected && day(selected.start) === current) && !(sel && !selected)) select(actions[0].id, { quiet: true });
  }, [actions, current, selected, sel, select]);

  if (!current) return <ViewFrame model={model}><div className="notice">No actions yet.</div></ViewFrame>;
  const sits = sittings(actions);
  const lanes = [...new Set(actions.map((a) => a.part_of[0] ?? NONE))]
    .sort((a, b) => (a === NONE ? 1 : b === NONE ? -1 : 0));
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
        <DayTabs days={model.days} current={current} />
        <div className="day-head">
          <h1>{dayLong(current)}</h1>
          <span className="day-sub">{plural(actions.length, "action")} · {hours(total)} across {plural(sits.length, "sitting")}</span>
        </div>
        <Lanes model={model} sits={sits} lanes={lanes} only={only} sel={focus?.id ?? null} onChoose={choose} />
        <Strip model={model} action={focus} />
        <div className="day-filters" role="group" aria-label="Show the actions of">
          <button type="button" className={`chip${only === null ? " on" : ""}`} onClick={() => setOnly(null)}>All {actions.length} actions</button>
          {lanes.map((id) => (
            <button type="button" key={id || "none"} className={`chip${only === id ? " on" : ""}`} onClick={() => choose(id)}>
              <Swatch model={model} id={id} />
              <span className="chip-text">{id ? short(model.title(id), 34) : "No activity yet"} · {actions.filter((a) => (a.part_of[0] ?? NONE) === id).length}</span>
            </button>
          ))}
        </div>
        <List model={model} sits={sits} only={only} sel={focus?.id ?? null} />
      </div>
    </ViewFrame>
  );
}

function DayTabs({ days, current }: { days: string[]; current: string }) {
  const navigate = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [current]);
  return (
    <div className="day-tabs" ref={box} role="tablist">
      {days.map((d) => (
        <button type="button" role="tab" key={d} aria-selected={d === current} className={d === current ? "on" : ""}
          onClick={() => navigate(`/day/${d}`)}>{dayLabel(d)}</button>
      ))}
    </div>
  );
}

interface LanesProps { model: Model; sits: Sitting[]; lanes: string[]; only: string | null; sel: string | null; onChoose(id: string): void }

/** A lane per activity; its columns are the sittings, each as wide as it is long (at least 14 minutes). */
function Lanes({ model, sits, lanes, only, sel, onChoose }: LanesProps) {
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
              <Swatch model={model} id={id} />
              <span>{id ? model.title(id) : "No activity yet"}</span>
            </button>
            <div className="day-tracks">
              {sits.map((s, i) => {
                const t0 = Date.parse(s.start);
                return (
                  <div className="day-track" key={s.start} style={width(i)}>
                    {s.actions.filter((a) => (a.part_of[0] ?? NONE) === id).map((a) => (
                      <button type="button" key={a.id} className={`day-bar${sel === a.id ? " on" : ""}`}
                        style={{ left: `${((Date.parse(a.start) - t0) / 60000 / lengths[i]) * 100}%`, width: `${(minutes(a.start, a.end) / lengths[i]) * 100}%`, "--c": laneColour(model, id) } as CSSProperties}
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

function List({ model, sits, only, sel }: { model: Model; sits: Sitting[]; only: string | null; sel: string | null }) {
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
              <Swatch model={model} id={a.part_of[0] ?? NONE} />
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

function sittings(actions: Action[]): Sitting[] {
  const out: Sitting[] = [];
  for (const a of actions) {
    const last = out[out.length - 1];
    if (last && (Date.parse(a.start) - Date.parse(last.end)) / 60000 <= GAP) {
      last.actions.push(a);
      if (Date.parse(a.end) > Date.parse(last.end)) last.end = a.end;
    } else out.push({ start: a.start, end: a.end, actions: [a] });
  }
  return out;
}
