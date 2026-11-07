// Capture's state in a line ("Recording", "Paused until 9:00 p.m.") with its dot, and the
// buttons to pause for 30 min or 1 h, or to resume. Reads `status`; calls `pause` / `resume`.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { core, type StatusView } from "../core";
import { clock, plural } from "../lib/format";
import { keys, useStatus } from "../lib/queries";

function line(s: StatusView): { text: string; tone: string } {
  if (s.pause.paused) return { text: s.pause.until ? `Paused until ${clock(s.pause.until)}` : "Paused", tone: "off" };
  switch (s.capture.mode) {
    case "active":
      return { text: `Active since ${clock(s.capture.since!)} · ${plural(s.capture.shots, "shot")}`, tone: "busy" };
    case "passive":
      return { text: `Watching since ${clock(s.capture.since!)} · ${plural(s.capture.shots, "shot")}`, tone: "busy" };
    case "stopped":
      return { text: s.capture.stopped_by === "locked" ? "Stopped · locked" : "Stopped · asleep", tone: "off" };
    default:
      return { text: "Recording · idle", tone: "ok" };
  }
}

export function CaptureState() {
  const status = useStatus();
  const client = useQueryClient();
  const change = useMutation({
    mutationFn: (minutes: number | null | "resume") => (minutes === "resume" ? core.resume() : core.pause(minutes)),
    onSuccess: (s) => client.setQueryData(keys.status, s),
  });
  if (!status.data) return <div className="capture" />;
  const { text, tone } = line(status.data);
  return (
    <div className="capture">
      <p className="capture-line"><span className={`dot tone-${tone}`} aria-hidden />{text}</p>
      <div className="capture-actions">
        {status.data.pause.paused ? (
          <button className="button button-wide" disabled={change.isPending} onClick={() => change.mutate("resume")}>Resume</button>
        ) : (
          <>
            <span className="label">Pause for</span>
            <button className="button" disabled={change.isPending} onClick={() => change.mutate(30)}>30 min</button>
            <button className="button" disabled={change.isPending} onClick={() => change.mutate(60)}>1 h</button>
          </>
        )}
      </div>
    </div>
  );
}
