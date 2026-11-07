"""Trip origins: a regular grid, a review table, and where each origin joins
the road network.

Candidate origins are the points of a square grid in UTM zone 18N metres
(EPSG:32618, anchored at zero) that fall inside the study rectangle. Each
spacing has a review table that marks every candidate as

* ``clear``: the point's surroundings in OpenStreetMap raise no concern;
* ``building_context``: held only because of nearby buildings, then kept
  after review;
* ``held``: excluded, with the reasons (open water, private or managed
  land, barriers, grade separation, a long or water-crossing connector).

The review is curated data; the rules below do not re-derive it.

A kept origin departs from the nearest usable road edge, split at the
closest point on it. Exact distance ties (the two directions of a two-way
street) go to the edge that comes first in routing order. Where the
opposite direction is also usable, the origin may depart that way too.

Census 2020 block populations give each kept origin a weight: a block counts
toward the grid cell, centred on a grid point, that contains its internal
point.
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

from shapely import STRtree, from_wkt, transform
from shapely.geometry import LineString, Point

from ..study.bundle import to_utm

STATUSES = ("clear", "building_context", "held")


def candidate_grid(bbox, spacing):
    """Grid points inside ``bbox`` as ``{origin_id: (easting, northing, lon, lat)}``.

    Ordered by origin ID.
    """
    from pyproj import Transformer
    from shapely.geometry import box

    unproject = Transformer.from_crs(32618, 4326, always_xy=True).transform
    west, south, east, north = bbox
    xmin, ymin, xmax, ymax = transform(box(*bbox), to_utm(), interleaved=False).bounds
    points = {}
    for i in range(math.floor(xmin / spacing), math.ceil(xmax / spacing) + 1):
        for j in range(math.floor(ymin / spacing), math.ceil(ymax / spacing) + 1):
            easting, northing = i * spacing, j * spacing
            lon, lat = unproject(easting, northing)
            if west <= lon <= east and south <= lat <= north:
                points[f"utm18n-{spacing}-{i}-{j}"] = (easting, northing, lon, lat)
    return dict(sorted(points.items()))


def kept_origins(grid, review):
    """Kept origin IDs: ``clear`` ones, then ``building_context`` ones.

    The review must cover the grid exactly.
    """
    status = {}
    for row in review:
        if row["status"] not in STATUSES:
            raise ValueError(f"unknown origin status {row['status']!r}")
        if row["origin"] in status:
            raise ValueError(f"origin {row['origin']} is reviewed twice")
        status[row["origin"]] = row["status"]
    if status.keys() != grid.keys():
        raise ValueError("the origin review does not match the candidate grid")
    return [o for s in STATUSES[:2] for o in sorted(o for o in grid if status[o] == s)]


def departures(edges, nodes, points):
    """``[[origin_id, edge, fraction], ...]`` from the nearest usable edge.

    ``edges`` are usable edges in routing order; an edge without geometry is
    the straight line between its end nodes (``nodes``: ``{id: [lon, lat]}``).
    ``points`` maps origin IDs to UTM ``(easting, northing)``.
    """
    lines = transform(
        [
            from_wkt(e["geometry"])
            if e["geometry"]
            else LineString([nodes[e["edge"][0]], nodes[e["edge"][1]]])
            for e in edges
        ],
        to_utm(),
        interleaved=False,
    )
    tree = STRtree(lines)
    rows = []
    for origin, (x, y) in points.items():
        point = Point(x, y)
        nearest = min(tree.query_nearest(point, all_matches=True))
        line = lines[nearest]
        rows.append([origin, edges[nearest]["edge"], line.project(point) / line.length])
    return rows


def reverse_departures(rows, usable):
    """Departures along the opposite direction of each edge, where usable.

    Ordered by origin ID.
    """
    reverse = []
    for origin, (u, v, key), fraction in rows:
        if (v, u, key) in usable:
            reverse.append([origin, [v, u, key], 1 - fraction])
    return sorted(reverse, key=lambda r: r[0])


def read_blocks(paths, bbox, margin):
    """``[(population, easting, northing), ...]`` for blocks near ``bbox``.

    A block is located by its Census internal point.
    """
    from pyogrio.raw import read

    project = to_utm()
    west, south, east, north = bbox
    blocks = []
    for path in paths:
        meta, _, _, values = read(
            f"zip://{path}",
            columns=["POP20", "INTPTLAT20", "INTPTLON20"],
            read_geometry=False,
        )
        field = dict(zip(meta["fields"], values, strict=True))
        columns = field["POP20"], field["INTPTLAT20"], field["INTPTLON20"]
        for p, y, x in zip(*columns, strict=True):
            y, x = float(y), float(x)
            if (
                west - margin <= x <= east + margin
                and south - margin <= y <= north + margin
            ):
                blocks.append((int(p), *project(x, y)))
    return blocks


def cell_population(blocks, grid, spacing):
    """Population of the grid cell around each candidate origin."""
    cell = {
        (round(x / spacing), round(y / spacing)): origin
        for origin, (x, y, _, _) in grid.items()
    }
    population = dict.fromkeys(grid, 0)
    for p, x, y in blocks:
        origin = cell.get((round(x / spacing), round(y / spacing)))
        if origin is not None:
            population[origin] += p
    return population


def prepare_origins(config, review_folder, edges, nodes, blocks):
    """Origin coordinates, weights and departures for every spacing.

    ``edges`` maps each routing buffer to its usable edges. Returns
    ``(origins, departures_by_buffer)`` in the bundle's ``origins.json`` and
    ``endpoints.json["origins"]`` layouts.
    """
    bbox = config["area"]["origin_bbox"]
    coordinates, population, points = {}, {}, {}
    by_buffer = {buffer: {} for buffer in edges}
    for spacing in config["origins"]["spacings_m"]:
        grid = candidate_grid(bbox, spacing)
        with gzip.open(Path(review_folder) / f"{spacing}-review.jsonl.gz", "rt") as f:
            kept = kept_origins(grid, [json.loads(line) for line in f])
        coordinates[str(spacing)] = {o: [p[2], p[3]] for o, p in grid.items()}
        weights = cell_population(blocks, grid, spacing)
        population[str(spacing)] = {o: weights[o] for o in sorted(kept)}
        points[str(spacing)] = {o: grid[o][:2] for o in kept}
    for buffer, rows in edges.items():
        usable = {tuple(e["edge"]) for e in rows}
        forward = departures(
            rows, nodes, {o: p for group in points.values() for o, p in group.items()}
        )
        start = 0
        for spacing, group in points.items():
            part, start = forward[start : start + len(group)], start + len(group)
            by_buffer[buffer][spacing] = {
                "departures": part,
                "reverse": reverse_departures(part, usable),
            }
    return {"coordinates": coordinates, "population_2020": population}, by_buffer
