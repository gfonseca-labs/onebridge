"""Study network: the road graph with origins and hospitals attached."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
from shapely import from_wkt, transform
from shapely.geometry import LineString

from ..graph import attach_points
from .bundle import to_utm


@dataclass
class Network:
    """One buffer, speed scenario and origin grid, ready to route.

    ``graph`` is the road graph, ``split`` the same graph with endpoints
    attached, ``pieces`` maps split edges to their pieces, and ``hospitals``
    and ``origins`` map IDs to node tuples in ``split``.
    """

    buffer: str
    speeds: str
    spacing: int
    graph: nx.MultiDiGraph
    split: nx.MultiDiGraph
    pieces: dict
    hospitals: dict
    origins: dict


def road_graph(bundle, buffer, speeds):
    """Directed road graph in UTM metres with ``travel_time`` and ``length``.

    Only edges that endpoints attach to carry a geometry; the rest are never
    split and do not need one.
    """
    nodes = bundle.nodes()
    endpoints = bundle.endpoints(buffer)
    attached = _attachment_edges(endpoints)
    project = to_utm()
    graph = nx.MultiDiGraph(crs="EPSG:32618")
    for row in bundle.edges(buffer):
        edge = tuple(row["edge"])
        for n in edge[:2]:
            if n not in graph:
                x, y = project(*nodes[n])
                graph.add_node(n, x=x, y=y)
        attrs = {"travel_time": row["seconds"][speeds], "length": row["length"]}
        if edge in attached:
            wkt = row["geometry"]
            line = from_wkt(wkt) if wkt else LineString([nodes[n] for n in edge[:2]])
            attrs["geometry"] = transform(line, project, interleaved=False)
        graph.add_edge(*edge[:2], key=edge[2], **attrs)
    missing = {n for ns in endpoints["hospital_nodes"].values() for n in ns} - set(
        graph
    )
    if missing:
        raise ValueError(f"hospital arrival nodes missing from the graph: {missing}")
    if not attached <= set(graph.edges(keys=True)):
        raise ValueError("an endpoint attaches to an edge outside the graph")
    return graph


def attach_endpoints(bundle, buffer, speeds, spacing, graph):
    """Attach the origin grid and every hospital arrival to ``graph``."""
    endpoints = bundle.endpoints(buffer)
    grid = endpoints["origins"][str(spacing)]
    splits, arrival_nodes = endpoints["hospital_splits"], endpoints["hospital_nodes"]

    requests = {}
    for oid, edge, fraction in grid["departures"]:
        if oid in requests:
            raise ValueError(f"origin {oid} is listed twice")
        requests[oid] = {"edge": tuple(edge), "fraction": fraction}
    reverse = {oid: (tuple(edge), f) for oid, edge, f in grid["reverse"]}
    for oid, (edge, fraction) in reverse.items():
        requests[f"{oid}-reverse"] = {"edge": edge, "fraction": fraction}
    for hid, points in splits.items():
        for i, (edge, fraction) in enumerate(points):
            requests[f"ed-{hid}-arrival-{i}"] = {
                "edge": tuple(edge),
                "fraction": fraction,
            }

    split, at, pieces = attach_points(graph, requests)
    hospitals = {
        hid: tuple(
            sorted(
                {at[f"ed-{hid}-arrival-{i}"] for i in range(len(splits.get(hid, [])))}
                | set(arrival_nodes.get(hid, [])),
                key=str,
            )
        )
        for hid in sorted(set(splits) | set(arrival_nodes))
    }
    origins = {
        oid: (at[oid], at[f"{oid}-reverse"]) if oid in reverse else (at[oid],)
        for oid in sorted(oid for oid, _, _ in grid["departures"])
    }
    return Network(buffer, speeds, spacing, graph, split, pieces, hospitals, origins)


def _attachment_edges(endpoints):
    edges = {
        tuple(e) for points in endpoints["hospital_splits"].values() for e, _ in points
    }
    for grid in endpoints["origins"].values():
        edges |= {tuple(e) for _, e, _ in grid["departures"]}
        edges |= {tuple(e) for _, e, _ in grid["reverse"]}
    return edges
