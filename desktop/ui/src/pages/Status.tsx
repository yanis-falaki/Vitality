// The Status page (spec §7): what capture is doing now in the terms of the rules (a pause first
// and loud, with Resume), today's shots and interactions, upload and its pending interactions,
// disk use, warnings from the health checks, and the recent decisions, skipped shots included.
import type { StatusPage, UploadStatus } from "../core";
import { usePauseResume } from "../components/CaptureState";
import { captureLine } from "../lib/capture";
import { bytes, clock, plural } from "../lib/format";
import { useNow } from "../lib/now";
import { useStatusPage } from "../lib/queries";

function uploadLine(u: UploadStatus): { text: string; tone: string } {
  switch (u.state) {
    case "ok":
      return { text: u.pending ? `Uploading · ${u.pending} pending` : "Up to date", tone: "ok" };
    case "offline":
      return { text: `Server unreachable · ${u.pending} pending, retrying`, tone: "warn" };
    case "rejected":
      return { text: `Refused: allow the device “${u.device}” on the server`, tone: "bad" };
    case "disabled":
      return { text: `Off · no server set, ${u.pending} kept here`, tone: "off" };
  }
}

export function Status() {
  const page = useStatusPage();
  const now = useNow();
  const change = usePauseResume();
  if (!page.data) return <div className="page-inner" />;
  const { status, today, disk, checks, recent_decisions } = page.data as StatusPage;
  const line = captureLine(status, now);
  const upload = uploadLine(status.upload);
  const failing = checks.filter((c) => !c.ok);
  return (
    <div className="page-inner">
      <header className="page-head">
        <div>
          <h1>Status</h1>
          <p className="lede">What Vitality is doing now, and why.</p>
        </div>
      </header>

      <section className={`now now-${line.tone}`} aria-live="polite">
        <div>
          <p className="now-line"><span className={`dot tone-${line.tone}`} aria-hidden />{line.text}</p>
          <p className="now-detail">{line.detail}</p>
        </div>
        {status.pause.paused ? (
          <button className="button button-primary" disabled={change.isPending} onClick={() => change.mutate("resume")}>Resume</button>
        ) : (
          <button className="button" disabled={change.isPending} onClick={() => change.mutate(30)}>Pause 30 min</button>
        )}
      </section>

      <div className="tiles">
        <div className="tile">
          <h2>Today</h2>
          <p className="tile-number">{today.shots.toLocaleString("en")}</p>
          <p className="tile-detail">{plural(today.shots, "shot").replace(/^\S+ /, "")} in {plural(today.interactions, "interaction")}</p>
        </div>
        <div className="tile">
          <h2>Upload</h2>
          <p className={`tile-state tone-${upload.tone}`}><span className={`dot tone-${upload.tone}`} aria-hidden />{upload.text}</p>
          <p className="tile-detail">
            {status.upload.last_upload ? `Last upload ${clock(status.upload.last_upload)}` : "Nothing uploaded since the app started"}
          </p>
          {status.upload.failed > 0 && (
            <p className="tile-warning">
              {plural(status.upload.failed, "interaction")} refused for good
              {status.upload.last_failure ? ` · latest: ${status.upload.last_failure}` : ""}
            </p>
          )}
        </div>
        <div className="tile">
          <h2>Disk</h2>
          <p className="tile-number">{bytes(disk.captures_bytes)}</p>
          <p className={`tile-detail${disk.low ? " tile-warning" : ""}`}>{bytes(disk.free_bytes)} free{disk.low ? " · running low" : ""}</p>
        </div>
      </div>

      {failing.length > 0 && (
        <section className="section">
          <h2 className="section-title">Needs attention</h2>
          <ul className="checks">
            {failing.map((c) => (
              <li key={c.id} className="check">
                <span className={`dot tone-${c.essential ? "bad" : "warn"}`} aria-hidden />
                <div>
                  <p className="check-title">{c.title} <span className="check-detail">· {c.detail}</span></p>
                  {c.fix && <p className="check-fix">{c.fix}</p>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section">
        <h2 className="section-title">Recent decisions</h2>
        {recent_decisions.length === 0 ? (
          <p className="note">Nothing yet.</p>
        ) : (
          <ol className="decisions">
            {[...recent_decisions].reverse().map((d, i) => (
              <li key={i} className={d.text.includes(" skipped") ? "skipped" : d.text.startsWith("error") ? "error" : ""}>
                <time>{clock(d.at)}</time>
                <span>{d.text}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
