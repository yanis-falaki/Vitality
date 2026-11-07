// Day: one day as lanes of activity (web spec, §6, §10). A heading between arrows to the previous and
// next days with actions (also the ← and → keys), a calendar to jump to any day, and one summary line;
// lanes, one per activity of the day, whose columns are the day's sittings (a gap over 30 minutes
// starts a new one), sized by length so idle time is squeezed out; the selected action's
// screenshots as a strip; activity filters (or a lane's name) that filter the list and dim the other
// lanes; and the day's actions in a list grouped by sitting. The pane fits the window: only the
// strip, the list (and on a phone the lanes, sideways) scroll.
//
// A switch, Actions | Evidence (`?mode=evidence`), shows instead the day's interactions as observed,
// on every device, whether an action cites them yet or not: grouped into sittings by the same rule,
// filtered by device or to those not modeled yet; the arrows and calendar step between days with
// interactions, and the strip shows the selected interaction's screenshots. `?at=latest` selects the
// newest interaction (Home's "Evidence" link) rather than the first.
import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { shotUrl, useDayInteractions, useEvidenceOf, useInteractionDays, type Action, type DayInteraction, type Shot } from "../api/client";
import { CalendarButton } from "../components/Calendar";
import { ShotBadge } from "../components/Evidence";
import { useLightbox } from "../components/Lightbox";
import { WithModel } from "../components/Loading";
import { ViewFrame } from "../components/Shell";
import { Swatch } from "../components/Swatch";
import { clock, day, dayLabel, dayLong, hours, minutes, partOfDay, plural, short } from "../lib/format";
import { lanePalette, lanesOf, NONE, sittings, type Sitting } from "../lib/lanes";
import { isInteraction, type Model } from "../lib/model";
import { useParam, useSelection } from "../lib/selection";
import { shotLabel, thumbsOf } from "../lib/shots";
import "./day.css";


export function Day() {
  return <WithModel>{(model) => <DayView model={model} />}</WithModel>;
}

function DayView({ model }: { model: Model }) {
  const { date } = useParams();
  const navigate = useNavigate();
  const { sel, select, update } = useSelection();
  const evidence = useParam("mode") === "evidence";
  const latest = useParam("at") === "latest";
  const search = evidence ? "?mode=evidence" : "";
  // The days to step between: with actions, or (Evidence) with interactions.
  const observed = useInteractionDays(evidence).data;
  const days = useMemo(() => (evidence ? Object.keys(observed ?? {}) : model.days), [evidence, observed, model]);
  const counts = useMemo(() => (evidence ? new Map(Object.entries(observed ?? {}))
    : new Map(model.days.map((d) => [d, model.actionsOn(d).length]))), [evidence, observed, model]);
  const waiting = evidence && !observed;   // the days with interactions are still loading
  const current = date && (days.includes(date) || waiting) ? date : days[days.length - 1];
  const [only, setOnly] = useState<string | null>(null);   // the activity the list is filtered to
  useEffect(() => setOnly(null), [current]);

  // An action of another day, selected from the inspector, moves to its day.
  const selected = sel ? model.action(sel) : undefined;
  useEffect(() => {
    if (selected && day(selected.start) !== current) {
      navigate({ pathname: `/day/${day(selected.start)}`, search: `${search || "?"}${search ? "&" : ""}sel=${encodeURIComponent(selected.id)}` }, { replace: true });
    }
  }, [selected, current, navigate, search]);

  const actions = useMemo(() => (current ? model.actionsOn(current) : []), [model, current]);
  // The first action of the day is shown until something else is chosen.
  useEffect(() => {
    if (evidence) return;
    if (actions.length && !(selected && day(selected.start) === current) && !(sel && !selected)) select(actions[0].id, { quiet: true });
  }, [evidence, actions, current, selected, sel, select]);

  // Evidence: the day's interactions; the first (or with `at=latest`, the newest) is shown until something
  // else is chosen (an interaction of the day, or an action in the lanes).
  const dayQuery = useDayInteractions(evidence && current ? current : null);
  const interactions = dayQuery.data && dayQuery.data.day === current ? dayQuery.data.interactions : undefined;
  useEffect(() => {
    if (!evidence || !interactions?.length) return;
    if (sel && !(isInteraction(sel) && !interactions.some((i) => i.interaction === sel))) return;
    const pick = latest ? interactions[interactions.length - 1] : interactions[0];
    update({ sel: pick.interaction, at: null }, { quiet: true });
  }, [evidence, interactions, sel, latest, update]);

  if (!current) {
    return <ViewFrame model={model}><div className="notice">{waiting ? "Loading…" : evidence ? "No interactions yet." : "No actions yet."}</div></ViewFrame>;
  }
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
  const shown = evidence && sel && isInteraction(sel) ? interactions?.find((i) => i.interaction === sel) : undefined;
  const switchTo = (mode: "actions" | "evidence") => {
    if ((mode === "evidence") !== evidence) update({ mode: mode === "evidence" ? "evidence" : null, sel: null, at: null });
  };

  return (
    <ViewFrame model={model} empty={evidence ? "Select an interaction to see its evidence." : "Select an action to see its evidence."}>
      <div className="day-left">
        <div className="day-head">
          <DayNav model={model} days={days} counts={counts} current={current} search={search} unit={evidence ? "interaction" : "action"} />
          <span className="day-sub">
            {evidence
              ? interactions ? evidenceSummary(interactions) : "Reading the day's evidence…"
              : <>{plural(actions.length, "action")} · {hours(total)} across {plural(sits.length, "sitting")}</>}
          </span>
          <div className="seg day-mode" role="group" aria-label="Show">
            <button type="button" className={evidence ? "" : "on"} aria-pressed={!evidence} onClick={() => switchTo("actions")}>Actions</button>
            <button type="button" className={evidence ? "on" : ""} aria-pressed={evidence} onClick={() => switchTo("evidence")}>Evidence</button>
          </div>
        </div>
        {actions.length > 0
          ? <Lanes model={model} palette={palette} sits={sits} lanes={lanes} only={evidence ? null : only} sel={focus?.id ?? null} onChoose={choose} />
          : <div className="day-lanes day-lanes-none">No actions on this day yet.</div>}
        {evidence && !focus
          ? <Strip names={shown ? [shown.interaction] : []}
              label={shown ? `${plural(shown.shots.length, "screenshot")} · ${shown.first_operation ?? "no operations"}` : undefined}
              none="No screenshots for this interaction." />
          : <Strip names={focus ? model.interactionsOf(focus) : []} label={focus?.summary && `· ${focus.summary}`}
              none="No screenshots for this action." count />}
        {evidence ? (
          <EvidenceList interactions={interactions} sel={sel} />
        ) : (
          <>
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
          </>
        )}
      </div>
    </ViewFrame>
  );
}

// "14 interactions · 3 not modeled yet · laptop, desktop".
function evidenceSummary(interactions: DayInteraction[]): string {
  const uncited = interactions.filter((i) => !i.actions.length).length;
  const devices = [...new Set(interactions.map((i) => i.device))];
  return [plural(interactions.length, "interaction"), uncited ? `${uncited} not modeled yet` : "", devices.join(", ")].filter(Boolean).join(" · ");
}

interface NavProps { model: Model; days: string[]; counts: Map<string, number>; current: string; search: string; unit: string }

/** "‹ Tuesday, November 3 ›": the days with actions (or interactions) before and after, and a calendar for any other. */
function DayNav({ model, days, counts, current, search, unit }: NavProps) {
  const navigate = useNavigate();
  const at = days.indexOf(current);
  const prev = at < 0 ? undefined : days[at - 1], next = at < 0 ? undefined : days[at + 1];
  const go = (d: string | undefined) => { if (d) navigate(`/day/${d}${search}`); };
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
      {days.length > 0 && (
        <CalendarButton counts={counts} first={days[0]} last={days[days.length - 1]} current={current} unit={unit}
          onPick={(d) => navigate(`/day/${d}${search}`)} />
      )}
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

/** The screenshots of the selected action's interactions (or one interaction), in order, as a strip to scroll through. */
function Strip({ names, label, none, count = false }: { names: string[]; label?: string; none: string; count?: boolean }) {
  const { show } = useLightbox();
  const evidence = useEvidenceOf(names);
  const shots = names.flatMap((name, i) => (evidence[i]?.shots ?? []).map((shot) => ({
    url: shotUrl(name, shot.file), shot, at: evidence[i]!.start, seconds: Number(shot.file.match(/^\d+/)?.[0] ?? 0),
  })));
  const urls = shots.map((s) => s.url);
  const captions = shots.map((s) => shotLabel(s.shot));
  const title = names.length === 0 ? "Screenshots" : count ? `${plural(shots.length, "screenshot")} ${label ?? ""}` : label ?? "Screenshots";
  return (
    <div className="day-strip">
      <h3 className="eyebrow">{title}</h3>
      <div className="day-strip-row">
        {shots.map((s, i) => (
          <figure key={s.url}>
            <button type="button" className="thumb" onClick={() => show(urls, i, captions)}
              aria-label={`Screenshot ${i + 1}${captions[i] ? `, ${captions[i]}` : ""}`}>
              <img src={s.url} alt="" loading="lazy" />
              <ShotBadge shot={s.shot} />
            </button>
            <figcaption>{clock(`${s.at.slice(0, 11)}${localTime(s.at, s.seconds)}`)}</figcaption>
          </figure>
        ))}
        {names.length > 0 && evidence.every((e) => e !== undefined) && shots.length === 0 && <span className="muted">{none}</span>}
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

/** The day's interactions, grouped into sittings, filtered by device or to those no action cites yet. */
function EvidenceList({ interactions, sel }: { interactions: DayInteraction[] | undefined; sel: string | null }) {
  const { select } = useSelection();
  const [device, setDevice] = useState<string | null>(null);
  const [uncited, setUncited] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const day0 = interactions?.[0]?.start.slice(0, 10);
  useEffect(() => { setDevice(null); setUncited(false); }, [day0]);
  useEffect(() => {
    if (sel) box.current?.querySelector(`[data-id="${CSS.escape(sel)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel, interactions]);
  const all = interactions ?? [];
  const devices = [...new Set(all.map((i) => i.device))].sort();
  const notModeled = all.filter((i) => !i.actions.length).length;
  const shown = (i: DayInteraction) => (device === null || i.device === device) && (!uncited || !i.actions.length);
  const sits = sittings(all);
  return (
    <>
      <div className="day-filters" role="group" aria-label="Show the interactions of">
        <button type="button" className={`chip${device === null && !uncited ? " on" : ""}`}
          onClick={() => { setDevice(null); setUncited(false); }}>All {all.length} interactions</button>
        {devices.map((d) => (
          <button type="button" key={d} className={`chip${device === d ? " on" : ""}`} aria-pressed={device === d}
            onClick={() => setDevice(device === d ? null : d)}>
            <span className="chip-text">{d} · {all.filter((i) => i.device === d).length}</span>
          </button>
        ))}
        {notModeled > 0 && (
          <button type="button" className={`chip${uncited ? " on" : ""}`} aria-pressed={uncited} onClick={() => setUncited(!uncited)}>
            <span className="chip-text">Not modeled yet · {notModeled}</span>
          </button>
        )}
      </div>
      <div className="day-list" ref={box} data-testid="day-evidence">
        {!interactions && <p className="day-list-note">Reading the day's evidence…</p>}
        {interactions && !all.some(shown) && <p className="day-list-note">No interactions to show.</p>}
        {/* Newest first: the latest sitting on top, and within each sitting the latest interaction. */}
        {sits.filter((s) => s.actions.some(shown)).reverse().map((s) => (
          <Fragment key={s.start}>
            <h3 className="day-sitting">{partOfDay(s.start)} · {clock(s.start)} – {clock(s.end)}</h3>
            {s.actions.filter(shown).reverse().map((i) => <EvidenceRow key={i.interaction} i={i} on={sel === i.interaction} onClick={() => select(i.interaction)} />)}
          </Fragment>
        ))}
      </div>
    </>
  );
}

function EvidenceRow({ i, on, onClick }: { i: DayInteraction; on: boolean; onClick(): void }) {
  const thumbs = thumbsOf(i.shots);
  return (
    <button type="button" data-id={i.interaction} className={`day-row day-ev-row${on ? " on" : ""}`} onClick={onClick}>
      <span className="time">{clock(i.start)}</span>
      <span className="day-row-text">
        <span className="day-ev-op">
          {i.first_operation ?? "No operations"}
          {i.operations > 1 && <span className="day-ev-more"> (+{i.operations - 1})</span>}
        </span>
        <span className="day-row-activity">
          {i.device}
          {!i.actions.length && <span className="day-ev-tag">not modeled yet</span>}
        </span>
      </span>
      <span className="day-ev-thumbs" aria-hidden>
        {thumbs.map((s: Shot) => (
          <span className="day-ev-thumb" key={s.file}>
            <img src={shotUrl(i.interaction, s.file)} alt="" loading="lazy" />
            <ShotBadge shot={s} brief />
          </span>
        ))}
      </span>
    </button>
  );
}
