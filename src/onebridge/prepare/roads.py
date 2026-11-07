"""Usable road edges and their travel times.

Inputs, per routing buffer:

* the road extract: every directed edge of the OSM drive-and-service graph
  with its ``highway`` class, raw ``maxspeed``, length, geometry and source
  ways;
* the road review table: whether each edge is usable and, if not, why.
  The review is a curated dataset (access tags, barriers, direction,
  structures, jurisdiction and source checks), not something these rules
  re-derive. It also lists the few edges whose speed was set by review:
  signed limits from NYC DOT and crossings costed segment by segment.

Speeds follow one order: reviewed exception, then a posted ``maxspeed``,
then the configured fallback for the road class and area (special roads,
New York City, or the outer area). Travel time is ``3.6 * length / kph``.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import tomllib
from pathlib import Path

from shapely import from_wkt, prepare, transform
from shapely.geometry import LineString

from ..study.bundle import to_utm

MPH = 1.609344
ORDINARY = frozenset(
    {"residential", "unclassified", "tertiary", "secondary", "primary"}
)
SLOW = frozenset({"service", "living_street"})
LINKS = frozenset(
    {"motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
)
_POSTED = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*", re.I | re.ASCII)


def load_speeds(config_path):
    """``{scenario: {setting: mph}}`` with every scenario filled from baseline."""
    config = tomllib.loads(Path(config_path).read_text())["speeds"]
    baseline = config["baseline"]
    return {name: {**baseline, **values} for name, values in config.items()}


def posted_kph(maxspeed):
    """A single numeric posted limit in km/h, or ``None``."""
    if isinstance(maxspeed, str):
        match = _POSTED.fullmatch(maxspeed)
        if match:
            value = float(match[1])
            kph = value * (MPH if (match[2] or "").lower() == "mph" else 1)
            if math.isfinite(kph) and kph > 0:
                return kph
    return None


def fallback_mph(highway, in_nyc, speeds):
    """Configured speed for a class with no posted limit, or ``None``."""
    if highway in ("motorway", "trunk"):
        return speeds[highway]
    if highway in LINKS:
        return speeds["link"]
    area = "nyc" if in_nyc else "outer"
    if highway in ORDINARY:
        return speeds[f"{area}_ordinary"]
    if highway in SLOW:
        return speeds[f"{area}_slow"]
    return None


def edge_seconds(row, review, scenarios, in_nyc):
    """Travel time per scenario for one usable edge."""
    exception = review.get("speed")
    if exception and exception["rule"] == "segments":
        return {s: exception["seconds"][s] for s in scenarios}
    if exception and exception["rule"] == "signed":
        kph = {s: exception["mph"] * MPH for s in scenarios}
    elif (posted := posted_kph(row["maxspeed"])) is not None:
        kph = dict.fromkeys(scenarios, posted)
    else:
        nyc = in_nyc()
        kph = {}
        for name, speeds in scenarios.items():
            mph = fallback_mph(row["highway"], nyc, speeds)
            if mph is None:
                raise ValueError(f"no speed rule for {row['edge']} ({row['highway']})")
            kph[name] = mph * MPH
    return {s: 3.6 * row["length"] / kph[s] for s in scenarios}


def usable_edges(extract, review, nodes, nyc_boundary, scenarios):
    """Usable edges with travel times, in routing order.

    Routing order is ``(str(u), str(v), key)``; the routers break exact ties
    by insertion order, so the order is part of the result.
    """
    project = to_utm()
    prepare(nyc_boundary)
    decisions = {tuple(r["edge"]): r for r in review}
    rows = []
    for row in extract:
        edge = tuple(row["edge"])
        decision = decisions.pop(edge, None)
        if decision is None:
            raise ValueError(f"edge {edge} has no review decision")
        if not decision["usable"]:
            continue

        def in_nyc(row=row, edge=edge):
            wkt = row["geometry"]
            line = from_wkt(wkt) if wkt else LineString([nodes[n] for n in edge[:2]])
            return nyc_boundary.intersects(transform(line, project, interleaved=False))

        rows.append(
            {
                "edge": list(edge),
                "length": row["length"],
                "geometry": row["geometry"],
                "ways": row["ways"],
                "seconds": edge_seconds(row, decision, scenarios, in_nyc),
            }
        )
    if decisions:
        raise ValueError(f"{len(decisions)} review decisions name unknown edges")
    rows.sort(key=lambda r: (str(r["edge"][0]), str(r["edge"][1]), r["edge"][2]))
    return rows


def read_lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def read_table(path):
    """An extraction table (``osm/ways.json.gz`` or ``osm/nodes.json.gz``)
    keyed by integer OSM ID."""
    with gzip.open(path, "rt") as f:
        return {int(k): v for k, v in json.load(f).items()}


def read_nodes(path):
    """``{node_id: [lon, lat]}`` from the extraction's ``osm/nodes.json.gz``."""
    with gzip.open(path, "rt") as f:
        return {int(k): [v["lon"], v["lat"]] for k, v in json.load(f).items()}
