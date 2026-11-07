// One interaction's evidence, as a card: its time, its operations (those `cited` by the action in
// view emphasised, the others dimmed), thumbnails that open the lightbox (each marked with its screen's
// name when there are several), and the observation behind a disclosure. Fetched when the card is shown.
import { useState } from "react";
import { shotUrl, useEvidence, type Shot } from "../api/client";
import { clock, minutes, plural } from "../lib/format";
import type { Model } from "../lib/model";
import { allPassive, shotLabel } from "../lib/shots";
import { RichText } from "./RichText";
import { useLightbox } from "./Lightbox";

interface Props {
  model: Model;
  interaction: string;
  cited?: Set<string>;      // operation ids the action cites; all are emphasised when absent
  showObservation?: boolean;
}

export function EvidenceCard({ model, interaction, cited, showObservation = false }: Props) {
  const { data, isLoading } = useEvidence(interaction);
  const { show } = useLightbox();
  const [observation, setObservation] = useState(showObservation);
  const start = data?.start ?? model.interactionStart(interaction);
  const end = data?.end;
  const shots = data?.shots ?? [];
  const urls = shots.map((s) => shotUrl(interaction, s.file));
  const captions = shots.map(shotLabel);
  return (
    <div className="evidence">
      <div className="evidence-head">
        <span className="time">{start ? clock(start) : "—"}</span>
        <span>
          {data ? `${plural(data.operations.length, "operation")} · ${plural(urls.length, "screenshot")}`
            : isLoading ? "Loading…" : "Not found"}
          {start && end && <span className="muted"> · {minutes(start, end)} min</span>}
          {allPassive(shots) && <span className="passive-tag" title="Taken while no one used the device">passive</span>}
        </span>
      </div>
      {data && (
        <>
          <ul className="ops">
            {data.operations.map((op) => (
              <li key={op.id} className={cited && !cited.has(op.id) ? "dim" : ""}>{op.text}</li>
            ))}
          </ul>
          {urls.length > 0 && (
            <div className="thumbs">
              {urls.map((url, i) => (
                <button type="button" key={url} className="thumb" onClick={() => show(urls, i, captions)}
                  aria-label={`Screenshot ${i + 1}${captions[i] ? `, ${captions[i]}` : ""}`}>
                  <img src={url} alt="" loading="lazy" />
                  <ShotBadge shot={shots[i]} />
                </button>
              ))}
            </div>
          )}
          {data.observation && (
            <div className="disclosure">
              <button type="button" className="disclosure-toggle" aria-expanded={observation}
                onClick={() => setObservation((o) => !o)}>
                <span className="caret">{observation ? "▾" : "▸"}</span> Observation
              </button>
              {observation && <RichText model={model} text={data.observation.body} small />}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** A shot's screen name in a corner of its thumbnail (none for a single screen); `brief` cuts a long name. */
export function ShotBadge({ shot, brief = false }: { shot: Shot; brief?: boolean }) {
  if (shot.screen_name == null) return null;
  const name = brief && shot.screen_name.length > 10 ? `${shot.screen_name.slice(0, 9)}…` : shot.screen_name;
  return <span className="shot-badge" title={shot.screen_name}>{name}</span>;
}
