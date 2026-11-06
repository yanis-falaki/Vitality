// Reader: the model as pages to read (web spec, §6, §10). An outline of the strivings (each folds
// away), their activities most recently seen first and the older ones behind a toggle, then the
// activities serving none; the page of the one chosen (`?page=<id>`): serif title, byline, a heatmap
// of when its actions happened, its body, then its activities or its actions (by day, or by month
// and week when there are many). References and actions open in the inspector. On a phone the
// outline is a drawer.
import { Fragment, useEffect, useState } from "react";
import type { Action, Activity, Striving } from "../api/client";
import { ActionGroups, actionsTitle } from "../components/ActionGroups";
import { Confidence, Dot, LevelLabel, Status } from "../components/bits";
import { Heatmap } from "../components/Heatmap";
import { span } from "../components/Inspector";
import { WithModel } from "../components/Loading";
import { RichText } from "../components/RichText";
import { ViewFrame } from "../components/Shell";
import { clock, dayLabel, minutes, plural, short } from "../lib/format";
import { useLayout } from "../lib/media";
import { byRecency, type Model } from "../lib/model";
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
  // Strivings fold away (the one whose page is open stays unfolded); older activities wait behind a toggle.
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [older, setOlder] = useState<Set<string>>(new Set());
  const flip = (set: Set<string>, id: string) => { const next = new Set(set); if (!next.delete(id)) next.add(id); return next; };
  const item = (a: Activity, quiet = false) => (
    <button type="button" key={a.id} className={`outline-activity${a.status === "tentative" ? " tentative" : ""}${quiet ? " older" : ""}${page === a.id ? " on" : ""}`}
      onClick={() => open(a.id)}>{a.title}</button>
  );
  const list = (group: string, activities: Activity[]) => {
    const { recent, older: old } = byRecency(model, activities);
    const shown = older.has(group) || old.some((a) => a.id === page);
    return (
      <>
        {recent.map((a) => item(a))}
        {old.length > 0 && (
          <button type="button" className="outline-older" aria-expanded={shown} onClick={() => setOlder((o) => flip(o, group))}>
            <span className="caret" aria-hidden>{shown ? "▾" : "▸"}</span>Older · {old.length}
          </button>
        )}
        {shown && old.map((a) => item(a, true))}
      </>
    );
  };
  return (
    <>
      <div className="eyebrow outline-head">Strivings</div>
      {model.strivings.map((s) => {
        const activities = model.activitiesOf(s.id);
        const unfolded = !folded.has(s.id) || activities.some((a) => a.id === page);
        return (
          <Fragment key={s.id}>
            <div className="outline-row">
              <button type="button" className={`outline-striving${page === s.id ? " on" : ""}`} onClick={() => open(s.id)}>{s.title}</button>
              {activities.length > 0 && (
                <button type="button" className="outline-fold" aria-expanded={unfolded}
                  aria-label={`${unfolded ? "Fold" : "Unfold"} its ${plural(activities.length, "activity", "activities")}`}
                  onClick={() => setFolded((f) => flip(f, s.id))}>
                  {unfolded ? "▾" : <>{activities.length} ▸</>}
                </button>
              )}
            </div>
            {unfolded && list(s.id, activities)}
          </Fragment>
        );
      })}
      {model.unassigned.length > 0 && <div className="eyebrow outline-head">Not yet serving a striving</div>}
      {list("none", model.unassigned)}
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
  const { recent, older } = byRecency(model, activities);
  const against = byRecency(model, model.activities.filter((a) => a.hinders.includes(s.id)));
  const card = (a: Activity) => (
    <button type="button" key={a.id} className={`page-card${a.status === "tentative" ? " tentative" : ""}${model.stale(a) ? " older" : ""}`}
      onClick={() => open(a.id)}>
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
      <Heatmap model={model} actions={strivingActions(model, activities)} level="striving" />
      <RichText model={model} text={s.body} />
      <section className="page-below">
        <h3 className="eyebrow">Activities serving it</h3>
        {activities.length ? recent.map(card) : <span className="muted">None yet.</span>}
        {older.length > 0 && <h3 className="eyebrow page-below-gap">Older · not seen for over a month</h3>}
        {older.map(card)}
        {against.recent.length + against.older.length > 0 && <h3 className="eyebrow page-below-gap">Working against it</h3>}
        {[...against.recent, ...against.older].map(card)}
      </section>
    </>
  );
}

function ActivityPage({ model, a, sel }: { model: Model; a: Activity; sel: string | null }) {
  const { select } = useSelection();
  const actions = model.actionsOf(a.id);
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
      <Heatmap model={model} actions={actions} level="activity" />
      <RichText model={model} text={a.body} />
      <section className="page-below">
        <h3 className="eyebrow">{actionsTitle(actions)}</h3>
        <ActionGroups actions={actions} className="timeline"
          dayHead={(d) => <div className="timeline-day" key={`day-${d}`}>{dayLabel(d)}</div>}
          row={(x) => (
            <button type="button" key={x.id} className={`timeline-row${sel === x.id ? " on" : ""}`} onClick={() => select(x.id)}>
              <span className="time">{clock(x.start)}</span>
              <span className="timeline-text">{x.summary}</span>
              <span className="timeline-len">{minutes(x.start, x.end)} min</span>
            </button>
          )} />
      </section>
    </>
  );
}

// The actions of a striving's activities, once each (an action may serve two of them), by start.
function strivingActions(model: Model, activities: Activity[]): Action[] {
  const ids = new Set(activities.map((a) => a.id));
  return model.actions.filter((x) => x.part_of.some((p) => ids.has(p)));
}
