// Home's status line: whether the pipeline is working, from the newest capture to the modeler's state.
// Something stuck reads in the muted warn tone, never as an alarm: no capture for an hour though there
// were captures earlier today, the observer an hour behind the captures, a modeler paused or not running.
import { Fragment } from "react";
import { useModeler, usePipeline } from "../../api/client";
import { ago, atOffset, type Now } from "../../lib/now";

const HOUR = 3_600_000;
const STUCK_STATES: Record<string, string> = {
  paused: "paused", "waiting-limit": "waiting for its usage limit", "waiting-edits": "waiting for uncommitted edits",
  "not running": "not running",
};

interface Part { text: string; warn?: string }   // `warn`: why it reads as stuck (its title)

export function PipelineLine({ now }: { now: Now }) {
  const pipeline = usePipeline().data;
  const modeler = useModeler().data;
  if (!pipeline) return <p className="home-status" />;
  const offset = now.local.slice(19);
  const parts: Part[] = [];

  const capture = pipeline.last_capture ? Date.parse(pipeline.last_capture) : null;
  if (capture === null) parts.push({ text: "no captures yet" });
  else {
    const today = atOffset(capture, offset).slice(0, 10) === now.today;
    parts.push({
      text: `captured ${ago(capture, now.ms)}`,
      warn: today && now.ms - capture > HOUR ? "No capture for over an hour, though there were captures earlier today" : undefined,
    });
  }
  const observed = pipeline.last_observation ? Date.parse(pipeline.last_observation) : null;
  if (observed !== null) {
    parts.push({
      text: `observed ${ago(observed, now.ms)}`,
      warn: capture !== null && capture - observed > HOUR ? "The observer is over an hour behind the captures" : undefined,
    });
  }
  if (modeler) {
    const last = [...modeler.sessions].reverse().find((s) => s.outcome === "committed");
    if (last) parts.push({ text: `model updated ${ago(Date.parse(last.ended), now.ms)}` });
    if (modeler.waiting) parts.push({ text: `${modeler.waiting} operations waiting` });
    const state = modeler.status.state;
    parts.push({ text: `modeler ${STUCK_STATES[state] ?? state}`, warn: STUCK_STATES[state] ? `The modeler is ${STUCK_STATES[state]}` : undefined });
  }
  const stuck = parts.filter((p) => p.warn);
  return (
    <p className="home-status" data-testid="home-status" title={stuck.map((p) => p.warn).join("; ") || undefined}>
      <i className={`mdot tone-${stuck.length ? "warn" : "ok"}`} aria-hidden />
      <span>
        {parts.map((p, i) => (
          <Fragment key={p.text}>
            <span className={p.warn ? "home-status-part warn" : "home-status-part"}>{p.text}{i < parts.length - 1 && " ·"}</span>
            {" "}
          </Fragment>
        ))}
      </span>
    </p>
  );
}
