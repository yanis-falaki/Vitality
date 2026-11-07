// The window: the sidebar (pages, and capture's state with Pause/Resume at the bottom) and the
// page shown, Status first.
import { useState } from "react";
import { CaptureState } from "./components/CaptureState";
import { useCoreEvents } from "./lib/queries";
import { Screens } from "./pages/Screens";
import { Status } from "./pages/Status";

const PAGES = { status: "Status", screens: "Screens" } as const;
type Page = keyof typeof PAGES;

export function App() {
  useCoreEvents();
  const [page, setPage] = useState<Page>(() => (new URLSearchParams(location.search).get("page") as Page) || "status");
  return (
    <div className="app">
      <aside className="sidebar">
        <span className="brand"><span className="brand-mark" aria-hidden />Vitality</span>
        <nav className="nav" aria-label="Pages">
          {(Object.keys(PAGES) as Page[]).map((p) => (
            <button key={p} className={p === page ? "on" : ""} aria-current={p === page ? "page" : undefined} onClick={() => setPage(p)}>
              {PAGES[p]}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <CaptureState />
      </aside>
      <main className="page">{page === "status" ? <Status /> : <Screens />}</main>
    </div>
  );
}
