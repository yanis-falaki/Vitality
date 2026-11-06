// The inspector: one panel, the same in every view, showing whatever is selected (web spec, §6).
//
// For each level: its label and id, title, confidence, status and time span; its links as chips;
// its body; then what lies beneath it (a striving's activities, an activity's actions by day, an
// action's evidence, an interaction's actions and evidence).
import { Fragment, type ReactNode } from "react";
import { useAction, useEvidence, type Action, type Activity, type Striving } from "../api/client";
import { clock, day, dayLabel, minutes, plural } from "../lib/format";
import type { Model } from "../lib/model";
import { useSelection } from "../lib/selection";
import { Chip, Confidence, LevelLabel, Muted, Section, Status } from "./bits";
import { EvidenceCard } from "./Evidence";
import { RichText } from "./RichText";

interface Props {
  model: Model;
  id: string | null;
  empty?: ReactNode;          // what to say when nothing is selected
  onClose?: () => void;       // shown as a close control (phone and tablet)
  closeLabel?: string;
}

export function Inspector({ model, id, empty, onClose, closeLabel = "Close" }: Props) {
  const kind = id ? model.kind(id) : undefined;
  let body: ReactNode;
  if (!id || !kind) {
    body = <div className="inspector-empty">{empty ?? "Select something to see what the model believes, and why."}</div>;
  } else if (kind === "striving") body = <StrivingPanel model={model} s={model.striving(id)!} />;
  else if (kind === "activity") body = <ActivityPanel model={model} a={model.activity(id)!} />;
  else if (kind === "action") body = <ActionPanel model={model} a={model.action(id)!} />;
  else body = <InteractionPanel model={model} name={id} />;
  return (
    <div className="inspector" data-testid="inspector">
      {onClose && (
        <div className="inspector-close">
          <button type="button" className="button" onClick={onClose} aria-label="Close the inspector">{closeLabel}</button>
        </div>
      )}
      <div className="inspector-body" key={id ?? ""}>{body}</div>
    </div>
  );
}

function StrivingPanel({ model, s }: { model: Model; s: Striving }) {
  const r = s.relations;
  return (
    <>
      <LevelLabel kind="striving" id={s.id} />
      <h2 className="inspector-title">{s.title}</h2>
      <div className="meta">
        <Confidence value={s.confidence} />
        <span>{s.orientation}</span><span>{s.aspiration_vs_obligation}</span><span>{s.autonomy}</span>
      </div>
      <div className="tags">{s.needs.map((n) => <span className="tag" key={n}>{n.replace(/_/g, " ")}</span>)}</div>
      <RichText model={model} text={s.body} />
      <Section title="Activities serving it">
        {model.activitiesOf(s.id).map((a) => <Chip key={a.id} model={model} id={a.id} />)}
        {model.activitiesOf(s.id).length === 0 && <Muted>None yet</Muted>}
      </Section>
      {([["Conflicts with", r.conflicts_with], ["Facilitates", r.facilitates], ["Serves", r.serves]] as const)
        .filter(([, ids]) => ids.length > 0)
        .map(([title, ids]) => (
          <Section key={title} title={title}>{ids.map((x) => <Chip key={x} model={model} id={x} />)}</Section>
        ))}
    </>
  );
}

function ActivityPanel({ model, a }: { model: Model; a: Activity }) {
  const actions = model.actionsOf(a.id);
  return (
    <>
      <LevelLabel kind="activity" id={a.id} />
      <h2 className="inspector-title">{a.title}</h2>
      <div className="meta">
        <Confidence value={a.confidence} />
        <Status status={a.status} />
        <span>{span(a.first_seen, a.last_seen)}</span>
      </div>
      <Section title="Serves">
        {a.part_of.map((s) => <Chip key={s} model={model} id={s} />)}
        {a.part_of.length === 0 && <Muted>No striving yet</Muted>}
      </Section>
      {a.hinders.length > 0 && (
        <Section title="Works against">{a.hinders.map((s) => <Chip key={s} model={model} id={s} />)}</Section>
      )}
      <RichText model={model} text={a.body} />
      <Section title={plural(actions.length, "action")}><ActionsByDay actions={actions} /></Section>
    </>
  );
}

function ActionPanel({ model, a }: { model: Model; a: Action }) {
  const interactions = model.interactionsOf(a);
  const detail = useAction(a.id).data;     // the body and the operations it cites
  const operations = detail?.operations;
  return (
    <>
      <LevelLabel kind="action" id={a.id} />
      <h2 className="inspector-title">{a.summary}</h2>
      <div className="meta">
        <Confidence value={a.confidence} />
        <span>{dayLabel(day(a.start))}, {clock(a.start)} – {clock(a.end)} · {minutes(a.start, a.end)} min</span>
      </div>
      <Section title="Part of">
        {a.part_of.map((x) => <Chip key={x} model={model} id={x} />)}
        {a.part_of.length === 0 && <Muted>No activity yet</Muted>}
      </Section>
      {detail ? <RichText model={model} text={detail.body} /> : <div className="prose-pending" aria-hidden />}
      <Section title={`Evidence · ${plural(interactions.length, "interaction")}${operations ? `, ${plural(operations.length, "operation")}` : ""}`}>
        <div className="stack">
          {interactions.map((i) => (
            <EvidenceCard key={i} model={model} interaction={i}
              cited={operations && new Set(operations.filter((r) => r.startsWith(`${i}#`)).map((r) => r.split("#")[1]))} />
          ))}
        </div>
      </Section>
    </>
  );
}

function InteractionPanel({ model, name }: { model: Model; name: string }) {
  const evidence = useEvidence(name).data;
  const start = evidence?.start ?? model.interactionStart(name);
  const citing = model.actionsCiting(name);
  return (
    <>
      <LevelLabel kind="interaction" id={name} />
      <h2 className="inspector-title">
        {start ? `${dayLabel(day(start))}, ${clock(start)}${evidence ? ` – ${clock(evidence.end)}` : ""}` : name}
      </h2>
      <Section title="In actions">
        {citing.map((a) => <Chip key={a.id} model={model} id={a.id} />)}
        {citing.length === 0 && <Muted>Not modeled yet</Muted>}
      </Section>
      <EvidenceCard model={model} interaction={name} showObservation />
    </>
  );
}

/** Actions under their day, as rows that select. */
export function ActionsByDay({ actions }: { actions: Action[] }) {
  const { sel, select } = useSelection();
  let last = "";
  return (
    <div className="rows">
      {actions.map((a) => {
        const d = day(a.start);
        const head = d !== last;
        last = d;
        return (
          <Fragment key={a.id}>
            {head && <div className="row-day">{dayLabel(d)}</div>}
            <button type="button" className={`row${sel === a.id ? " on" : ""}`} onClick={() => select(a.id)}>
              <span className="time">{clock(a.start)}</span>
              <span className="row-text">{a.summary}</span>
            </button>
          </Fragment>
        );
      })}
    </div>
  );
}

/** "Mon, Nov 2 – Wed, Nov 4", or one day. */
export function span(first: string, last: string): string {
  return day(first) === day(last) ? dayLabel(day(first)) : `${dayLabel(day(first))} – ${dayLabel(day(last))}`;
}
