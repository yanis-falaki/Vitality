// Graph: the model as a network (web spec, §6). Strivings, activities and actions are circles sized
// and coloured by level; edges are part_of, hinders and the strivings' relations; "+ Evidence" adds
// the interactions. A time range (all, the last 30 or 7 days of the model) keeps every striving and
// activity and the actions (and evidence) of those days. Each (range, evidence) is laid out once,
// when first shown, with ForceAtlas2 from a circle (so the same model always gives the same
// picture), in a worker; a range with more evidence than can be laid out calmly shows the model with
// a note, and the selected action's own evidence around it. Clicking (or tapping) a node focuses its two-step neighbourhood and opens it in the
// inspector; hovering highlights its neighbours; the search box jumps to a node. Strivings and
// activities are always labelled, actions once zoomed in, never overlapping (Sigma's label grid).
import Graph from "graphology";
import { useEffect, useMemo, useRef, useState } from "react";
import Sigma from "sigma";
import type { NodeLabelDrawingFunction } from "sigma/rendering";
import type { CameraState, NodeDisplayData } from "sigma/types";
import { WithModel } from "../components/Loading";
import { ViewFrame } from "../components/Shell";
import { useInteractions, type Action, type InteractionSummary, type Interactions } from "../api/client";
import { addDays } from "../lib/calendar";
import { clock, day, short } from "../lib/format";
import { useLayout } from "../lib/media";
import { LEVEL, type Kind, type Model } from "../lib/model";
import { useSelection } from "../lib/selection";
import "./graph.css";
import type { LayoutReply, LayoutRequest } from "./layout.worker";

interface NodeAttrs { key: string; kind: Kind; label: string; size: number; color: string; x: number; y: number; tentative: boolean }
interface EdgeAttrs { size: number; color: string }

const SIZE: Record<Kind, number> = { striving: 17, activity: 11, action: 5, interaction: 3 };

export function GraphView() {
  return <WithModel>{(model) => <GraphPage model={model} />}</WithModel>;
}

type Range = "all" | "30" | "7";
const RANGES: { range: Range; label: string; short: string }[] = [
  { range: "all", label: "All", short: "All" },
  { range: "30", label: "Last 30 days", short: "30 days" },
  { range: "7", label: "Last 7 days", short: "7 days" },
];
/** Above this many interactions in the range, the evidence is not laid out (the note says why). */
const EVIDENCE_CAP = 5000;

function GraphPage({ model }: { model: Model }) {
  const [evidence, setEvidence] = useState(false);
  const [range, setRange] = useState<Range>("all");
  const [query, setQuery] = useState("");
  const { sel, select } = useSelection();
  const phone = useLayout() === "phone";

  // What the range holds: every striving and activity; the actions (and their evidence) since `from`.
  const from = range === "all" || !model.lastDay ? null : addDays(model.lastDay, 1 - Number(range));
  const actions = useMemo(() => (from ? model.actions.filter((a) => day(a.start) >= from) : model.actions), [model, from]);
  const count = useMemo(() => new Set(actions.flatMap((a) => a.interactions)).size, [actions]);
  const capped = evidence && count > EVIDENCE_CAP;
  const withEvidence = evidence && !capped;
  const summaries = useInteractions(from, null, withEvidence).data;
  // Too much evidence for the range: the selected action's own, placed around it, once fetched.
  const picked = capped && sel ? actions.find((a) => a.id === sel) : undefined;
  const own = useInteractions(picked ? day(picked.start) : null, picked ? day(picked.start) : null, !!picked).data;

  // Layouts come from a worker, one per (range, evidence), computed when first shown and kept.
  const layouts = useLayouts(model);
  const modelKey = `${range}|model`, evidenceKey = `${range}|evidence`;
  useEffect(() => {
    layouts.want(modelKey, () => buildGraph(model, actions, null, null));
    if (withEvidence) layouts.want(evidenceKey, () => buildGraph(model, actions, {}, null));
  }, [layouts, model, actions, modelKey, evidenceKey, withEvidence]);
  const evidenceLayout = withEvidence && summaries ? layouts.get(evidenceKey) : null;
  // While a range is first arranged, the graph shown before it stays, with a note.
  const shown = useRef<Graph<NodeAttrs, EdgeAttrs> | null>(null);
  const modelLayout = layouts.get(modelKey);
  const graph = useMemo(() => {
    if (evidenceLayout) return buildGraph(model, actions, summaries!, evidenceLayout);
    // With evidence asked for, the graph on screen stays until its evidence is placed (one swap, not two).
    if (!modelLayout || (withEvidence && shown.current)) return null;
    const g = buildGraph(model, actions, null, modelLayout);
    if (picked && own) around(g, picked, own);
    return g;
  }, [model, actions, summaries, evidenceLayout, modelLayout, picked, own, withEvidence]);
  if (graph) shown.current = graph;
  const onScreen = graph ?? shown.current;
  const arranging = withEvidence && !evidenceLayout ? "Arranging the evidence…"
    : !modelLayout ? (onScreen ? "Arranging the range…" : "Arranging the graph…") : null;

  return (
    <ViewFrame model={model} empty="Click a node to see what the model believes, and why.">
      <div className="graph-stage">
        {onScreen && <Network graph={onScreen} query={query} layout={evidenceLayout ? evidenceKey : modelKey} />}
        {arranging && <div className="graph-arranging" role="status"><i className="spinner" aria-hidden />{arranging}</div>}
        {!arranging && capped && (
          <div className="graph-arranging graph-note" role="status">
            Too much evidence for this range — pick a shorter range or select an action
          </div>
        )}
        <div className="graph-tools">
          <div className="graph-search">
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder="Search the model…" aria-label="Search the model (Enter jumps to the first match)"
              onKeyDown={(e) => {
                if (e.key === "Enter" && onScreen) { jump(onScreen, query, select); setQuery(""); }
              }} />
          </div>
          <div className="seg" role="group" aria-label="What to show">
            <button type="button" className={evidence ? "" : "on"} onClick={() => setEvidence(false)}>Model</button>
            <button type="button" className={evidence ? "on" : ""} onClick={() => setEvidence(true)}>+ Evidence</button>
          </div>
          <div className="seg" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <button type="button" key={r.range} className={range === r.range ? "on" : ""} title={r.label}
                onClick={() => setRange(r.range)}>{phone ? r.short : r.label}</button>
            ))}
          </div>
        </div>
        {onScreen && <Legend graph={onScreen} />}
      </div>
    </ViewFrame>
  );
}

// Select the first node whose label holds the query, preferring the higher levels.
function jump(graph: Graph<NodeAttrs, EdgeAttrs>, query: string, select: (id: string) => void) {
  const q = query.trim().toLowerCase();
  if (!q) return;
  const order: Kind[] = ["striving", "activity", "action", "interaction"];
  const hits = graph.filterNodes((_, a) => a.label.toLowerCase().includes(q));
  hits.sort((a, b) => order.indexOf(graph.getNodeAttribute(a, "kind")) - order.indexOf(graph.getNodeAttribute(b, "kind")));
  if (hits.length) select(hits[0]);
}

/** The graph drawn by Sigma. `layout` names the layout its nodes were placed by: a new graph from the
 * same layout (the selected action's own evidence added around it) keeps the camera where it was. */
function Network({ graph, query, layout }: { graph: Graph<NodeAttrs, EdgeAttrs>; query: string; layout: string }) {
  const { sel, select } = useSelection();
  const container = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const sigma = useRef<Sigma<NodeAttrs, EdgeAttrs> | null>(null);
  // The renderer is made once per graph: what it calls back is read through refs, never a reason to remake it.
  const selectRef = useRef(select);
  selectRef.current = select;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const kept = useRef<{ layout: string; camera: CameraState } | null>(null);
  const state = useRef({ hovered: null as string | null, focus: null as Set<string> | null, sel: null as string | null, query: "" });

  useEffect(() => {
    const palette = tokens();
    // Labels are placed once per frame, when the first is drawn (after Sigma has positioned the nodes).
    const placed: Placed = { side: new Map(), stale: true, place: () => {} };
    const renderer = new Sigma<NodeAttrs, EdgeAttrs>(graph, container.current!, {
      labelFont: palette.sans, labelSize: 12, labelWeight: "500", labelColor: { color: palette.ink },
      labelDensity: 1, labelGridCellSize: 60, labelRenderedSizeThreshold: THRESHOLD,
      defaultDrawNodeLabel: drawLabel(palette, placed), defaultDrawNodeHover: drawHover(palette),
      minCameraRatio: 0.08, maxCameraRatio: 3, zIndex: true, stagePadding: 40,
      nodeReducer: (node, data) => nodeLook(graph, node, data, state.current, palette),
      edgeReducer: (edge, data) => edgeLook(graph, edge, data, state.current, palette),
    });
    sigma.current = renderer;
    Object.assign(container.current!, { sigma: renderer });  // for the smoke run: the camera, and nodes on screen
    const madeFor = layoutRef.current;
    if (kept.current && kept.current.layout === madeFor) renderer.getCamera().setState(kept.current.camera);
    placed.place = () => placeLabels(renderer, graph, state.current, palette, placed);
    const refresh = () => renderer.refresh({ skipIndexation: true });
    renderer.on("clickNode", ({ node }) => selectRef.current(node));
    renderer.on("clickStage", () => { state.current.focus = null; selectRef.current(null); });
    renderer.on("enterNode", ({ node }) => { state.current.hovered = node; refresh(); });
    renderer.on("leaveNode", () => { state.current.hovered = null; refresh(); });
    renderer.on("beforeRender", () => { placed.stale = true; });
    renderer.on("afterRender", () => drawRings(renderer, graph, overlay.current!, palette));
    // Canvas text needs the bundled font loaded before the first labels are drawn.
    document.fonts.load(`500 12px ${palette.sans}`).then(refresh, () => {});
    return () => {
      kept.current = { layout: madeFor, camera: renderer.getCamera().getState() };
      renderer.kill();
      sigma.current = null;
    };
  }, [graph]);

  // The selection (from a click, the inspector or the URL) focuses its neighbourhood.
  useEffect(() => {
    const renderer = sigma.current;
    if (!renderer) return;
    state.current.sel = sel && graph.hasNode(sel) ? sel : null;
    state.current.focus = state.current.sel ? neighbourhood(graph, state.current.sel, 2) : null;
    renderer.refresh({ skipIndexation: true });
    if (state.current.sel) {
      const at = renderer.getNodeDisplayData(state.current.sel);
      const kind = graph.getNodeAttribute(state.current.sel, "kind");
      const ratio = kind === "action" || kind === "interaction" ? 0.45 : 0.75;
      if (at) renderer.getCamera().animate({ x: at.x + coveredShift(renderer, ratio), y: at.y, ratio }, { duration: 450 });
    }
  }, [sel, graph]);

  useEffect(() => {
    state.current.query = query.trim().toLowerCase();
    sigma.current?.refresh({ skipIndexation: true });
  }, [query]);

  const camera = () => sigma.current?.getCamera();
  return (
    <>
      <div ref={container} className="graph-canvas" data-testid="graph" />
      <canvas ref={overlay} className="graph-overlay" aria-hidden />
      <div className="graph-zoom" role="group" aria-label="Zoom">
        <button type="button" aria-label="Zoom out" onClick={() => camera()?.animatedUnzoom({ duration: 200 })}>−</button>
        <button type="button" aria-label="Fit" onClick={() => camera()?.animatedReset({ duration: 300 })}>⤢</button>
        <button type="button" aria-label="Zoom in" onClick={() => camera()?.animatedZoom({ duration: 200 })}>+</button>
      </div>
    </>
  );
}

function Legend({ graph }: { graph: Graph<NodeAttrs, EdgeAttrs> }) {
  const counts = new Map<Kind, number>();
  graph.forEachNode((_, a) => counts.set(a.kind, (counts.get(a.kind) ?? 0) + 1));
  return (
    <div className="graph-legend" aria-label="Legend">
      {(["striving", "activity", "action", "interaction"] as Kind[]).filter((k) => counts.get(k)).map((k) => (
        <span key={k}><i className={`dot dot-${k} legend-${k}`} />{counts.get(k)} {LEVEL[k].plural}</span>
      ))}
      <span><i className="legend-ring" />tentative</span>
      <span><i className="legend-line" />works against</span>
    </div>
  );
}

// -- the graph ---------------------------------------------------------------------------------

type Layout = Map<string, { x: number; y: number }>;

interface Layouts {
  get(key: string): Layout | null;
  /** Start laying out `key` (unless it is done or under way): `graph` builds what the worker places. */
  want(key: string, graph: () => Graph<NodeAttrs, EdgeAttrs>): void;
}

/** The layouts of one model, by key, each computed once in its own worker and kept while the model is. */
function useLayouts(model: Model): Layouts {
  const store = useRef(new WeakMap<Model, Map<string, Layout | "running">>());
  const workers = useRef(new Map<Worker, () => void>());
  const [, setVersion] = useState(0);
  // A new model (or leaving the view) stops the work still under way for the old one.
  useEffect(() => () => {
    workers.current.forEach((forget, w) => { w.terminate(); forget(); });
    workers.current.clear();
  }, [model]);
  return useMemo(() => {
    const of = () => {
      let layouts = store.current.get(model);
      if (!layouts) store.current.set(model, (layouts = new Map()));
      return layouts;
    };
    return {
      get(key) {
        const found = of().get(key);
        return found && found !== "running" ? found : null;
      },
      want(key, graph) {
        const layouts = of();
        if (layouts.has(key)) return;
        layouts.set(key, "running");
        const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
        workers.current.set(worker, () => layouts.delete(key));
        worker.onmessage = (event: MessageEvent<LayoutReply>) => {
          layouts.set(key, new Map(Object.entries(event.data)));
          workers.current.delete(worker);
          worker.terminate();
          setVersion((v) => v + 1);
        };
        // Built and sent in a task of its own, so a frame never holds two of them; only ids and
        // edges cross to the worker (cheaper to copy than a serialized graph).
        setTimeout(() => {
          if (!workers.current.has(worker)) return;
          const g = graph();
          const index = new Map(g.nodes().map((n, i) => [n, i]));
          const request: LayoutRequest = {
            nodes: g.nodes(), edges: g.mapEdges((_, __, a, b) => [index.get(a)!, index.get(b)!] as [number, number]),
            scalingRatio: key.endsWith("|evidence") ? 6 : 14,
          };
          worker.postMessage(request);
        }, 0);
      },
    };
  }, [model]);
}

/** The graph of the model with `actions` (and, with `summaries`, the interactions they cite), placed by
 * `layout` when given. For a layout request, `summaries` may be empty: the worker needs no labels. */
function buildGraph(model: Model, actions: Action[], summaries: Interactions | null, layout: Layout | null): Graph<NodeAttrs, EdgeAttrs> {
  const p = tokens();
  const colour: Record<Kind, string> = { striving: p.striving, activity: p.activity, action: p.action, interaction: p.evidence };
  const graph = new Graph<NodeAttrs, EdgeAttrs>({ type: "undirected", multi: false });
  // Many small dots read better smaller: actions and evidence shrink once there are hundreds of them.
  const crowd = actions.length > 600 ? 0.7 : 1;
  const sized = (kind: Kind) => (kind === "action" || kind === "interaction" ? SIZE[kind] * crowd : SIZE[kind]);
  const add = (id: string, kind: Kind, label: string, tentative = false) =>
    graph.addNode(id, { key: id, kind, label, size: sized(kind), color: colour[kind], x: 0, y: 0, tentative });
  const edge = (a: string, b: string, size: number, color: string) => {
    if (graph.hasNode(a) && graph.hasNode(b) && a !== b && !graph.hasEdge(a, b)) graph.addEdge(a, b, { size, color });
  };
  model.strivings.forEach((s) => add(s.id, "striving", short(s.title, 60)));
  model.activities.forEach((a) => add(a.id, "activity", short(a.title, 48), a.status === "tentative"));
  actions.forEach((a) => add(a.id, "action", short(a.summary, 44)));
  for (const a of model.activities) {
    a.part_of.forEach((s) => edge(a.id, s, 2, p.edgeStrong));
    a.hinders.forEach((s) => edge(a.id, s, 2, p.edgeAgainst));
  }
  for (const a of actions) a.part_of.forEach((x) => edge(a.id, x, 1, p.edge));
  for (const s of model.strivings) {
    s.relations.conflicts_with.forEach((t) => edge(s.id, t, 2.5, p.edgeAgainst));
    [...s.relations.facilitates, ...s.relations.serves].forEach((t) => edge(s.id, t, 2.5, p.edgeStrong));
  }
  if (summaries) {
    for (const a of actions) {
      for (const i of a.interactions) {
        if (!graph.hasNode(i)) add(i, "interaction", interactionLabel(summaries[i]));
        edge(a.id, i, 0.6, p.edge);
      }
    }
  }
  if (layout) graph.forEachNode((n) => { const at = layout.get(n); if (at) graph.mergeNodeAttributes(n, at); });
  return graph;
}

// "9:18 a.m. · Opened doc.rust-lang.org on the… (+2)"; empty until its summary is known.
function interactionLabel(summary: InteractionSummary | undefined): string {
  if (!summary) return "";
  const more = summary.operations - 1;
  return `${clock(summary.start)} · ${short(summary.first_operation, 40)}${more ? ` (+${more})` : ""}`;
}

// The interactions of `action` (summaries of its day in `summaries`), on a small ring around it: when
// the range holds too much evidence to lay out, the selected action still shows its own.
function around(graph: Graph<NodeAttrs, EdgeAttrs>, action: Action, summaries: Interactions) {
  if (!graph.hasNode(action.id)) return;
  const p = tokens();
  let minX = Infinity, maxX = -Infinity;
  graph.forEachNode((_, a) => { minX = Math.min(minX, a.x); maxX = Math.max(maxX, a.x); });
  const r = Math.max(1, (maxX - minX) * 0.012);
  const at = graph.getNodeAttributes(action.id);
  action.interactions.forEach((i, n) => {
    if (graph.hasNode(i)) return;
    const angle = (2 * Math.PI * n) / action.interactions.length;
    graph.addNode(i, { key: i, kind: "interaction", label: interactionLabel(summaries[i]), size: SIZE.interaction,
      color: p.evidence, x: at.x + r * Math.cos(angle), y: at.y + r * Math.sin(angle), tentative: false });
    graph.addEdge(action.id, i, { size: 0.6, color: p.edge });
  });
}

// How far right of a node the camera must look (in framed graph units, at `ratio`) for the node to sit
// in the middle of what a sliding inspector (tablet) leaves visible.
function coveredShift(renderer: Sigma<NodeAttrs, EdgeAttrs>, ratio: number): number {
  const panel = document.querySelector(".frame-tablet .frame-inspector");
  if (!panel) return 0;
  const px = panel.getBoundingClientRect().width / 2;
  const unit = renderer.viewportToFramedGraph({ x: 1, y: 0 }).x - renderer.viewportToFramedGraph({ x: 0, y: 0 }).x;
  return px * unit * (ratio / renderer.getCamera().getState().ratio);
}

function neighbourhood(graph: Graph, node: string, hops: number): Set<string> {
  const seen = new Set([node]);
  let frontier = [node];
  for (let h = 0; h < hops; h++) {
    const next: string[] = [];
    for (const n of frontier) graph.forEachNeighbor(n, (m) => { if (!seen.has(m)) { seen.add(m); next.push(m); } });
    frontier = next;
  }
  return seen;
}

// -- how nodes and edges look ------------------------------------------------------------------

interface Look { hovered: string | null; focus: Set<string> | null; sel: string | null; query: string }

function nodeLook(graph: Graph<NodeAttrs, EdgeAttrs>, node: string, data: NodeAttrs, look: Look, p: Palette): Partial<NodeDisplayData> {
  const res: Partial<NodeDisplayData> & { tentative?: boolean } = { ...data };
  const active = look.hovered ?? look.sel;
  const high = data.kind === "striving" || data.kind === "activity";
  const faded = () => { res.color = p.faded; res.label = ""; res.zIndex = 0; };
  if (look.focus && !look.focus.has(node)) faded();
  else if (active) {
    if (node === active || graph.areNeighbors(node, active)) {
      res.zIndex = 2;
      res.forceLabel = true;
    } else if (!look.focus) faded();
    else res.forceLabel = high;
  } else res.forceLabel = high;
  if (look.query && !data.label.toLowerCase().includes(look.query)) faded();
  if (node === look.sel) { res.size = data.size * 1.3; res.forceLabel = true; res.zIndex = 3; res.highlighted = true; }
  return res;
}

function edgeLook(graph: Graph<NodeAttrs, EdgeAttrs>, edge: string, data: EdgeAttrs, look: Look, p: Palette) {
  const res: Partial<EdgeAttrs & { hidden: boolean }> = { ...data };
  const [a, b] = graph.extremities(edge);
  const active = look.hovered ?? look.sel;
  if (look.focus && !(look.focus.has(a) && look.focus.has(b))) res.hidden = true;
  else if (active && !look.focus && a !== active && b !== active) res.color = p.fadedEdge;
  if (look.query) res.color = p.fadedEdge;
  return res;
}

type Draw = NodeLabelDrawingFunction<NodeAttrs, EdgeAttrs>;

// Where each label goes this frame (right or left of its node, shortened if the screen is narrow),
// or absent when it would overlap another label or a large node: labels are placed by priority (the
// selection, then strivings, activities, actions, evidence), each clear of those placed before it.
interface Placed { side: Map<string, { left: boolean; text: string }>; stale: boolean; place(): void }
const PRIORITY: Kind[] = ["striving", "activity", "action", "interaction"];
const THRESHOLD = 7.5;  // rendered size (px) from which a node is labelled without being forced
const measure = document.createElement("canvas").getContext("2d")!;

interface Box { x: number; y: number; w: number; h: number; owner?: string }

function placeLabels(renderer: Sigma<NodeAttrs, EdgeAttrs>, graph: Graph<NodeAttrs, EdgeAttrs>, look: Look, p: Palette, placed: Placed) {
  placed.side.clear();
  placed.stale = false;
  const { width, height } = renderer.getDimensions();
  const candidates: { node: string; kind: Kind; x: number; y: number; r: number; label: string; first: boolean }[] = [];
  const boxes: Box[] = [];
  graph.forEachNode((node) => {
    const d = renderer.getNodeDisplayData(node) as (NodeDisplayData & { kind: Kind }) | undefined;
    if (!d || d.hidden) return;
    const r = renderer.scaleSize(d.size);
    const at = renderer.framedGraphToViewport(d);
    if (d.kind === "striving" || d.kind === "activity") boxes.push({ x: at.x - r, y: at.y - r, w: 2 * r, h: 2 * r, owner: node });
    if (!d.label || (!d.forceLabel && r < THRESHOLD)) return;
    if (at.x < -200 || at.x > width + 200 || at.y < -20 || at.y > height + 20) return;
    candidates.push({ node, kind: d.kind, x: at.x, y: at.y, r, label: d.label, first: node === look.sel || node === look.hovered });
  });
  candidates.sort((a, b) => Number(b.first) - Number(a.first) || PRIORITY.indexOf(a.kind) - PRIORITY.indexOf(b.kind) || b.r - a.r);
  const clear = (box: Box, node: string) => !boxes.some((o) => o.owner !== node
    && box.x < o.x + o.w && o.x < box.x + box.w && box.y < o.y + o.h && o.y < box.y + box.h);
  for (const c of candidates) {
    measure.font = labelFont(c.kind, p);
    const h = c.kind === "striving" ? 17 : 15;
    const roomRight = width - 6 - (c.x + c.r + 5), roomLeft = c.x - c.r - 5 - 6;
    let text = c.label;
    const fits = (t: string) => measure.measureText(t).width <= Math.max(roomRight, roomLeft);
    while (text.length > 12 && !fits(text)) text = `${text.slice(0, -2).trimEnd()}…`;
    const w = measure.measureText(text).width;
    const right: Box = { x: c.x + c.r + 3, y: c.y - h / 2, w: w + 4, h };
    const left: Box = { x: c.x - c.r - 7 - w, y: c.y - h / 2, w: w + 4, h };
    const box = w <= roomRight && clear(right, c.node) ? right : w <= roomLeft && clear(left, c.node) ? left : null;
    if (box) {
      boxes.push(box);
      placed.side.set(c.node, { left: box === left, text });
    }
  }
}

const labelFont = (kind: Kind, p: Palette) => (kind === "striving" ? `600 13px ${p.sans}` : `500 12px ${p.sans}`);

// Labels beside the node, haloed in the page colour so edges never cross the text.
function drawLabel(p: Palette, placed: Placed): Draw {
  return (ctx, data) => {
    const d = data as typeof data & { key: string; kind: Kind };
    if (placed.stale) placed.place();
    const place = placed.side.get(d.key);
    if (!d.label || !place) return;
    ctx.font = labelFont(d.kind, p);
    const w = ctx.measureText(place.text).width;
    const x = place.left ? d.x - d.size - 5 - w : d.x + d.size + 5, y = d.y + 4;
    ctx.lineJoin = "round";
    ctx.lineWidth = 4;
    ctx.strokeStyle = p.bg;
    ctx.strokeText(place.text, x, y);
    ctx.fillStyle = d.kind === "interaction" ? p.ink2 : p.ink;
    ctx.fillText(place.text, x, y);
  };
}

// The hovered node: its label on a white tag with a hairline border.
function drawHover(p: Palette): Draw {
  return (ctx, data, settings) => {
    if (!data.label) return;
    ctx.font = `${settings.labelWeight} ${settings.labelSize}px ${settings.labelFont}`;
    const w = ctx.measureText(data.label).width, h = settings.labelSize + 10;
    const x = data.x + data.size + 2, y = data.y - h / 2;
    ctx.fillStyle = p.panel;
    ctx.strokeStyle = p.line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x, y, w + 14, h, 6);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2);
    ctx.strokeStyle = data.color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = p.ink;
    ctx.fillText(data.label, x + 7, data.y + settings.labelSize / 3);
  };
}

// A dashed ring around each tentative activity, as tentative is dashed everywhere else.
function drawRings(renderer: Sigma<NodeAttrs, EdgeAttrs>, graph: Graph<NodeAttrs, EdgeAttrs>, canvas: HTMLCanvasElement, p: Palette) {
  const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.setLineDash([3, 3]);
  ctx.lineWidth = 1.5;
  graph.forEachNode((node, a) => {
    if (!a.tentative) return;
    const shown = renderer.getNodeDisplayData(node);
    if (!shown || shown.hidden || shown.color === p.faded) return;
    const at = renderer.framedGraphToViewport(shown);
    ctx.beginPath();
    ctx.arc(at.x, at.y, renderer.scaleSize(shown.size) + 4, 0, Math.PI * 2);
    ctx.strokeStyle = p.activity;
    ctx.stroke();
  });
}

// -- the theme's tokens, for the canvas --------------------------------------------------------

interface Palette {
  bg: string; panel: string; line: string; ink: string; ink2: string; sans: string; faded: string; fadedEdge: string;
  striving: string; activity: string; action: string; evidence: string; edge: string; edgeStrong: string; edgeAgainst: string;
}

function tokens(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    bg: v("--bg"), panel: v("--panel"), line: v("--line"), ink: v("--ink"), ink2: v("--ink-2"), sans: v("--sans"),
    faded: v("--line"), fadedEdge: v("--hover"),
    striving: v("--striving"), activity: v("--activity"), action: v("--action"), evidence: v("--evidence"),
    edge: v("--edge"), edgeStrong: v("--edge-strong"), edgeAgainst: v("--edge-against"),
  };
}
