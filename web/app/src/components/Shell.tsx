// The frame around every view: the top bar (views, the modeler's state), a bottom tab bar on a
// phone, and `ViewFrame`, which places a view beside the inspector: a column on a desktop, a panel
// sliding over the view on a tablet, a full-screen sheet on a phone.
import type { ReactNode } from "react";
import { NavLink, Outlet, useSearchParams } from "react-router-dom";
import { useModeler, type Modeler } from "../api/client";
import { useLayout } from "../lib/media";
import type { Model } from "../lib/model";
import { useSelection } from "../lib/selection";
import { Inspector } from "./Inspector";

const VIEWS = [
  { to: "/", label: "Reader", end: true },
  { to: "/graph", label: "Graph", end: false },
  { to: "/cards", label: "Cards", end: false },
  { to: "/day", label: "Day", end: false },
];

export function Shell() {
  const layout = useLayout();
  return (
    <div className={`shell shell-${layout}`}>
      <header className="topbar">
        <span className="brand">Vitality</span>
        {layout !== "phone" && <ViewTabs className="views" />}
        <span className="spacer" />
        <ModelerState compact={layout === "phone"} />
      </header>
      <main className="shell-main"><Outlet /></main>
      {layout === "phone" && <ViewTabs className="tabbar" />}
    </div>
  );
}

function ViewTabs({ className }: { className: string }) {
  // The selection goes along to the next view.
  const [params] = useSearchParams();
  const sel = params.get("sel");
  const search = sel ? `?sel=${encodeURIComponent(sel)}` : "";
  return (
    <nav className={className} aria-label="Views">
      {VIEWS.map((v) => (
        <NavLink key={v.to} to={{ pathname: v.to, search }} end={v.end}
          className={({ isActive }) => (isActive ? "on" : "")}>{v.label}</NavLink>
      ))}
    </nav>
  );
}

const STATES: Record<string, { text: string; tone: string }> = {
  idle: { text: "idle", tone: "ok" },
  running: { text: "running", tone: "busy" },
  paused: { text: "paused", tone: "bad" },
  "waiting-limit": { text: "waiting (usage limit)", tone: "warn" },
  "waiting-edits": { text: "waiting (uncommitted edits)", tone: "warn" },
  "not running": { text: "not running", tone: "off" },
};

/** "● modeler idle · 3 operations waiting · last session Nov 4, 11:20 p.m. committed". */
export function ModelerState({ compact }: { compact: boolean }) {
  const { data } = useModeler();
  if (!data) return <span className="mstate" />;
  const state = STATES[data.status.state] ?? { text: data.status.state, tone: "off" };
  const last = data.sessions[data.sessions.length - 1];
  const full = describe(data, state.text, last);
  return (
    <span className="mstate" title={full} data-testid="modeler-state">
      <i className={`mdot tone-${state.tone}`} aria-hidden />
      {compact ? (
        <span>{state.text}{data.waiting ? ` · ${data.waiting} waiting` : ""}</span>
      ) : (
        <span>
          modeler <b>{state.text}</b>
          {data.waiting !== null && <> · {data.waiting} operations waiting</>}
          {last && <> · last session {when(last.started)} {last.outcome}</>}
        </span>
      )}
    </span>
  );
}

function describe(data: Modeler, state: string, last: Modeler["sessions"][number] | undefined): string {
  const parts = [`modeler ${state}`];
  if (data.waiting !== null) parts.push(`${data.waiting} operations waiting`);
  if (last) parts.push(`last session ${when(last.started)} ${last.outcome}`);
  return parts.join(" · ");
}

// A session's UTC time, in the browser's own time zone.
const when = (iso: string) =>
  new Date(iso).toLocaleString("en", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

interface FrameProps {
  model: Model;
  children: ReactNode;         // the view itself
  empty?: ReactNode;           // the inspector's text when nothing is selected
  className?: string;
}

/** A view beside the inspector, laid out for the window's width. */
export function ViewFrame({ model, children, empty, className = "" }: FrameProps) {
  const layout = useLayout();
  const { sel, open, close } = useSelection();
  if (layout === "desktop") {
    return (
      <div className={`frame frame-desktop ${className}`}>
        <div className="frame-view">{children}</div>
        <aside className="frame-inspector"><Inspector model={model} id={sel} empty={empty} /></aside>
      </div>
    );
  }
  const shown = open && sel !== null;
  return (
    <div className={`frame frame-${layout} ${className}`}>
      <div className="frame-view">{children}</div>
      {shown && layout === "tablet" && <div className="frame-scrim" onClick={close} />}
      <aside className={`frame-inspector${shown ? " shown" : ""}`} aria-hidden={!shown}>
        {shown && <Inspector model={model} id={sel} onClose={close} closeLabel={layout === "phone" ? "← Back" : "Close"} />}
      </aside>
    </div>
  );
}
