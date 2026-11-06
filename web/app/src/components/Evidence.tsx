// One interaction's evidence, as a card: its time, its operations (those `cited` by the action in
// view emphasised, the others dimmed), thumbnails that open the lightbox, and the observation
// behind a disclosure. Fetched when the card is shown.
import { useState } from "react";
import { shotUrl, useEvidence } from "../api/client";
import { clock, minutes, plural } from "../lib/format";
import type { Model } from "../lib/model";
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
  const urls = (data?.screenshots ?? []).map((f) => shotUrl(interaction, f));
  return (
    <div className="evidence">
      <div className="evidence-head">
        <span className="time">{start ? clock(start) : "—"}</span>
        <span>
          {data ? `${plural(data.operations.length, "operation")} · ${plural(urls.length, "screenshot")}`
            : isLoading ? "Loading…" : "Not found"}
          {start && end && <span className="muted"> · {minutes(start, end)} min</span>}
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
                <button type="button" key={url} className="thumb" onClick={() => show(urls, i)} aria-label={`Screenshot ${i + 1}`}>
                  <img src={url} alt="" loading="lazy" />
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
