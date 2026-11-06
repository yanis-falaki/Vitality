// Small shared pieces: the level dot and label, confidence, status, a chip that selects, a section.
import type { ReactNode } from "react";
import { LEVEL, type Kind, type Model } from "../lib/model";
import { useSelection } from "../lib/selection";

/** A dot in the level's colour. */
export function Dot({ kind, tentative = false }: { kind: Kind; tentative?: boolean }) {
  return <i className={`dot dot-${kind}${tentative ? " tentative" : ""}`} aria-hidden />;
}

/** "● ACTIVITY · act-0001": the level in small caps, with the id. */
export function LevelLabel({ kind, id }: { kind: Kind; id?: string }) {
  return (
    <div className={`level level-${kind}`}>
      <Dot kind={kind} />
      {LEVEL[kind].label}
      {id && <span className="level-id">{id}</span>}
    </div>
  );
}

/** Confidence out of 10, as dots. */
export function Confidence({ value }: { value: number }) {
  return (
    <span className="confidence" title={`confidence ${value}/10`} aria-label={`confidence ${value} of 10`}>
      {Array.from({ length: 10 }, (_, i) => <i key={i} className={i < value ? "on" : ""} />)}
    </span>
  );
}

/** Tentative (dashed) or established (solid): drawn the same way everywhere. */
export function Status({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status}</span>;
}

/** A pill naming a striving, activity, action or interaction; selects it. */
export function Chip({ model, id }: { model: Model; id: string }) {
  const { select } = useSelection();
  const kind = model.kind(id);
  const tentative = kind === "activity" && model.activity(id)?.status === "tentative";
  return (
    <button type="button" className={`chip${tentative ? " tentative" : ""}`} onClick={() => select(id)}
      title={model.title(id)}>
      {kind && <Dot kind={kind} tentative={tentative} />}
      <span className="chip-text">{model.title(id)}</span>
    </button>
  );
}

/** A titled block of the inspector or a page. */
export function Section({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <h3 className="eyebrow">{title}</h3>
      <div className="section-body">{children}</div>
    </section>
  );
}

/** Text in muted ink. */
export const Muted = ({ children }: { children: ReactNode }) => <span className="muted">{children}</span>;
