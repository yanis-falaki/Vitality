// Screenshots at full size, over everything. `useLightbox().show(urls, index, captions?)` opens it;
// arrows (keys or buttons) step through the set, Escape or a tap outside the picture closes it. A
// caption (e.g. "Left monitor · passive") is shown beside the count.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

interface Lightbox { show(urls: string[], index: number, captions?: string[]): void }

const LightboxContext = createContext<Lightbox>({ show: () => {} });

export const useLightbox = () => useContext(LightboxContext);

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ urls: string[]; index: number; captions?: string[] } | null>(null);
  const show = useCallback((urls: string[], index: number, captions?: string[]) => setState({ urls, index, captions }), []);
  const step = useCallback((by: number) => setState((s) => s && {
    ...s, index: (s.index + by + s.urls.length) % s.urls.length,
  }), []);
  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setState(null);
      if (e.key === "ArrowRight") step(1);
      if (e.key === "ArrowLeft") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, step]);
  return (
    <LightboxContext.Provider value={{ show }}>
      {children}
      {state && (
        <div className="lightbox" role="dialog" aria-label="Screenshot" onClick={() => setState(null)}>
          <img src={state.urls[state.index]} alt="" onClick={(e) => e.stopPropagation()} />
          <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
            {state.urls.length > 1 && <button type="button" onClick={() => step(-1)} aria-label="Previous">←</button>}
            <span>{state.index + 1} / {state.urls.length}{state.captions?.[state.index] ? ` · ${state.captions[state.index]}` : ""}</span>
            {state.urls.length > 1 && <button type="button" onClick={() => step(1)} aria-label="Next">→</button>}
            <button type="button" onClick={() => setState(null)} aria-label="Close">Close</button>
          </div>
        </div>
      )}
    </LightboxContext.Provider>
  );
}
