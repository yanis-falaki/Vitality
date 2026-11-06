// What a view shows while the model loads, or when it cannot be read.
import type { ReactNode } from "react";
import { useModel, type Model } from "../lib/model";

/** Renders `children(model)` once the model is loaded; a quiet notice until then. */
export function WithModel({ children }: { children: (model: Model) => ReactNode }) {
  const { model, error } = useModel();
  if (error) return <div className="notice">The model could not be read ({String(error)}). It is tried again every minute.</div>;
  if (!model) return <div className="notice">Loading the model…</div>;
  return <>{children(model)}</>;
}
