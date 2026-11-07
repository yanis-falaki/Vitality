// The window: the sidebar (pages, and capture's state with Pause/Resume at the bottom) and the
// page shown. Milestone 1 has one page, Screens; Status, Settings and Setup come later.
import { CaptureState } from "./components/CaptureState";
import { useCoreEvents } from "./lib/queries";
import { Screens } from "./pages/Screens";

export function App() {
  useCoreEvents();
  return (
    <div className="app">
      <aside className="sidebar">
        <span className="brand"><span className="brand-mark" aria-hidden />Vitality</span>
        <nav className="nav" aria-label="Pages">
          <a className="on" aria-current="page">Screens</a>
        </nav>
        <span className="spacer" />
        <CaptureState />
      </aside>
      <main className="page">
        <Screens />
      </main>
    </div>
  );
}
