// One screen's card on the Screens page: live preview, name (renamed in place), its state
// ("Live · last frame 2 s ago", "Needs to be picked again", "Opening…"), Re-pick and Remove
// (with an inline confirmation). Calls `onRename`, `onRepick`, `onRemove`.
import { useState } from "react";
import type { ScreenStatus } from "../core";
import { frameAge } from "../lib/format";
import { NameEdit } from "./NameEdit";
import { Preview } from "./Preview";

/** A feed this quiet is probably stuck. */
const STALE_S = 10;

function stateLine(s: ScreenStatus): { text: string; tone: string } {
  if (s.state === "needs_pick") return { text: "Needs to be picked again", tone: "warn" };
  if (s.state === "opening") return { text: "Opening…", tone: "off" };
  if (s.last_frame_age_s === null) return { text: "Live · waiting for the first frame", tone: "ok" };
  const stale = s.last_frame_age_s > STALE_S;
  return { text: `Live · ${frameAge(s.last_frame_age_s)}`, tone: stale ? "warn" : "ok" };
}

interface Props {
  screen: ScreenStatus;
  busy: boolean;
  onRename: (name: string) => void;
  onRepick: () => void;
  onRemove: () => void;
}

export function ScreenCard({ screen, busy, onRename, onRepick, onRemove }: Props) {
  const [confirming, setConfirming] = useState(false);
  const { text, tone } = stateLine(screen);
  const needsPick = screen.state === "needs_pick";
  return (
    <article className={`card${needsPick ? " card-attention" : ""}`}>
      <Preview
        id={screen.id}
        live={screen.state === "live"}
        empty={needsPick ? "The screen-share permission was refused" : screen.state === "opening" ? "Opening…" : "No frame yet"}
      />
      <div className="card-body">
        <NameEdit name={screen.name} onSave={onRename} />
        <p className={`state tone-${tone}`}><span className={`dot tone-${tone}`} aria-hidden />{text}</p>
      </div>
      <div className="card-actions">
        {confirming ? (
          <>
            <span className="confirm">Remove {screen.name}?</span>
            <button className="button button-danger" onClick={onRemove}>Remove</button>
            <button className="button" onClick={() => setConfirming(false)}>Keep</button>
          </>
        ) : (
          <>
            <button className={`button${needsPick ? " button-primary" : ""}`} disabled={busy} onClick={onRepick}>
              {needsPick ? "Pick again" : "Re-pick"}
            </button>
            <button className="button button-quiet" disabled={busy} onClick={() => setConfirming(true)}>Remove</button>
          </>
        )}
      </div>
    </article>
  );
}
