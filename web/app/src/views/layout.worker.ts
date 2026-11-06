// The graph's layout, off the main thread: ForceAtlas2 from a circle, the same for the same graph.
// Receives a graph exported by graphology (`graph.export()`) and the scaling to use; replies with
// each node's position, so the page stays responsive while the nodes are placed.
//
// Up to 1,000 nodes, 600 exact iterations (as on the first week of a model). Larger graphs (months of
// actions, or a range's evidence) use the Barnes–Hut approximation and fewer iterations, which keeps
// a 5,000-node graph to a few seconds; the view never asks for more than that.

import Graph from "graphology";
import { circular } from "graphology-layout";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type { SerializedGraph } from "graphology-types";

export interface LayoutRequest { graph: SerializedGraph; scalingRatio: number }
export type LayoutReply = Record<string, { x: number; y: number }>;

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const graph = Graph.from(event.data.graph);
  circular.assign(graph, { scale: 100 });
  const settings = forceAtlas2.inferSettings(graph);
  const large = graph.order > 1000;
  forceAtlas2.assign(graph, {
    iterations: !large ? 600 : graph.order > 3000 ? 200 : 300,
    settings: { ...settings, gravity: 1.2, scalingRatio: event.data.scalingRatio, barnesHutOptimize: large, adjustSizes: false },
  });
  const positions: LayoutReply = {};
  graph.forEachNode((node, attrs) => { positions[node] = { x: attrs.x as number, y: attrs.y as number }; });
  self.postMessage(positions);
};
