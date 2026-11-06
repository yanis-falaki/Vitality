// Cards: the model as a tree of cards on a canvas (web spec, §6). Strivings across the top, the
// activities serving each beneath with curved links (dashed for a second striving), a group for the
// activities serving none, and an activity's actions expanding under it by day. The depth control
// shows strivings only, with activities, or with every action. Drag to pan, pinch or wheel to zoom
// around the pointer, or use the zoom buttons; a tap or click selects.
import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import type { Activity, Striving } from "../api/client";
import { Confidence, LevelLabel } from "../components/bits";
import { WithModel } from "../components/Loading";
import { ViewFrame } from "../components/Shell";
import { clock, day, dayLabel, minutes, plural } from "../lib/format";
import { useLayout } from "../lib/media";
import type { Model } from "../lib/model";
import { useSelection } from "../lib/selection";
import "./cards.css";

type Depth = 1 | 2 | 3;

export function Cards() {
  return <WithModel>{(model) => <CardsView model={model} />}</WithModel>;
}

function CardsView({ model }: { model: Model }) {
  const [depth, setDepth] = useState<Depth>(2);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const { sel } = useSelection();
  // Selecting an action (here or elsewhere) opens its activity.
  const selAction = sel ? model.action(sel) : undefined;
  const open = (id: string) => depth === 3 || expanded.has(id) || (selAction?.part_of.includes(id) ?? false);
  const toggle = (id: string) => setExpanded((e) => {
    const next = new Set(e);
    if (open(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const groups: { head: Striving | null; activities: Activity[] }[] = [
    ...model.strivings.map((s) => ({ head: s, activities: model.activities.filter((a) => a.part_of[0] === s.id) })),
    ...(model.unassigned.length ? [{ head: null, activities: model.unassigned }] : []),
  ];
  return (
    <ViewFrame model={model} empty="Select a card to see what the model believes, and why.">
      <Canvas toolbar={
        <div className="seg" role="group" aria-label="Depth">
          {(["Strivings", "+ Activities", "+ Actions"] as const).map((label, i) => (
            <button type="button" key={label} className={depth === i + 1 ? "on" : ""} onClick={() => setDepth((i + 1) as Depth)}>{label}</button>
          ))}
        </div>
      } layoutKey={`${depth}|${[...expanded].join()}|${sel}`} model={model}>
        {groups.map((g) => (
          <div className="cards-group" key={g.head?.id ?? "none"}>
            {g.head ? <StrivingCard model={model} s={g.head} /> : <div className="cards-none" data-anchor="none">Not yet serving a striving</div>}
            {depth >= 2 && (
              <div className="cards-activities">
                {g.activities.map((a) => (
                  <div className="cards-column" key={a.id}>
                    <ActivityCard model={model} a={a} open={open(a.id)} onToggle={() => toggle(a.id)} />
                    {open(a.id) && <ActionList model={model} a={a} />}
                  </div>
                ))}
                {g.activities.length === 0 && <div className="cards-empty">No activities yet</div>}
              </div>
            )}
          </div>
        ))}
      </Canvas>
    </ViewFrame>
  );
}

function StrivingCard({ model, s }: { model: Model; s: Striving }) {
  const { sel, select } = useSelection();
  return (
    <button type="button" className={`card card-striving${sel === s.id ? " sel" : ""}`} data-anchor={s.id} onClick={() => select(s.id)}>
      <LevelLabel kind="striving" id={s.id} />
      <span className="card-title">{s.title}</span>
      <span className="card-foot"><Confidence value={s.confidence} /><span>{plural(model.activitiesOf(s.id).length, "activity", "activities")}</span></span>
    </button>
  );
}

function ActivityCard({ model, a, open, onToggle }: { model: Model; a: Activity; open: boolean; onToggle(): void }) {
  const { sel, select } = useSelection();
  const n = model.actionsOf(a.id).length;
  return (
    <div className={`card card-activity ${a.status}${sel === a.id ? " sel" : ""}`} data-anchor={a.id}
      role="button" tabIndex={0} onClick={() => select(a.id)} onKeyDown={(e) => { if (e.key === "Enter") select(a.id); }}>
      <div className="level level-activity"><i className={`dot dot-activity${a.status === "tentative" ? " tentative" : ""}`} />Activity · {a.status}</div>
      <span className="card-title">{a.title}</span>
      <span className="card-foot">
        <Confidence value={a.confidence} />
        {n > 0 && (
          <button type="button" className="card-expand" aria-expanded={open}
            onClick={(e) => { e.stopPropagation(); onToggle(); }}>{open ? "▾" : "▸"} {plural(n, "action")}</button>
        )}
      </span>
    </div>
  );
}

function ActionList({ model, a }: { model: Model; a: Activity }) {
  const { sel, select } = useSelection();
  let last = "";
  return (
    <div className="cards-actions">
      {model.actionsOf(a.id).map((x) => {
        const d = day(x.start);
        const head = d !== last;
        last = d;
        return (
          <Fragment key={x.id}>
            {head && <div className="cards-day">{dayLabel(d)}</div>}
            <button type="button" className={`card-action${sel === x.id ? " sel" : ""}`} onClick={() => select(x.id)}>
              <span className="time">{clock(x.start)} · {minutes(x.start, x.end)} min</span>
              {x.summary}
            </button>
          </Fragment>
        );
      })}
    </div>
  );
}

// -- the canvas: pan, zoom, and the links drawn between cards ------------------------------------

interface CanvasProps { children: ReactNode; toolbar: ReactNode; layoutKey: string; model: Model }

function Canvas({ children, toolbar, layoutKey, model }: CanvasProps) {
  const layout = useLayout();
  const stage = useRef<HTMLDivElement>(null);
  const tree = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: layout === "phone" ? 28 : 0, z: layout === "phone" ? 0.7 : 0.9 });
  const [links, setLinks] = useState<{ d: string; second: boolean; none: boolean }[]>([]);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ moved: boolean; pinch: number | null }>({ moved: false, pinch: null });

  const zoomAt = useCallback((factor: number, cx?: number, cy?: number) => {
    const box = stage.current!.getBoundingClientRect();
    setView((v) => {
      const px = cx ?? box.width / 2, py = cy ?? box.height / 2;
      const z = Math.min(1.6, Math.max(0.25, v.z * factor));
      return { x: px - (px - v.x) * (z / v.z), y: py - (py - v.y) * (z / v.z), z };
    });
  }, []);
  const fit = () => {
    const w = stage.current!.clientWidth, h = stage.current!.clientHeight;
    const t = tree.current!;
    const z = Math.min(1, w / (t.scrollWidth + 24), h / (t.scrollHeight + 24));
    setView({ x: Math.max(8, (w - t.scrollWidth * z) / 2), y: 8, z: Math.max(0.25, z) });
  };

  // The links, measured from the cards (in the tree's own, unscaled coordinates).
  useLayoutEffect(() => {
    const root = tree.current!;
    const at = (id: string, edge: "top" | "bottom") => {
      const el = root.querySelector<HTMLElement>(`[data-anchor="${CSS.escape(id)}"]`);
      if (!el) return null;
      let x = el.offsetWidth / 2, y = edge === "bottom" ? el.offsetHeight : 0;
      for (let n: HTMLElement | null = el; n && n !== root; n = n.offsetParent as HTMLElement | null) {
        x += n.offsetLeft;
        y += n.offsetTop;
      }
      return { x, y };
    };
    const out: { d: string; second: boolean; none: boolean }[] = [];
    for (const a of model.activities) {
      const to = at(a.id, "top");
      if (!to) continue;
      (a.part_of.length ? a.part_of : ["none"]).forEach((s, i) => {
        const from = at(s, "bottom");
        if (!from) return;
        const my = (from.y + to.y) / 2;
        out.push({ d: `M${from.x},${from.y} C${from.x},${my} ${to.x},${my} ${to.x},${to.y}`, second: i > 0, none: s === "none" });
      });
    }
    setLinks(out);
  }, [layoutKey, model]);

  // The wheel zooms around the pointer (sideways scrolling pans); the page itself never zooms.
  useEffect(() => {
    const el = stage.current!;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      if (e.ctrlKey || Math.abs(e.deltaY) > Math.abs(e.deltaX)) zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - box.left, e.clientY - box.top);
      else setView((v) => ({ ...v, x: v.x - e.deltaX }));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  // Pointers: one drags, two pinch. A gesture that moved is not a click.
  const onDown = (e: PointerEvent) => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 1) gesture.current = { moved: false, pinch: null };
  };
  const onMove = (e: PointerEvent) => {
    const last = pointers.current.get(e.pointerId);
    if (!last) return;
    const now = { x: e.clientX, y: e.clientY };
    if (pointers.current.size === 1) {
      const dx = now.x - last.x, dy = now.y - last.y;
      if (!gesture.current.moved && Math.hypot(dx, dy) < 4) return;
      if (!gesture.current.moved) stage.current!.setPointerCapture(e.pointerId);
      gesture.current.moved = true;
      setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
    } else if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.entries()].map(([id, p]) => (id === e.pointerId ? now : p));
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      const box = stage.current!.getBoundingClientRect();
      if (gesture.current.pinch) zoomAt(distance / gesture.current.pinch, (a.x + b.x) / 2 - box.left, (a.y + b.y) / 2 - box.top);
      gesture.current = { moved: true, pinch: distance };
    }
    pointers.current.set(e.pointerId, now);
  };
  const onUp = (e: PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) gesture.current.pinch = null;
  };

  return (
    <div className="cards-stage" ref={stage} data-testid="cards"
      onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
      onClickCapture={(e) => { if (gesture.current.moved) { e.stopPropagation(); e.preventDefault(); gesture.current.moved = false; } }}>
      <div className="cards-tree" ref={tree} style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})` }}>
        <svg className="cards-links" aria-hidden>
          {links.map((l, i) => (
            <path key={i} d={l.d} className={`cards-link${l.second ? " second" : ""}${l.none ? " none" : ""}`} />
          ))}
        </svg>
        {children}
      </div>
      <div className="cards-tools" onPointerDown={(e) => e.stopPropagation()}>{toolbar}</div>
      <div className="cards-zoom" role="group" aria-label="Zoom" onPointerDown={(e) => e.stopPropagation()}>
        <button type="button" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.2)}>−</button>
        <button type="button" aria-label="Fit" onClick={fit}>⤢</button>
        <button type="button" aria-label="Zoom in" onClick={() => zoomAt(1.2)}>+</button>
      </div>
    </div>
  );
}
