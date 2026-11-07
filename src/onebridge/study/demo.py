"""A tiny synthetic bundle for trying OneBridge without any downloads.

A 3 x 2 street grid near Midtown Manhattan, one hospital beside node 6, two
origins and one turn restriction:

    1 - 2 - 3          o-west starts on 1-2, o-south on 4-5
    |   |   |          h-east arrives on 5-6 or at node 6
    4 - 5 - 6          no right turn from 1-2 into 2-5 at node 2

Every street takes 30 seconds in each direction. The data is invented; only
the coordinates are real, so the map has a recognisable place.
`onebridge demo` writes this bundle and runs `run` and `report` on it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..prepare.output import write_json, write_lines
from .bundle import SPEEDS, Bundle

NODES = {
    1: (-73.990, 40.752),
    2: (-73.988, 40.752),
    3: (-73.986, 40.752),
    4: (-73.990, 40.750),
    5: (-73.988, 40.750),
    6: (-73.986, 40.750),
}
STREETS = {  # way id: (from node, to node); every street is two-way
    101: (1, 2),
    102: (2, 3),
    103: (4, 5),
    104: (5, 6),
    105: (1, 4),
    106: (2, 5),
    107: (3, 6),
}
SECONDS = 30.0


def write_bundle(root):
    """Write the demo bundle to a new directory ``root`` and open it."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "nodes.json.gz", {str(n): list(p) for n, p in NODES.items()})
    write_json(root / "ways.json.gz", {str(w): list(e) for w, e in STREETS.items()})
    write_lines(
        root / "restrictions.jsonl.gz",
        [
            {
                "id": 1,
                "tags": {"type": "restriction", "restriction": "no_right_turn"},
                "members": [
                    {"type": "w", "ref": 101, "role": "from"},
                    {"type": "n", "ref": 2, "role": "via"},
                    {"type": "w", "ref": 106, "role": "to"},
                ],
            }
        ],
    )
    square = [[-74.0, 40.74], [-73.97, 40.74], [-73.97, 40.76], [-74.0, 40.76]]
    boundary = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [square + square[:1]]},
            }
        ],
    }
    (root / "nyc-boundary.geojson").write_text(json.dumps(boundary))
    grid = {
        "departures": [["o-west", [1, 2, 0], 0.5], ["o-south", [4, 5, 0], 0.1]],
        "reverse": [["o-west", [2, 1, 0], 0.5]],
    }
    for name in ("primary", "wide"):
        folder = root / name
        folder.mkdir()
        edges, ordinary, basemap = [], [], []
        for way, (a, b) in STREETS.items():
            pair = [[a, b, 0], [b, a, 0]]
            for u, v, k in pair:
                edges.append(
                    {
                        "edge": [u, v, k],
                        "length": 200.0,
                        "geometry": None,
                        "ways": [way],
                        "seconds": dict.fromkeys(SPEEDS, SECONDS),
                    }
                )
                (x0, y0), (x1, y1) = NODES[u], NODES[v]
                basemap.append(
                    {
                        "edge": [u, v, k],
                        "geometry": f"LINESTRING ({x0} {y0}, {x1} {y1})",
                    }
                )
            ordinary.append(
                {
                    "closure_id": f"segment-{way}",
                    "edges": pair,
                    "names": [f"Street {way}"],
                    "ways": [way],
                }
            )
        write_lines(folder / "edges.jsonl.gz", edges)
        write_lines(folder / "ordinary.jsonl.gz", ordinary)
        write_lines(folder / "basemap.jsonl.gz", basemap)
        write_lines(folder / "structures.jsonl.gz", [])
        write_lines(
            folder / "crossings.jsonl.gz",
            [{"corridor_id": "none", "river": "none", "edges": [], "ways": []}],
        )
        (folder / "endpoints.json").write_text(
            json.dumps(
                {
                    "hospital_splits": {"h-east": [[[5, 6, 0], 0.5]]},
                    "hospital_nodes": {"h-east": [6]},
                    "origins": {"500": grid, "250": grid},
                }
            )
        )
    coordinates = {"o-west": [-73.989, 40.752], "o-south": [-73.9898, 40.750]}
    (root / "origins.json").write_text(
        json.dumps(
            {
                "coordinates": {"500": coordinates, "250": coordinates},
                "population_2020": {"500": {"o-west": 100, "o-south": 300}},
            }
        )
    )
    (root / "hospitals.json").write_text(
        json.dumps(
            {"h-east": {"name": "East Hospital", "lon": -73.986, "lat": 40.7498}}
        )
    )
    write_json(root / "way-names.json.gz", {str(w): f"Street {w}" for w in STREETS})
    (root / "map.json").write_text(
        json.dumps(
            {
                "title": "OneBridge demo: synthetic 3 x 2 street grid (red: the closure "
                "that adds the most time)",
                "credit": "Invented demo data; not a study result.",
                "margin_m": 150,
            }
        )
    )
    return Bundle(root)


def run_demo(out, log=print):
    """Write the demo bundle under ``out`` and run the study and report on it."""
    from .report import build_report
    from .runs import run_study

    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    bundle = write_bundle(out / "bundle")
    run_study(bundle, out / "runs", log=log)
    return build_report(bundle, out / "runs", out / "report", log=log)
