"""Single-closure evaluation.

``evaluate_closures`` is the reference method: remove each closure's edges and
route every origin again. ``evaluate_local`` gives identical results much
faster: an origin whose baseline route uses none of the closed edges cannot
change its ``(seconds, hospital_id)`` label, so only origins whose route
crosses the closure are re-solved, each with a forward Dijkstra that stops
once its best label is settled.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from itertools import count

import networkx as nx

from .ranking import rank_closures, require_id, require_number

OUTCOME_KEYS = ("status", "hospital_id", "travel_time_seconds")
UNREACHABLE = {
    "status": "unreachable",
    "hospital_id": None,
    "travel_time_seconds": None,
}


def evaluate_local(graph, hospitals, origins, closures, baseline):
    """Evaluate closures using recorded baseline routes.

    ``hospitals`` and ``origins`` map IDs to node tuples, ``closures`` maps IDs
    to edge lists and ``baseline`` is the output of
    :func:`onebridge.routing.routes_with_paths`.

    Returns ``{"ranking": [...], "affected_route_outcomes": [...]}``; outcomes
    are listed only for origins whose route used a closed edge.
    """
    hospital_at = {}
    for hid, nodes in hospitals.items():
        for node in nodes:
            hospital_at.setdefault(node, []).append(hid)
    users = {}
    for oid, route in baseline.items():
        for edge in route["path_edges"]:
            users.setdefault(tuple(edge), set()).add(oid)
    reachable = sum(r["status"] == "reachable" for r in baseline.values())
    if not reachable:
        raise ValueError("no origin reaches a hospital in the baseline graph")

    rows, records = [], []
    for cid in sorted(closures):
        closed = {tuple(e) for e in closures[cid]}
        if not closed or any(not graph.has_edge(*e) for e in closed):
            raise ValueError(f"closure {cid} is empty or names an unknown edge")
        affected = set().union(*(users.get(e, set()) for e in closed))
        deltas, disconnected = [], 0
        for oid in sorted(origins):
            before = {k: baseline[oid][k] for k in OUTCOME_KEYS}
            if oid in affected:
                label = _best_label(graph, origins[oid], hospital_at, closed)
                after = (
                    {
                        "status": "reachable",
                        "hospital_id": label[1],
                        "travel_time_seconds": label[0],
                    }
                    if label
                    else dict(UNREACHABLE)
                )
            else:
                after = dict(before)
            delta, lost = _compare(before, after)
            disconnected += lost
            if delta is not None:
                deltas.append(delta)
            if oid in affected:
                records.append(_record(cid, oid, before, after, delta, lost))
        rows.append(_summary(cid, deltas, disconnected, reachable, len(origins)))
    return {"ranking": rank_closures(rows), "affected_route_outcomes": records}


def evaluate_closures(graph, hospitals, origins, closures):
    """Reference evaluation: re-route every origin with each closure removed.

    ``hospitals`` and ``origins`` map IDs to node tuples and ``closures`` maps
    IDs to ``(u, v, key)`` edges. Returns ``{"ranking": [...],
    "route_outcomes": [...]}`` with one outcome per closure and origin.
    """
    if not isinstance(graph, nx.MultiDiGraph):
        raise ValueError("expected a networkx MultiDiGraph")
    for *_, data in graph.edges(keys=True, data=True):
        if require_number(data.get("travel_time"), "edge travel_time") == 0:
            raise ValueError("edge travel_time must be positive")
    hospitals = _node_options(graph, hospitals, "hospital_id")
    origins = _node_options(graph, origins, "origin_id")
    if not isinstance(closures, Mapping) or not closures:
        raise ValueError("no closures given")
    for cid, edges in closures.items():
        require_id(cid, "closure_id")
        if not edges or len(set(edges)) != len(edges):
            raise ValueError(f"closure {cid} is empty or repeats an edge")
        for edge in edges:
            if (
                not isinstance(edge, tuple)
                or len(edge) != 3
                or not graph.has_edge(*edge)
            ):
                raise ValueError(f"closure {cid} names unknown edge {edge!r}")

    baseline = _nearest(graph, hospitals, origins)
    reachable = sum(r["status"] == "reachable" for r in baseline.values())
    if not reachable:
        raise ValueError("no origin reaches a hospital in the baseline graph")
    rows, records = [], []
    for cid in sorted(closures):
        scenario_graph = graph.copy()
        scenario_graph.remove_edges_from(closures[cid])
        scenario = _nearest(scenario_graph, hospitals, origins)
        deltas, disconnected = [], 0
        for oid in origins:
            delta, lost = _compare(baseline[oid], scenario[oid])
            disconnected += lost
            if delta is not None:
                deltas.append(delta)
            records.append(_record(cid, oid, baseline[oid], scenario[oid], delta, lost))
        rows.append(_summary(cid, deltas, disconnected, reachable, len(origins)))
    return {"ranking": rank_closures(rows), "route_outcomes": records}


def best_route(graph, start_nodes, hospital_at, closed):
    """Return ``((seconds, hospital_id), path_edges)`` with ``closed`` removed.

    ``hospital_at`` maps graph nodes to hospital IDs. Returns ``(None, [])``
    when no hospital is reachable.
    """
    dist, pred, queue, sequence = {}, {}, [], count()
    for node in start_nodes:
        heapq.heappush(queue, (0.0, next(sequence), node, None))
    best = None
    while queue:
        seconds, _, node, via = heapq.heappop(queue)
        if node in dist:
            continue
        if best is not None and seconds > best[0]:
            break
        dist[node], pred[node] = seconds, via
        for hid in hospital_at.get(node, ()):
            if best is None or (seconds, hid) < best[:2]:
                best = (seconds, hid, node)
        for neighbor, parallels in graph.succ[node].items():
            if neighbor in dist:
                continue
            open_edges = [
                (float(d["travel_time"]), k)
                for k, d in parallels.items()
                if (node, neighbor, k) not in closed
            ]
            if open_edges:
                cost, key = min(open_edges)
                heapq.heappush(
                    queue,
                    (
                        require_number(seconds + cost, "route time"),
                        next(sequence),
                        neighbor,
                        (node, neighbor, key),
                    ),
                )
    if best is None:
        return None, []
    path, node = [], best[2]
    while pred[node] is not None:
        path.append(pred[node])
        node = pred[node][0]
    return best[:2], path[::-1]


def _best_label(graph, start_nodes, hospital_at, closed):
    """Smallest ``(seconds, hospital_id)`` reachable from any start node."""
    dist, queue, sequence = {}, [], count()
    for node in start_nodes:
        heapq.heappush(queue, (0.0, next(sequence), node))
    best = None
    while queue:
        seconds, _, node = heapq.heappop(queue)
        if node in dist:
            continue
        if best is not None and seconds > best[0]:
            break
        dist[node] = seconds
        for hid in hospital_at.get(node, ()):
            if best is None or (seconds, hid) < best:
                best = (seconds, hid)
        for neighbor, parallels in graph.succ[node].items():
            if neighbor in dist:
                continue
            costs = [
                float(d["travel_time"])
                for k, d in parallels.items()
                if (node, neighbor, k) not in closed
            ]
            if costs:
                cost = require_number(seconds + min(costs), "route time")
                heapq.heappush(queue, (cost, next(sequence), neighbor))
    return best


def _nearest(graph, hospitals, origins):
    """Nearest-hospital outcome for each origin (no paths)."""
    best, queue, sequence = {}, [], count()
    reverse = graph.reverse(copy=False)
    for hid, nodes in hospitals.items():
        for node in nodes:
            label = (0.0, hid)
            if node not in best or label < best[node]:
                best[node] = label
                heapq.heappush(queue, (0.0, hid, next(sequence), node))
    while queue:
        seconds, hid, _, node = heapq.heappop(queue)
        if best.get(node) != (seconds, hid):
            continue
        for neighbor, parallels in reverse[node].items():
            cost = min(float(d["travel_time"]) for d in parallels.values())
            label = (require_number(seconds + cost, "route time"), hid)
            if neighbor not in best or label < best[neighbor]:
                best[neighbor] = label
                heapq.heappush(queue, (*label, next(sequence), neighbor))
    outcomes = {}
    for oid, nodes in origins.items():
        chosen = min((best[n] for n in nodes if n in best), default=None)
        outcomes[oid] = (
            {
                "status": "reachable",
                "hospital_id": chosen[1],
                "travel_time_seconds": chosen[0],
            }
            if chosen
            else dict(UNREACHABLE)
        )
    return outcomes


def _node_options(graph, values, label):
    if not isinstance(values, Mapping) or not values:
        raise ValueError(f"no {label} attachments given")
    options = {}
    for identity, nodes in sorted(values.items()):
        require_id(identity, label)
        nodes = tuple(nodes)
        if (
            not nodes
            or len(set(nodes)) != len(nodes)
            or any(n not in graph for n in nodes)
        ):
            raise ValueError(f"{label} {identity} has missing or unknown nodes")
        options[identity] = nodes
    return options


def _compare(before, after):
    """Return (added minutes or None, newly disconnected)."""
    b, s = before["travel_time_seconds"], after["travel_time_seconds"]
    if (b is None and s is not None) or (b is not None and s is not None and s < b):
        raise ValueError("a closure made a route faster")
    delta = (s - b) / 60 if b is not None and s is not None else None
    if delta is not None:
        require_number(delta, "finite_added_minutes")
    return delta, b is not None and s is None


def _record(cid, oid, before, after, delta, lost):
    return {
        "closure_id": cid,
        "origin_id": oid,
        "baseline": dict(before),
        "scenario": dict(after),
        "finite_added_minutes": delta,
        "newly_disconnected": lost,
    }


def _summary(cid, deltas, disconnected, reachable, origin_count):
    total = require_number(math.fsum(deltas), "total_finite_added_minutes")
    return {
        "closure_id": cid,
        "total_finite_added_minutes": total,
        "mean_finite_added_minutes": total / len(deltas) if deltas else None,
        "finite_comparable_origins": len(deltas),
        "baseline_reachable_origins": reachable,
        "baseline_unreachable_origins": origin_count - reachable,
        "newly_disconnected_origins": disconnected,
        "newly_disconnected_share": disconnected / reachable,
        "retained_origins": origin_count,
    }
