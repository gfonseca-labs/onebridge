"""Attach points to directed road edges and map closures onto the split pieces."""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise

from pyproj import CRS
from shapely.geometry import LineString, Point
from shapely.ops import substring

from .ranking import require_id, require_number

CRS_UTM18N = CRS.from_epsg(32618)
ENDPOINT_TOLERANCE_M = 0.01


def attach_points(graph, requests):
    """Split edges at requested points.

    ``requests`` maps an ID to ``{"edge": (u, v, key), "fraction": f}``, where
    ``f`` is measured from ``u`` towards ``v``. Only the named direction is
    split; the reverse carriageway needs its own request. Geometry and node
    ``x``/``y`` must be in EPSG:32618.

    Returns ``(split_graph, attachments, descendants)``: a copy of the graph
    with the edges split, ``{id: node}``, and ``{original_edge: [pieces]}``.
    Each piece carries ``original_edge``; time and length are divided by
    fraction so that the pieces sum exactly to the original edge.
    """
    if CRS.from_user_input(graph.graph.get("crs")) != CRS_UTM18N:
        raise ValueError("attachment geometry must be in EPSG:32618")
    grouped = defaultdict(list)
    for identity, request in requests.items():
        require_id(identity, "attachment id")
        edge, fraction = request["edge"], request["fraction"]
        if not isinstance(edge, tuple) or len(edge) != 3 or not graph.has_edge(*edge):
            raise ValueError(f"attachment {identity} references an unknown edge")
        fraction = require_number(fraction, "attachment fraction")
        if fraction > 1:
            raise ValueError("attachment fraction must be between 0 and 1")
        grouped[edge].append((fraction, identity))

    result = graph.copy()
    attachments, descendants = {}, {}
    for edge, points in grouped.items():
        u, v, _ = edge
        data = dict(graph.edges[edge])
        start = (graph.nodes[u]["x"], graph.nodes[u]["y"])
        finish = (graph.nodes[v]["x"], graph.nodes[v]["y"])
        line = _oriented_line(data.get("geometry", LineString([start, finish])), start)
        if (
            Point(line.coords[0]).distance(Point(start)) > ENDPOINT_TOLERANCE_M
            or Point(line.coords[-1]).distance(Point(finish)) > ENDPOINT_TOLERANCE_M
        ):
            raise ValueError(f"geometry endpoints of {edge} disagree with the graph")
        seconds = require_number(data.get("travel_time"), "edge travel_time")
        length = require_number(data.get("length"), "edge length")
        if seconds <= 0 or length <= 0:
            raise ValueError(f"edge {edge} has nonpositive length or time")

        fractions = sorted({fraction for fraction, _ in points} | {0.0, 1.0})
        nodes = {0.0: u, 1.0: v}
        for fraction in fractions[1:-1]:
            first = min(identity for f, identity in points if f == fraction)
            node = "onebridge-attachment:" + first
            if node in result:
                raise ValueError(f"attachment node {node} already exists")
            location = line.interpolate(fraction, normalized=True)
            result.add_node(node, x=location.x, y=location.y, synthetic_attachment=True)
            nodes[fraction] = node
        for fraction, identity in points:
            attachments[identity] = nodes[fraction]

        result.remove_edge(*edge)
        pieces = []
        used_time = used_length = 0.0
        last = len(fractions) - 2
        for index, (left, right) in enumerate(pairwise(fractions)):
            part = dict(data)
            # The last piece takes the remainder so the pieces sum exactly.
            if index == last:
                part["travel_time"] = seconds - used_time
                part["length"] = length - used_length
            else:
                part["travel_time"] = seconds * (right - left)
                part["length"] = length * (right - left)
            if part["travel_time"] <= 0 or part["length"] <= 0:
                raise ValueError(f"splitting {edge} produced a nonpositive piece")
            part["geometry"] = substring(line, left, right, normalized=True)
            part["original_edge"] = edge
            key = result.add_edge(nodes[left], nodes[right], **part)
            pieces.append((nodes[left], nodes[right], key))
            used_time += part["travel_time"]
            used_length += part["length"]
        descendants[edge] = pieces
    return result, attachments, descendants


def _oriented_line(line, start):
    if not isinstance(line, LineString) or not math.isfinite(line.length):
        raise ValueError("invalid edge geometry")
    if line.length <= 0:
        raise ValueError("invalid edge geometry")
    first, last = Point(line.coords[0]), Point(line.coords[-1])
    if first.distance(Point(start)) > last.distance(Point(start)):
        return LineString(list(line.coords)[::-1])
    return line


def expand_closures(graph, split_graph, closures, descendants):
    """Map each closure's original edges onto every piece of the split graph.

    A closure that names a split edge closes all of its pieces; a missing or
    partial mapping is an error rather than an open remnant.
    """
    represented = defaultdict(list)
    for u, v, key, data in split_graph.edges(keys=True, data=True):
        if "original_edge" in data:
            original = data["original_edge"]
            if not isinstance(original, tuple) or len(original) != 3:
                raise ValueError("invalid original_edge on a split piece")
            represented[original].append((u, v, key))
    for original, pieces in descendants.items():
        if not graph.has_edge(*original):
            raise ValueError(f"descendants reference unknown edge {original}")
        if not pieces or len(set(pieces)) != len(pieces):
            raise ValueError(f"empty or duplicate pieces for {original}")
        if set(pieces) != set(represented.get(original, [])):
            raise ValueError(f"pieces of {original} are incomplete or stale")
        if (
            pieces[0][0] != original[0]
            or pieces[-1][1] != original[1]
            or any(a[1] != b[0] for a, b in pairwise(pieces))
        ):
            raise ValueError(f"pieces of {original} do not form a chain")
    if set(represented) != set(descendants):
        raise ValueError("split pieces without a descendants entry")

    expanded = {}
    for identity, edges in closures.items():
        require_id(identity, "closure_id")
        edges = tuple(edges)
        if not edges or len(set(edges)) != len(edges):
            raise ValueError(f"closure {identity} is empty or repeats an edge")
        pieces = []
        for edge in edges:
            if (
                not isinstance(edge, tuple)
                or len(edge) != 3
                or not graph.has_edge(*edge)
            ):
                raise ValueError(f"closure {identity} references unknown edge {edge}")
            if edge in descendants:
                pieces.extend(descendants[edge])
            elif split_graph.has_edge(*edge):
                pieces.append(edge)
            else:
                raise ValueError(f"edge {edge} vanished from the split graph")
        if len(set(pieces)) != len(pieces):
            raise ValueError(f"closure {identity} has overlapping pieces")
        expanded[identity] = pieces
    return expanded
