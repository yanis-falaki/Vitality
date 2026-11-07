// A screen's live preview: its newest frame, fetched about once a second while the window is
// visible, in a 16:9 frame; a quiet placeholder before the first frame or when there is none.
import type { ReactNode } from "react";
import type { ScreenId } from "../core";
import { usePreview } from "../lib/queries";

export function Preview({ id, live, empty }: { id: ScreenId; live: boolean; empty: ReactNode }) {
  const preview = usePreview(id);
  const src = live ? preview.data : null;
  return (
    <div className="preview">
      {src ? <img src={src} alt="" draggable={false} /> : <div className="preview-empty">{empty}</div>}
    </div>
  );
}
