// Layered (Sugiyama-style, simplified) SVG layout for dependency graphs. No layout library needed:
// nodes are ranked by longest path from the roots (cycles broken by DFS order), then spread per layer.
"use client";

import { useMemo, useState } from "react";

import type { GraphEdge, GraphNode } from "@/lib/types";

const NODE_W = 168;
const NODE_H = 44;
const GAP_X = 36;
const GAP_Y = 64;

function layers(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const out = new Map<string, string[]>();
  nodes.forEach((n) => out.set(n.id, []));
  edges.forEach((e) => out.get(e.source)?.push(e.target));
  const rank = new Map<string, number>();
  const visiting = new Set<string>();
  const visit = (id: string): number => {
    if (rank.has(id)) return rank.get(id)!;
    if (visiting.has(id)) return 0; // back edge in a cycle
    visiting.add(id);
    const deps = out.get(id) ?? [];
    const r = deps.length ? 1 + Math.max(...deps.map(visit)) : 0;
    visiting.delete(id);
    rank.set(id, r);
    return r;
  };
  nodes.forEach((n) => visit(n.id));
  return rank;
}

export function DependencyGraph({ nodes, edges, cycles = [] }: { nodes: GraphNode[]; edges: GraphEdge[]; cycles?: string[][] }) {
  const [hover, setHover] = useState<string | null>(null);
  const layout = useMemo(() => {
    const rank = layers(nodes, edges);
    const maxRank = Math.max(0, ...rank.values());
    const byLayer: GraphNode[][] = Array.from({ length: maxRank + 1 }, () => []);
    // Dependents on top, dependencies at the bottom.
    nodes.forEach((n) => byLayer[maxRank - (rank.get(n.id) ?? 0)].push(n));
    const width = Math.max(...byLayer.map((l) => l.length), 1) * (NODE_W + GAP_X);
    const pos = new Map<string, { x: number; y: number }>();
    byLayer.forEach((layer, li) => {
      const offset = (width - layer.length * (NODE_W + GAP_X)) / 2;
      layer.forEach((n, i) => pos.set(n.id, { x: offset + i * (NODE_W + GAP_X) + GAP_X / 2, y: li * (NODE_H + GAP_Y) + 16 }));
    });
    return { pos, width, height: byLayer.length * (NODE_H + GAP_Y) + 16 };
  }, [nodes, edges]);

  const inCycle = new Set(cycles.flat());
  const related = hover ? new Set(edges.filter((e) => e.source === hover || e.target === hover).flatMap((e) => [e.source, e.target])) : null;

  if (nodes.length === 0) return <p className="p-6 text-sm text-muted">No internal dependencies detected.</p>;

  return (
    <div className="overflow-auto scrollbar-thin">
      <svg width={layout.width} height={layout.height} className="mx-auto block" role="img" aria-label="Dependency graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="currentColor" className="text-muted" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = layout.pos.get(e.source);
          const b = layout.pos.get(e.target);
          if (!a || !b) return null;
          const x1 = a.x + NODE_W / 2;
          const y1 = a.y + NODE_H;
          const x2 = b.x + NODE_W / 2;
          const y2 = b.y;
          const active = hover && (e.source === hover || e.target === hover);
          const up = y2 <= y1; // edge pointing upward = part of a cycle
          return (
            <path
              key={`${e.source}->${e.target}`}
              d={up ? `M${x1},${a.y} C${x1},${a.y - 40} ${x2},${y2 + NODE_H + 40} ${x2},${y2 + NODE_H}` : `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`}
              fill="none"
              strokeWidth={active ? 2 : Math.min(1 + (e.weight ?? 1) * 0.3, 3)}
              markerEnd="url(#arrow)"
              className={active ? "stroke-[rgb(var(--accent))]" : up ? "stroke-rose-500/70" : "stroke-[rgb(var(--line))]"}
              opacity={hover && !active ? 0.25 : 1}
            />
          );
        })}
        {nodes.map((n) => {
          const p = layout.pos.get(n.id)!;
          const dim = related && !related.has(n.id);
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} opacity={dim ? 0.35 : 1}>
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                className={inCycle.has(n.id) ? "fill-rose-500/10 stroke-rose-500/60" : "fill-[rgb(var(--panel))] stroke-[rgb(var(--line))]"}
                strokeWidth={hover === n.id ? 2 : 1}
              />
              <text x={10} y={18} className="fill-[rgb(var(--ink))] text-[11px] font-medium">
                {n.id.length > 24 ? `…${n.id.slice(-23)}` : n.id}
              </text>
              <text x={10} y={34} className="fill-[rgb(var(--muted))] text-[10px]">
                {n.files !== undefined ? `${n.files} files · ${n.lines ?? 0} lines` : `in ${n.fan_in ?? 0} · out ${n.fan_out ?? 0}`}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
