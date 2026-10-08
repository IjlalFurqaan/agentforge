"""Module and component dependency graphs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DependencyGraph:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": self.nodes, "edges": self.edges, "cycles": self.cycles}


def component_of(path: str) -> str:
    """Group files into architectural components by their leading directories."""
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] in {"src", "lib", "app", "packages", "apps", "services", "internal", "pkg"} and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def strongly_connected_components(adj: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's algorithm (iterative). Returns SCCs with more than one node or a self-loop."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for root in sorted(adj):
        if root in index:
            continue
        work = [(root, iter(sorted(adj.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(adj.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == node:
                        break
                if len(scc) > 1 or node in adj.get(node, set()):
                    result.append(sorted(scc))
    return result


def build_graphs(
    file_edges: dict[str, set[str]], file_meta: dict[str, dict[str, Any]]
) -> tuple[DependencyGraph, DependencyGraph]:
    """Return (module graph, component graph) from resolved internal file-level imports."""
    fan_in: Counter[str] = Counter()
    for targets in file_edges.values():
        for t in targets:
            fan_in[t] += 1

    module_graph = DependencyGraph()
    for path, meta in sorted(file_meta.items()):
        module_graph.nodes.append({
            "id": path, "component": component_of(path), "language": meta.get("language"),
            "lines": meta.get("lines", 0), "fan_in": fan_in[path], "fan_out": len(file_edges.get(path, ())),
        })
    for src, targets in sorted(file_edges.items()):
        for t in sorted(targets):
            module_graph.edges.append({"source": src, "target": t})
    module_graph.cycles = strongly_connected_components(file_edges)[:20]

    comp_edges: Counter[tuple[str, str]] = Counter()
    comp_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"files": 0, "lines": 0, "languages": Counter()})
    for path, meta in file_meta.items():
        c = component_of(path)
        comp_stats[c]["files"] += 1
        comp_stats[c]["lines"] += meta.get("lines", 0)
        if meta.get("language"):
            comp_stats[c]["languages"][meta["language"]] += 1
    for src, targets in file_edges.items():
        for t in targets:
            a, b = component_of(src), component_of(t)
            if a != b:
                comp_edges[(a, b)] += 1

    component_graph = DependencyGraph()
    for name, stats in sorted(comp_stats.items()):
        component_graph.nodes.append({
            "id": name, "files": stats["files"], "lines": stats["lines"],
            "primary_language": stats["languages"].most_common(1)[0][0] if stats["languages"] else None,
        })
    for (a, b), weight in sorted(comp_edges.items()):
        component_graph.edges.append({"source": a, "target": b, "weight": weight})
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in comp_edges:
        adj[a].add(b)
    component_graph.cycles = strongly_connected_components(adj)
    return module_graph, component_graph
