// Reader: the model as pages to read (web spec, §6). An outline of the strivings, their activities,
// then the activities serving none; the page of the one chosen (`?page=<id>`): serif title, byline,
// when its actions happened, its body, then its activities or its actions by day. References and
// actions open in the inspector. On a phone the outline is a drawer.
import { Fragment, useEffect, useState } from "react";
import type { Action, Activity, Striving } from "../api/client";
import { Confidence, Dot, LevelLabel, Status } from "../components/bits";
import { span } from "../components/Inspector";
import { WithModel } from "../components/Loading";
import { RichText } from "../components/RichText";
import { ViewFrame } from "../components/Shell";
import { clock, day, dayLabel, minutes, plural, short } from "../lib/format";
import { useLayout } from "../lib/media";
import type { Model } from "../lib/model";
import { useParam, useSelection } from "../lib/selection";
import "./reader.css";

export function Reader() {
  return <WithModel>{(model) => <ReaderView model={model} />}</WithModel>;
}

function ReaderView({ model }: { model: Model }) {
  const { sel, update } = useSelection();
  const layout = useLayout();
  const [drawer, setDrawer] = useState(false);
  const asked = useParam("page");
  const fallback = model.strivings[0]?.id ?? model.activities[0]?.id ?? null;
  const page = asked && (model.kind(asked) === "striving" || model.kind(asked) === "activity") ? asked : fallback;
  const open = (id: string) => {
    update({ page: id, sel: null });
    setDrawer(false);
  };
  useEffect(() => {
    document.querySelector(".reader-article")?.scrollTo({ top: 0 });
  }, [page]);

  const outline = <Outline model={model} page={page} open={open} />;
  return (
    <ViewFrame model={model} className="reader" empty={<>Click a dated reference or an action<br />to see its evidence here.</>}>
      <div className={`reader-grid reader-${layout}`}>
        {layout !== "phone" && <nav className="reader-outline" aria-label="Outline">{outline}</nav>}
        <article className="reader-article">
          {layout === "phone" && (
            <button type="button" className="button reader-outline-button" onClick={() => setDrawer(true)}>
              ☰ Outline
            </button>
          )}
          {page ? <Page model={model} id={page} sel={sel} open={open} /> : <div className="notice">The model is empty so far.</div>}
        </article>
        {layout === "phone" && drawer && (
          <>
            <div className="reader-scrim" onClick={() => setDrawer(false)} />
            <nav className="reader-outline reader-drawer" aria-label="Outline">
              <div className="reader-drawer-head">
                <span className="eyebrow">Outline</span>
                <button type="button" className="button" onClick={() => setDrawer(false)}>Close</button>
              </div>
              {outline}
            </nav>
          </>
        )}
      </div>
    </ViewFrame>
  );
}

function Outline({ model, page, open }: { model: Model; page: string | null; open(id: string): void }) {
  const item = (a: Activity) => (
    <button type="button" key={a.id} className={`outline-activity${a.status === "tentative" ? " tentative" : ""}${page === a.id ? " on" : ""}`}
      onClick={() => open(a.id)}>{a.title}</button>
  );
  return (
    <>
      <div className="eyebrow outline-head">Strivings</div>
      {model.strivings.map((s) => (
        <Fragment key={s.id}>
          <button type="button" className={`outline-striving${page === s.id ? " on" : ""}`} onClick={() => open(s.id)}>{s.title}</button>
          {model.activitiesOf(s.id).map(item)}
        </Fragment>
      ))}
      {model.unassigned.length > 0 && <div className="eyebrow outline-head">Not yet serving a striving</div>}
      {model.unassigned.map(item)}
      {model.api.problems.length > 0 && (
        <div className="outline-problems" title={model.api.problems.map((p) => `${p.path}: ${p.error}`).join("\n")}>
          {plural(model.api.problems.length, "file")} could not be read:
          {model.api.problems.map((p) => <div key={p.path} className="outline-problem">{p.path}</div>)}
        </div>
      )}
    </>
  );
}

interface PageProps { model: Model; id: string; sel: string | null; open(id: string): void }

function Page({ model, id, sel, open }: PageProps) {
  const striving = model.striving(id);
  return <div className="page">{striving ? <StrivingPage model={model} s={striving} open={open} />
    : <ActivityPage model={model} a={model.activity(id)!} sel={sel} />}</div>;
}

function StrivingPage({ model, s, open }: { model: Model; s: Striving; open(id: string): void }) {
  const activities = model.activitiesOf(s.id);
  const against = model.activities.filter((a) => a.hinders.includes(s.id));
  const card = (a: Activity) => (
    <button type="button" key={a.id} className={`page-card${a.status === "tentative" ? " tentative" : ""}`} onClick={() => open(a.id)}>
      <b>{a.title}</b>
      <span>{a.status} · {plural(model.actionsOf(a.id).length, "action")} · {span(a.first_seen, a.last_seen)}</span>
    </button>
  );
  return (
    <>
      <LevelLabel kind="striving" id={s.id} />
      <h1 className="page-title">{s.title}</h1>
      <div className="byline">
        <Confidence value={s.confidence} />
        <span>{s.needs.map((n) => n.replace(/_/g, " ")).join(" · ")}</span>
        <span>{s.orientation}, {s.aspiration_vs_obligation}, {s.autonomy}</span>
      </div>
      <When model={model} actions={activities.flatMap((a) => model.actionsOf(a.id))} />
      <RichText model={model} text={s.body} />
      <section className="page-below">
        <h3 className="eyebrow">Activities serving it</h3>
        {activities.length ? activities.map(card) : <span className="muted">None yet.</span>}
        {against.length > 0 && <h3 className="eyebrow page-below-gap">Working against it</h3>}
        {against.map(card)}
      </section>
    </>
  );
}

function ActivityPage({ model, a, sel }: { model: Model; a: Activity; sel: string | null }) {
  const { select } = useSelection();
  const actions = model.actionsOf(a.id);
  let last = "";
  return (
    <>
      <LevelLabel kind="activity" id={a.id} />
      <h1 className="page-title">{a.title}</h1>
      <div className="byline">
        <Confidence value={a.confidence} />
        <Status status={a.status} />
        <span>{span(a.first_seen, a.last_seen)}</span>
        <span className="byline-links">
          {a.part_of.length ? "serves" : "serves no striving yet"}
          {a.part_of.map((s) => (
            <button type="button" key={s} className="byline-link" onClick={() => select(s)}><Dot kind="striving" />{short(model.title(s), 44)}</button>
          ))}
        </span>
      </div>
      <When model={model} actions={actions} />
      <RichText model={model} text={a.body} />
      <section className="page-below">
        <h3 className="eyebrow">{plural(actions.length, "action")}</h3>
        <div className="timeline">
          {actions.map((x) => {
            const d = day(x.start);
            const head = d !== last;
            last = d;
            return (
              <Fragment key={x.id}>
                {head && <div className="timeline-day">{dayLabel(d)}</div>}
                <button type="button" className={`timeline-row${sel === x.id ? " on" : ""}`} onClick={() => select(x.id)}>
                  <span className="time">{clock(x.start)}</span>
                  <span className="timeline-text">{x.summary}</span>
                  <span className="timeline-len">{minutes(x.start, x.end)} min</span>
                </button>
              </Fragment>
            );
          })}
        </div>
      </section>
    </>
  );
}

/** When the page's actions happened: a column per day of the model, a tick per action at its time of day. */
function When({ model, actions }: { model: Model; actions: Action[] }) {
  const { sel, select } = useSelection();
  if (!actions.length) return null;
  const days = model.days;
  return (
    <div className="when" aria-label="When its actions happened">
      {days.map((d) => (
        <div className="when-day" key={d}>
          <div className="when-track">
            {actions.filter((a) => day(a.start) === d).map((a) => {
              const h = Number(a.start.slice(11, 13)) + Number(a.start.slice(14, 16)) / 60;
              return (
                <button type="button" key={a.id} className={`when-tick${sel === a.id ? " on" : ""}`}
                  style={{ left: `${(h / 24) * 100}%` }} title={`${clock(a.start)} · ${a.summary}`}
                  aria-label={`${clock(a.start)} ${a.summary}`} onClick={() => select(a.id)} />
              );
            })}
          </div>
          <div className="when-label">{dayLabel(d)}</div>
        </div>
      ))}
    </div>
  );
}
