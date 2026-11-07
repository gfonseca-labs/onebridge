"""Closure candidates: the road pieces whose loss the study tests.

There are three kinds, per routing buffer.

Ordinary segments
    Usable at-grade edges grouped by OSM source: the directed edges that
    share their source ways, their pair of end nodes and their geometry
    (in either direction). Usually one two-way street segment between two
    intersections. The ID is a hash of that shared key, so it is stable
    across runs. Edges on a grade-separated way, or whose ways touch a
    grade-separated node, are not ordinary.

Structures
    Every grade-separated way (bridge, tunnel, ford or non-zero layer) with
    its usable edges. The study groups structures that share a name and a
    node into one closure.

River crossings
    Reviewed corridors (tunnels and bridges across the Hudson, East and
    Harlem rivers) closed as a whole. Edges the crossing review made usable
    belong only to their corridor and are not listed under structures.
"""

from __future__ import annotations

import hashlib
import json

from shapely import from_wkt
from shapely.geometry import LineString


def grade_separated(tags):
    """True for a bridge, tunnel, ford or non-zero layer."""
    return any(
        tags.get(k) not in (None, "no") for k in ("bridge", "tunnel", "ford")
    ) or tags.get("layer") not in (None, "0")


def ordinary_candidates(extract, usable, ways, nodes):
    """Ordinary segment candidates, sorted by ID.

    ``ways`` maps way IDs to ``{"tags", "nodes"}`` and ``nodes`` node IDs to
    ``{"tags", "lon", "lat"}``.
    """
    separated = {i for i, w in ways.items() if grade_separated(w["tags"])}
    touches = {
        i
        for i, w in ways.items()
        if any(grade_separated(nodes[n]["tags"]) for n in w["nodes"])
    }
    groups = {}
    for row in extract:
        edge = tuple(row["edge"])
        if edge not in usable or any(
            w in separated or w in touches for w in row["ways"]
        ):
            continue
        if row["geometry"]:
            line = from_wkt(row["geometry"])
        else:
            line = LineString([(nodes[n]["lon"], nodes[n]["lat"]) for n in edge[:2]])
        coords = tuple(tuple(c) for c in line.coords)
        key = (sorted(edge[:2]), row["ways"], min(coords, coords[::-1]))
        digest = hashlib.sha256(
            json.dumps(key, separators=(",", ":")).encode()
        ).hexdigest()
        group = groups.setdefault(
            digest,
            {
                "closure_id": "ordinary-source-segment-" + digest,
                "edges": [],
                "names": sorted(
                    {ways[w]["tags"].get("name") for w in row["ways"]} - {None}
                ),
                "ways": row["ways"],
            },
        )
        group["edges"].append(list(edge))
    rows = sorted(groups.values(), key=lambda g: g["closure_id"])
    for g in rows:
        g["edges"].sort()
    return rows


def structure_candidates(extract, usable, ways, opened):
    """``[{"way", "name", "edges"}, ...]`` for every grade-separated way, by ID.

    ``opened`` holds edges that belong only to a river-crossing corridor.
    """
    found = {}
    for row in extract:
        edge = tuple(row["edge"])
        for w in row["ways"]:
            if grade_separated(ways[w]["tags"]):
                edges = found.setdefault(w, [])
                if edge in usable and edge not in opened:
                    edges.append(list(edge))
    return [
        {"way": w, "name": ways[w]["tags"].get("name"), "edges": edges}
        for w, edges in sorted(found.items())
    ]


def crossing_corridors(table):
    """Bundle rows for the reviewed crossings, and the set of opened edges."""
    rows = [
        {
            "corridor_id": c["corridor"],
            "river": c["river"],
            "edges": c["edges"],
            "ways": c["ways"],
        }
        for c in table
    ]
    opened = {tuple(e) for c in table for e in c["opened_edges"]}
    return rows, opened
