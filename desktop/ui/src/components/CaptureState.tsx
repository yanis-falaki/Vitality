// The sidebar's foot: capture's state in a line with its dot (paused in the warning colour, so it
// cannot be missed), warnings about screens and refused uploads, and Pause for 30 min / 1 h or
// Resume. Reads `status`; calls `pause` / `resume`.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { core } from "../core";
import { captureLine } from "../lib/capture";
import { keys, useStatus } from "../lib/queries";
import { useNow } from "../lib/now";

export function usePauseResume() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (minutes: number | null | "resume") => (minutes === "resume" ? core.resume() : core.pause(minutes)),
    onSuccess: (s) => {
      client.setQueryData(keys.status, s);
      void client.invalidateQueries({ queryKey: keys.statusPage });
    },
  });
}

export function CaptureState() {
  const status = useStatus();
  const change = usePauseResume();
  const now = useNow();
  if (!status.data) return <div className="capture" />;
  const s = status.data;
  const { text, tone } = captureLine(s, now);
  return (
    <div className={`capture${s.pause.paused ? " capture-paused" : ""}`}>
      <p className="capture-line"><span className={`dot tone-${tone}`} aria-hidden />{text}</p>
      {s.screens_attention.length > 0 && <p className="capture-warning">{s.screens_attention.join(", ")}: see Screens</p>}
      {s.upload.failed > 0 && (
        <p className="capture-warning" title={s.upload.last_failure ?? undefined}>
          {s.upload.failed === 1 ? "1 interaction" : `${s.upload.failed} interactions`} refused by the server for good
        </p>
      )}
      <div className="capture-actions">
        {s.pause.paused ? (
          <button className="button button-primary button-wide" disabled={change.isPending} onClick={() => change.mutate("resume")}>Resume</button>
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
