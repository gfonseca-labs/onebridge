"""Where trips arrive at each hospital.

Each study hospital has a campus outline from OpenStreetMap and a list of
reviewed arrival rows:

* ``entrance`` rows: a point on a usable road (an edge and a fraction along
  it, or an existing node) reviewed against the emergency department's
  published or mapped entrance;
* ``excluded`` rows: campus-frontage options (below) that the context review
  removed, for example because of a mapped access restriction or a grade
  separation at that point.

Campus frontage adds every usable public road that passes within 20 m of the
campus without lying inside it: ``residential`` to ``primary`` class, not on
a bridge, tunnel or non-zero layer. The arrival point is the road point
closest to the campus; within 1 cm of an end of the edge, that end node is
used instead of a split.

A trip may arrive at any of a hospital's points. Entrances come first, in
review order; frontage follows, by hospital and then by edge. A frontage
split on an edge that already carries an entrance is dropped, as is a node
already listed.
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely import from_wkt, transform
from shapely.geometry import LineString, Point, shape
from shapely.ops import nearest_points

from ..study.bundle import to_utm

BORDER_M = 20.0
NODE_TOLERANCE_M = 0.01
SEARCH_M = 60.0
PUBLIC_CLASSES = frozenset(
    {"residential", "unclassified", "tertiary", "secondary", "primary"}
)


def read_campuses(path):
    """``{hospital: polygon}`` in UTM metres from ``campuses.geojson``."""
    project = to_utm()
    features = json.loads(Path(path).read_text())["features"]
    return {
        f["properties"]["hospital"]: transform(
            shape(f["geometry"]), project, interleaved=False
        )
        for f in features
    }


def on_structure(tags):
    """True for a way on a bridge, in a tunnel or on a non-zero layer."""
    return (
        tags.get("bridge") not in (None, "no")
        or tags.get("tunnel") not in (None, "no")
        or tags.get("layer") not in (None, "0")
    )


class Roads:
    """Usable public edges near a campus, with their lines in UTM metres."""

    def __init__(self, extract, usable, ways, nodes):
        self.project = to_utm()
        self.nodes = nodes
        self.start = {}
        self.rows = []
        for row in extract:
            edge = tuple(row["edge"])
            if (
                edge not in usable
                or row["highway"] not in PUBLIC_CLASSES
                or any(on_structure(ways[w]["tags"]) for w in row["ways"])
            ):
                continue
            self.rows.append(row)
            self.start[edge] = Point(self.project(*nodes[edge[0]]))

    def line(self, row):
        """The edge's line from its first node to its second."""
        u, v, _ = row["edge"]
        wkt = row["geometry"]
        line = from_wkt(wkt) if wkt else LineString([self.nodes[u], self.nodes[v]])
        line = transform(line, self.project, interleaved=False)
        if Point(line.coords[0]).distance(self.start[tuple(row["edge"])]) > 0.01:
            line = LineString(list(line.coords)[::-1])
        return line

    def near(self, campus):
        """Rows whose first node lies in the campus's search envelope."""
        box = campus.buffer(SEARCH_M).envelope
        return [r for r in self.rows if box.contains(self.start[tuple(r["edge"])])]


def frontage(campus, roads):
    """Campus-frontage arrival options, sorted by edge.

    Each option is ``{"edge", "fraction"}`` or ``{"edge", "node"}``.
    """
    options = []
    for row in roads.near(campus):
        line = roads.line(row)
        if line.within(campus) or campus.distance(line) > BORDER_M:
            continue
        along = line.project(nearest_points(campus, line)[1])
        edge = row["edge"]
        if along <= NODE_TOLERANCE_M:
            options.append({"edge": edge, "node": edge[0]})
        elif line.length - along <= NODE_TOLERANCE_M:
            options.append({"edge": edge, "node": edge[1]})
        else:
            options.append({"edge": edge, "fraction": along / line.length})
    return sorted(options, key=lambda o: tuple(o["edge"]))


def hospital_arrivals(review, campuses, roads):
    """``(splits, nodes)`` in the bundle's ``endpoints.json`` layout.

    ``splits`` maps hospitals to ``[[edge, fraction], ...]`` and ``nodes``
    to node IDs. Every exclusion must name a frontage option.
    """
    splits, nodes = {}, {}
    excluded = {}
    for row in review:
        hospital = row["hospital"]
        if hospital not in campuses:
            raise ValueError(f"hospital {hospital} has no campus")
        if row["kind"] == "excluded":
            excluded.setdefault(hospital, set()).add(tuple(row["edge"]))
        elif row["kind"] != "entrance":
            raise ValueError(f"unknown arrival kind {row['kind']!r}")
        elif "node" in row:
            nodes.setdefault(hospital, []).append(row["node"])
        else:
            splits.setdefault(hospital, []).append([row["edge"], row["fraction"]])
    for hospital, campus in sorted(campuses.items()):
        options = frontage(campus, roads)
        removed = excluded.pop(hospital, set())
        if removed - {tuple(o["edge"]) for o in options}:
            raise ValueError(f"{hospital}: an exclusion names no frontage option")
        entrances = {tuple(edge) for edge, _ in splits.get(hospital, [])}
        for o in options:
            edge = tuple(o["edge"])
            if edge in removed:
                continue
            if "node" in o:
                if o["node"] not in nodes.setdefault(hospital, []):
                    nodes[hospital].append(o["node"])
            elif edge not in entrances:
                splits.setdefault(hospital, []).append([o["edge"], o["fraction"]])
    return splits, nodes
