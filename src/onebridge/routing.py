"""Nearest-hospital routing with recorded paths."""

from __future__ import annotations

import heapq
from itertools import count

from .ranking import require_number


def routes_with_paths(graph, hospitals, origins):
    """Route every origin to its nearest hospital and record the path.

    ``hospitals`` and ``origins`` map IDs to tuples of graph nodes. One
    multi-source Dijkstra runs backwards from all hospital nodes. Labels are
    ``(seconds, hospital_id)`` compared lexicographically, so equal times go
    to the smaller hospital ID; among parallel edges the cheapest is used.

    Returns ``{origin_id: {"status", "hospital_id", "travel_time_seconds",
    "path_edges"}}`` with ``path_edges`` in travel order.
    """
    best, successor, seeds = {}, {}, {}
    queue, sequence = [], count()
    reverse = graph.reverse(copy=False)
    for hid, nodes in hospitals.items():
        for node in nodes:
            label = (0.0, hid)
            if node not in best or label < best[node]:
                best[node] = label
                seeds[node] = hid
                successor.pop(node, None)
                heapq.heappush(queue, (0.0, hid, next(sequence), node))
    while queue:
        seconds, hid, _, node = heapq.heappop(queue)
        if best.get(node) != (seconds, hid):
            continue
        for neighbor, parallels in reverse[node].items():
            key, data = min(
                parallels.items(), key=lambda kv: (float(kv[1]["travel_time"]), kv[0])
            )
            label = (
                require_number(seconds + float(data["travel_time"]), "route time"),
                hid,
            )
            if neighbor not in best or label < best[neighbor]:
                best[neighbor] = label
                successor[neighbor] = (neighbor, node, key)
                seeds.pop(neighbor, None)
                heapq.heappush(queue, (*label, next(sequence), neighbor))

    routes = {}
    for oid, nodes in origins.items():
        reached = [(best[n], n) for n in nodes if n in best]
        if not reached:
            routes[oid] = {
                "status": "unreachable",
                "hospital_id": None,
                "travel_time_seconds": None,
                "path_edges": [],
            }
            continue
        label, node = min(reached)
        path = []
        while node in successor:
            edge = successor[node]
            path.append(edge)
            node = edge[1]
        if seeds.get(node) != label[1]:
            raise ValueError(f"path of {oid} does not end at {label[1]}")
        routes[oid] = {
            "status": "reachable",
            "hospital_id": label[1],
            "travel_time_seconds": label[0],
            "path_edges": path,
        }
    return routes


def original_movements(graph, path_edges):
    """Collapse split pieces on a path back to the original edges, in order."""
    movements = []
    for u, v, k in path_edges:
        original = graph.edges[u, v, k].get("original_edge", (u, v, k))
        if not movements or movements[-1] != original:
            movements.append(original)
    return movements
