// A small square in an activity's lane colour (Day, Home): dashed outline when the activity is tentative.
import type { CSSProperties } from "react";
import type { Model } from "../lib/model";

export function Swatch({ model, id, palette }: { model: Model; id: string; palette: Map<string, string> }) {
  const tentative = !!id && model.activity(id)?.status === "tentative";
  return <i className={`swatch${tentative ? " tentative" : ""}`} style={{ "--c": palette.get(id) } as CSSProperties} aria-hidden />;
}
