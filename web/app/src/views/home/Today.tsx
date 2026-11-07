// "Today so far": today's lanes, compact (Day's lanes and colours, a sitting per column), and what is
// probably happening now when the latest action ended less than half an hour ago. The lanes open Day;
// "Evidence" opens today's interactions (Day's Evidence mode) at the newest.
// With nothing today, a quiet line says so and the last day with evidence is shown in its place.
import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import type { Action } from "../../api/client";
import { Swatch } from "../../components/Swatch";
import { clock, day, dayLabel, hours, minutes, plural } from "../../lib/format";
import { lanePalette, lanesOf, NONE, sittings } from "../../lib/lanes";
import { useLayout } from "../../lib/media";
import type { Model } from "../../lib/model";
import { atOffset, type Now } from "../../lib/now";

const RECENT = 30 * 60_000;   // an action that ended this recently is "probably now"
const LANES = 8;              // lanes shown at most (as many as there are lane colours); on a phone, 5
const PHONE_LANES = 5;

export function Today({ model, now, lastCapture }: { model: Model; now: Now; lastCapture: string | null }) {
  const today = upTo(model.actionsOn(now.today), now);
  const shown = today.length ? now.today : [...model.days].reverse().find((d) => d < now.today);
  const actions = today.length ? today : shown ? model.actionsOn(shown) : [];
  const latest = today[today.length - 1];
  const current = latest && now.ms - Date.parse(latest.end) < RECENT ? latest : undefined;
  const capturedToday = !!lastCapture && atOffset(Date.parse(lastCapture), now.local.slice(19)).slice(0, 10) === now.today;

  return (
    <section className="home-section" aria-labelledby="home-today">
      <div className="home-section-head">
        <h2 className="eyebrow" id="home-today">Today so far</h2>
        <span className="home-links">
          <Link className="home-more" to={`/day/${now.today}?mode=evidence&at=latest`}>Evidence</Link>
          {shown && <Link className="home-more" to={`/day/${shown}`}>Open Day →</Link>}
        </span>
      </div>
      {!today.length && (
        <p className="home-quiet">
          {capturedToday ? "Nothing modeled yet today" : "Nothing captured yet today"}
          {shown ? <> — last evidence {dayLabel(shown)}</> : null}
        </p>
      )}
      {current && (
        <Link className="home-now" to={`/day/${day(current.start)}?sel=${encodeURIComponent(current.id)}`}>
          <i className="home-now-dot" aria-hidden />
          <span className="home-now-text">
            <span className="home-now-label">Probably now</span> {current.summary}
            {current.part_of[0] && <span className="home-now-activity"> · {model.title(current.part_of[0])}</span>}
          </span>
          <span className="time">since {clock(current.start)}</span>
        </Link>
      )}
      {shown && actions.length > 0 && <MiniLanes model={model} day={shown} actions={actions} label={!today.length} />}
    </section>
  );
}

// The actions that had started by `now` (a fixture's present may fall inside its last day).
const upTo = (actions: Action[], now: Now) => actions.filter((a) => Date.parse(a.start) <= now.ms);

/** Day's lanes, small: a row per activity, a column per sitting sized by its length; opens Day. */
function MiniLanes({ model, day: d, actions, label }: { model: Model; day: string; actions: Action[]; label: boolean }) {
  const most = useLayout() === "phone" ? PHONE_LANES : LANES;
  const sits = sittings(actions);
  const all = lanesOf(actions);
  const palette = lanePalette(model, all);   // as Day colours them, from all of the day's lanes
  // The activities with the most time, in Day's order; the others are counted beneath.
  const time = new Map<string, number>();
  for (const a of actions) time.set(a.part_of[0] ?? NONE, (time.get(a.part_of[0] ?? NONE) ?? 0) + minutes(a.start, a.end));
  const kept = new Set([...all].sort((a, b) => time.get(b)! - time.get(a)!).slice(0, most));
  const lanes = all.filter((l) => kept.has(l));
  const rest = all.filter((l) => !kept.has(l));
  const lengths = sits.map((s) => Math.max(14, minutes(s.start, s.end) + 2));
  const total = actions.reduce((n, a) => n + minutes(a.start, a.end), 0);
  const span = lengths.reduce((a, b) => a + b, 0);   // a sitting's time is labelled when its column has room
  return (
    <Link className="home-lanes" to={`/day/${d}`} aria-label={`Open Day for ${dayLabel(d)}`} data-testid="home-lanes">
      <span className="home-lanes-sub">
        {label && <b>{dayLabel(d)} · </b>}
        {plural(actions.length, "action")} · {hours(total)} across {plural(sits.length, "sitting")}
      </span>
      <span className="home-lane home-lane-axis" aria-hidden>
        <span className="home-lane-name" />
        <span className="home-tracks">
          {sits.map((s, i) => (
            <span key={s.start} className="home-track home-axis" style={{ flexGrow: lengths[i] }}>
              {lengths[i] / span >= 0.2 ? clock(s.start) : ""}
            </span>
          ))}
        </span>
      </span>
      {lanes.map((id) => (
        <span className="home-lane" key={id || "none"}>
          <span className="home-lane-name">
            <Swatch model={model} id={id} palette={palette} />
            <span className="home-lane-title">{id ? model.title(id) : "No activity yet"}</span>
          </span>
          <span className="home-tracks">
            {sits.map((s, i) => {
              const t0 = Date.parse(s.start);
              return (
                <span className="home-track" key={s.start} style={{ flexGrow: lengths[i] }}>
                  {s.actions.filter((a) => (a.part_of[0] ?? NONE) === id).map((a) => (
                    <i key={a.id} className="home-bar" style={{
                      left: `${((Date.parse(a.start) - t0) / 60000 / lengths[i]) * 100}%`,
                      width: `${(minutes(a.start, a.end) / lengths[i]) * 100}%`, "--c": palette.get(id),
                    } as CSSProperties} />
                  ))}
                </span>
              );
            })}
          </span>
        </span>
      ))}
      {rest.length > 0 && (
        <span className="home-lanes-rest">
          and {plural(rest.length, "other activity", "other activities")}, {hours(rest.reduce((n, l) => n + time.get(l)!, 0))}
        </span>
      )}
    </Link>
  );
}
