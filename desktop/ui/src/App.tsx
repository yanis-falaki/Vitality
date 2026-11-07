// The window: Setup while no screen is set up or an essential check fails (spec §8), else the
// sidebar (pages, and capture's state with Pause/Resume at the bottom) and the page shown,
// Status first. Once Setup has started it stays until its Done, even if the checks pass midway.
import { useEffect, useState } from "react";
import { CaptureState } from "./components/CaptureState";
import { useChecks, useCoreEvents, useStatus } from "./lib/queries";
import { needsSetup } from "./lib/setup";
import { HowItWorks } from "./pages/HowItWorks";
import { Screens } from "./pages/Screens";
import { Settings } from "./pages/Settings";
import { Setup } from "./pages/Setup";
import { Status } from "./pages/Status";

const PAGES = { status: "Status", screens: "Screens", settings: "Settings", how: "How capture works" } as const;
type Page = keyof typeof PAGES;

export function App() {
  useCoreEvents();
  const params = new URLSearchParams(location.search);
  const [page, setPage] = useState<Page>(() => (params.get("page") as Page) || "status");
  const [inSetup, setInSetup] = useState(() => params.get("setup") === "1");
  const status = useStatus().data;
  const checks = useChecks().data;
  const needed = needsSetup(status, checks);
  useEffect(() => {
    if (needed) setInSetup(true);
  }, [needed]);
  if (needed === undefined) return <div className="app-loading" />;
  if (needed || inSetup)
    return <Setup initialStep={Number(params.get("step") ?? 0)} onDone={() => { setInSetup(false); setPage("status"); }} />;
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
      <main className="page">
        {page === "status" && <Status />}
        {page === "screens" && <Screens />}
        {page === "settings" && <Settings />}
        {page === "how" && <HowItWorks />}
      </main>
    </div>
  );
}
