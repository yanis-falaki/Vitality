// The three layouts of the app, by window width: phone (one column, the inspector a full-screen
// sheet), tablet (the inspector slides over the view) and desktop (the inspector is a column).
import { useEffect, useState } from "react";

export type Layout = "phone" | "tablet" | "desktop";

const PHONE = "(max-width: 699px)";
const TABLET = "(min-width: 700px) and (max-width: 1099px)";

function current(): Layout {
  if (window.matchMedia(PHONE).matches) return "phone";
  if (window.matchMedia(TABLET).matches) return "tablet";
  return "desktop";
}

/** The layout for the window's width, updated as it changes. */
export function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(current);
  useEffect(() => {
    const queries = [PHONE, TABLET].map((q) => window.matchMedia(q));
    const update = () => setLayout(current());
    queries.forEach((q) => q.addEventListener("change", update));
    return () => queries.forEach((q) => q.removeEventListener("change", update));
  }, []);
  return layout;
}
