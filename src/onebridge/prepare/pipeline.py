"""Assemble a study bundle from the extraction and the source tables.

Inputs:

* ``extract``: the output of ``onebridge extract`` (``roads/`` and ``osm/``);
* ``sources``: the reviewed tables and pinned geography::

      roads/<buffer>-review.jsonl.gz        road review (usable, reasons, speeds)
      origins/<spacing>-review.jsonl.gz     origin review
      hospitals/campuses.geojson            campus outlines
      hospitals/arrivals.jsonl.gz           arrival review
      hospitals/registry.json               state hospital registry
      closures/<buffer>-crossings.jsonl.gz  river-crossing corridors

* ``downloads``: the Census block files and the NYC borough boundaries,
  checked against the SHA-256 values in the configuration.

The bundle layout is described in :mod:`onebridge.study.bundle`. Files are
written with sorted keys and gzip headers without timestamps, so the same
inputs always give the same bytes.
"""

from __future__ import annotations

import gzip
import json
import shutil
import tomllib
from pathlib import Path

from shapely.geometry import shape

from ..study.bundle import read_boundary
from .closures import crossing_corridors, ordinary_candidates, structure_candidates
from .downloads import fetched
from .hospitals import Roads, hospital_arrivals, read_campuses
from .origins import prepare_origins, read_blocks
from .output import write_gzip, write_json, write_lines
from .roads import load_speeds, read_lines, read_table, usable_edges
from .shared import (
    basemap,
    bundle_nodes,
    bundle_ways,
    hospital_locations,
    used_ways,
    way_names,
)


def prepare_bundle(config_path, extract, sources, downloads, out, log=print):
    """Write a new bundle directory ``out``."""
    config = tomllib.loads(Path(config_path).read_text())
    extract, sources, out = Path(extract), Path(sources), Path(out)
    buffers = list(config["area"]["buffers_m"])
    blocks = read_blocks(
        [fetched(downloads, name, config) for name in config["census"]["files"]],
        config["area"]["origin_bbox"],
        config["census"]["margin_deg"],
    )
    log("census:", len(blocks), "blocks near the study area")
    ways = read_table(extract / "osm/ways.json.gz")
    nodes = read_table(extract / "osm/nodes.json.gz")
    points = {n: [r["lon"], r["lat"]] for n, r in nodes.items()}
    boundary = fetched(downloads, config["area"]["nyc_boundary"], config)
    nyc = read_boundary(boundary)
    scenarios = load_speeds(config_path)

    roads, edges = {}, {}
    for buffer in buffers:
        roads[buffer] = read_lines(extract / f"roads/{buffer}-extract.jsonl.gz")
        review = read_lines(sources / f"roads/{buffer}-review.jsonl.gz")
        edges[buffer] = usable_edges(roads[buffer], review, points, nyc, scenarios)
        log(buffer, len(edges[buffer]), "usable edges")

    origins, departures = prepare_origins(
        config, sources / "origins", edges, points, blocks
    )
    campuses = read_campuses(sources / "hospitals/campuses.geojson")
    arrivals = read_lines(sources / "hospitals/arrivals.jsonl.gz")
    endpoints, structures = {}, {}
    out.mkdir(parents=True, exist_ok=False)
    for buffer in buffers:
        usable = {tuple(e["edge"]) for e in edges[buffer]}
        splits, by_node = hospital_arrivals(
            arrivals, campuses, Roads(roads[buffer], usable, ways, points)
        )
        endpoints[buffer] = {
            "hospital_splits": splits,
            "hospital_nodes": by_node,
            "origins": departures[buffer],
        }
        crossings, opened = crossing_corridors(
            read_lines(sources / f"closures/{buffer}-crossings.jsonl.gz")
        )
        structures[buffer] = structure_candidates(roads[buffer], usable, ways, opened)
        ordinary = ordinary_candidates(roads[buffer], usable, ways, nodes)
        folder = out / buffer
        folder.mkdir()
        write_lines(folder / "edges.jsonl.gz", edges[buffer])
        (folder / "endpoints.json").write_text(json.dumps(endpoints[buffer]))
        write_lines(folder / "ordinary.jsonl.gz", ordinary)
        write_lines(folder / "structures.jsonl.gz", structures[buffer])
        write_lines(folder / "crossings.jsonl.gz", crossings)
        log(
            buffer,
            len(ordinary),
            "ordinary,",
            len(structures[buffer]),
            "structure,",
            len(crossings),
            "crossing candidates",
        )

    hospital_nodes = {b: e["hospital_nodes"] for b, e in endpoints.items()}
    used = used_ways(edges, structures)
    write_json(out / "nodes.json.gz", bundle_nodes(edges, hospital_nodes, nodes))
    write_json(out / "ways.json.gz", bundle_ways(used, ways))
    write_json(out / "way-names.json.gz", way_names(used, ways))
    write_lines(out / "primary/basemap.jsonl.gz", basemap(roads["primary"]))
    with gzip.open(extract / "osm/restrictions.jsonl.gz", "rb") as f:
        write_gzip(out / "restrictions.jsonl.gz", f.read())
    shutil.copyfile(boundary, out / "nyc-boundary.geojson")
    (out / "origins.json").write_text(json.dumps(origins))
    outlines = {
        f["properties"]["hospital"]: shape(f["geometry"])
        for f in json.loads((sources / "hospitals/campuses.geojson").read_text())[
            "features"
        ]
    }
    registry = json.loads((sources / "hospitals/registry.json").read_text())
    (out / "hospitals.json").write_text(
        json.dumps(hospital_locations(registry, outlines), sort_keys=True)
    )
    (out / "study.json").write_text(
        json.dumps(
            {
                "kind": "nyc",
                "buffers_m": config["area"]["buffers_m"],
                "spacings_m": config["origins"]["spacings_m"],
            },
            sort_keys=True,
        )
        + "\n"
    )
    log("bundle written to", out)
