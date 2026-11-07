// "What changed in the model": the modeler's commits, newest first, each under its session's time, in
// plain words: per belief one line (its title, which opens it in the Reader, and what happened to it),
// then "+N actions". Sessions newer than the owner's last visit to Home carry a quiet "new". The feed
// scrolls inside its own box ("Show more" loads into it): beside the day and the week on a desktop, as
// tall as they are; on a tablet or phone, at most about 60% of the window.
import { Link } from "react-router-dom";
import { useChanges, type ChangeItem, type ModelerCommit } from "../../api/client";
import { Dot } from "../../components/bits";
import { clock, day, dayLabel, plural } from "../../lib/format";
import type { Model } from "../../lib/model";

export function Changes({ model, lastVisit }: { model: Model; lastVisit: number | null }) {
  const query = useChanges(6);
  const commits = query.data?.pages.flatMap((p) => p.commits) ?? [];
  return (
    <section className="home-section home-changes" aria-labelledby="home-changes">
      <div className="home-section-head">
        <h2 className="eyebrow" id="home-changes">What changed in the model</h2>
      </div>
      <div className="home-feed-box" data-testid="home-feed-box">
        {query.isLoading && <p className="home-quiet">Reading the modeler's history…</p>}
        {query.isError && <p className="home-quiet">The model's history could not be read.</p>}
        {query.isSuccess && commits.length === 0 && <p className="home-quiet">The modeler has not changed the model yet.</p>}
        <ol className="home-feed" data-testid="home-changes">
          {commits.map((c) => (
            <Session key={c.commit} model={model} commit={c} fresh={lastVisit !== null && Date.parse(c.time) > lastVisit} />
          ))}
        </ol>
        {query.hasNextPage && (
          <button type="button" className="button home-show-more" disabled={query.isFetchingNextPage}
            onClick={() => query.fetchNextPage()}>
            {query.isFetchingNextPage ? "Reading…" : "Show more"}
          </button>
        )}
      </div>
    </section>
  );
}

function Session({ model, commit, fresh }: { model: Model; commit: ModelerCommit; fresh: boolean }) {
  const beliefs = byBelief(commit.items);
  const added = commit.actions_added, revised = commit.actions_changed;
  return (
    <li className={`home-session${fresh ? " fresh" : ""}`}>
      <h3 className="home-session-head">
        <span>{dayLabel(day(commit.time))}, {clock(commit.time)} · session</span>
        {fresh && <span className="home-new">new</span>}
      </h3>
      <ul className="home-items">
        {beliefs.map((items) => <Belief key={items[0].id} model={model} items={items} />)}
        {(added > 0 || revised > 0 || beliefs.length === 0) && (
          <li className="home-item-actions">
            {added || revised
              ? [added ? `+${plural(added, "action")}` : "", revised ? `${plural(revised, "action")} revised` : ""].filter(Boolean).join(" · ")
              : "No change to the strivings, activities or actions"}
          </li>
        )}
      </ul>
    </li>
  );
}

/** One belief's changes in a session, on one line: its title, then what happened, in plain words. */
function Belief({ model, items }: { model: Model; items: ChangeItem[] }) {
  const first = items[0];
  const exists = model.kind(first.id) === first.level;
  const tentative = first.level === "activity" && model.activity(first.id)?.status === "tentative";
  const title = exists ? model.title(first.id) : first.title;
  const body = (
    <>
      <Dot kind={first.level} tentative={tentative} />
      <span className="home-item-text">
        <span className="home-item-title">{title}</span>
        <span className="home-item-what">{items.map((i) => words(model, i)).join(" · ")}</span>
      </span>
    </>
  );
  return (
    <li>
      {exists ? (
        <Link className="home-item" to={`/reader?page=${encodeURIComponent(first.id)}`} data-id={first.id}>{body}</Link>
      ) : (
        <span className="home-item gone">{body}</span>
      )}
    </li>
  );
}

// The items of a commit grouped by belief, in their order.
function byBelief(items: ChangeItem[]): ChangeItem[][] {
  const groups = new Map<string, ChangeItem[]>();
  for (const i of items) groups.set(i.id, [...(groups.get(i.id) ?? []), i]);
  return [...groups.values()];
}

function words(model: Model, i: ChangeItem): string {
  const name = (id: string | null | undefined) => (id ? `“${model.kind(id) ? model.title(id) : id}”` : "");
  switch (i.kind) {
    case "created": return `new ${i.level}`;
    case "retitled": return `retitled from “${i.before}”`;
    case "status": return `became ${i.after}`;
    case "confidence": return `confidence ${i.before} → ${i.after}`;
    case "serves": return `now serves ${name(i.other)}`;
    case "unserves": return `no longer serves ${name(i.other)}`;
    case "removed": return i.other ? `merged into ${name(i.other)}` : "removed";
  }
}
