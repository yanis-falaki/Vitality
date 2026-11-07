// When the owner last opened Home, kept in this browser (localStorage), so that what changed since can be
// marked. Storage may be missing or refuse (a private window): then nothing is marked, and nothing fails.
import { useEffect, useRef, useState } from "react";

const KEY = "vitality.home.lastVisit";

function read(): number | null {
  try {
    const value = Number(window.localStorage.getItem(KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function write(ms: number) {
  try {
    window.localStorage.setItem(KEY, String(ms));
  } catch {
    // not kept: the next visit marks nothing as new
  }
}

/** The time of the previous visit (null on the first, or without storage); this visit is recorded at `now`. */
export function useLastVisit(now: number | undefined): number | null {
  const [previous] = useState(read);
  const written = useRef(false);
  useEffect(() => {
    if (now !== undefined && !written.current) {
      written.current = true;
      write(Math.max(now, previous ?? 0));
    }
  }, [now, previous]);
  return previous;
}
