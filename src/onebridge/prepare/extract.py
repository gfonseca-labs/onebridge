"""Road graphs from OpenStreetMap extracts.

The Geofabrik New York and New Jersey extracts are read with osmium. Highway
ways with a node inside the query polygon (the study rectangle buffered by
the routing buffer plus OSMnx's 500 m margin) are kept, together with every
node they reference. The graph is
then built by OSMnx's own ``graph_from_polygon`` (``drive_service``,
simplified, ``truncate_by_edge``) fed from those local elements instead of
Overpass, so the result is the graph OSMnx would build for the same data.

OSMnx is pinned to 2.1.1 because the local feed replaces one private
download function whose behaviour was checked for that version.

Turn restrictions are kept whole: every restriction relation in either
extract, in file order, tagged with the extract it came from. A relation on
the state line appears once per extract.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

from .downloads import fetched
from .output import write_json, write_lines

OSMNX_VERSION = "2.1.1"
RESTRICTION_TYPES = {"restriction", "restriction:motorcar", "restriction:motor_vehicle"}
# Way tags kept on graph edges in addition to OSMnx's defaults.
EXTRA_WAY_TAGS = {
    "access",
    "motor_vehicle",
    "motorcar",
    "vehicle",
    "maxspeed:forward",
    "maxspeed:backward",
    "maxspeed:conditional",
    "maxspeed:forward:conditional",
    "maxspeed:backward:conditional",
    "oneway:conditional",
    "access:conditional",
    "motor_vehicle:conditional",
    "motorcar:conditional",
    "vehicle:conditional",
    "layer",
    "bridge",
    "tunnel",
    "service",
    "junction",
    "ref",
}
_RULE = re.compile(r'\["([^"\]]+)"(?:(?!\])(!~)"([^"\]]*)")?\]')


def routing_polygon(origin_bbox, buffer_m):
    """The origin rectangle buffered by ``buffer_m`` metres, in lon/lat."""
    from pyproj import Transformer
    from shapely import transform
    from shapely.geometry import box

    project = Transformer.from_crs(4326, 32618, always_xy=True).transform
    unproject = Transformer.from_crs(32618, 4326, always_xy=True).transform
    projected = transform(box(*origin_bbox), project, interleaved=False)
    return transform(
        projected.buffer(buffer_m, quad_segs=16), unproject, interleaved=False
    )


def way_filter(expression):
    """A predicate for OSMnx's Overpass way filter (presence and ``!~`` rules)."""
    rules = list(_RULE.finditer(expression))
    if not rules or "".join(r.group(0) for r in rules) != expression:
        raise ValueError("unsupported way filter grammar")
    compiled = [(r[1], re.compile(r[3]) if r[2] else None) for r in rules]

    def accepts(tags):
        for key, excluded in compiled:
            if excluded is None:
                if key not in tags:
                    return False
            elif key in tags and excluded.search(tags[key]) is not None:
                return False
        return True

    return accepts


def query_polygon(polygon):
    """OSMnx's Overpass query area for ``polygon`` and its rounded footprint.

    OSMnx buffers the polygon by 500 m, splits it and rounds coordinates to
    six decimals; ways are selected against that rounded footprint.
    """
    from osmnx import _overpass, projection
    from shapely import union_all
    from shapely.geometry import Polygon

    projected, crs = projection.project_geometry(polygon)
    query, _ = projection.project_geometry(
        projected.buffer(500), crs=crs, to_latlong=True
    )
    pieces = []
    for coordinates in _overpass._make_overpass_polygon_coord_strs(query):
        values = [float(v) for v in coordinates.split()]
        pieces.append(
            Polygon([(values[i + 1], values[i]) for i in range(0, len(values), 2)])
        )
    return query, union_all(pieces)


def read_osm(paths, footprint):
    """Highway ways touching ``footprint``, their nodes and turn restrictions.

    Returns ``(elements, restrictions)``: ways and nodes as OSM JSON-style
    dicts, which must be identical wherever both extracts carry them, and
    every turn-restriction relation as a row (see :func:`_restriction`).
    """
    import osmium
    from osmnx import _overpass
    from shapely import intersects_xy

    inside = set()
    xmin, ymin, xmax, ymax = footprint.bounds
    for path in paths:
        for obj in osmium.FileProcessor(str(path), entities=osmium.osm.NODE):
            x, y = obj.location.lon, obj.location.lat
            if (
                xmin <= x <= xmax
                and ymin <= y <= ymax
                and intersects_xy(footprint, x, y)
            ):
                inside.add(obj.id)
    ways = {}
    for path in paths:
        for obj in osmium.FileProcessor(str(path), entities=osmium.osm.WAY):
            if "highway" in obj.tags and any(n.ref in inside for n in obj.nodes):
                _add(ways, _way(obj))
    del inside
    # Compare every copy of a selected way, including copies whose nodes all
    # lie outside the footprint in the other extract.
    for path in paths:
        for obj in osmium.FileProcessor(str(path), entities=osmium.osm.WAY):
            if obj.id in ways:
                _add(ways, _way(obj))
    accepts = way_filter(_overpass._get_network_filter("drive_service"))
    ways = {i: w for i, w in ways.items() if accepts(w["tags"])}
    refs = {n for w in ways.values() for n in w["nodes"]}
    nodes, restrictions = {}, []
    for path in paths:
        region = Path(path).name.removesuffix(".osm.pbf")
        for obj in osmium.FileProcessor(
            str(path), entities=osmium.osm.NODE | osmium.osm.RELATION
        ):
            if obj.is_node() and obj.id in refs:
                _add(nodes, _node(obj))
            elif obj.is_relation() and obj.tags.get("type") in RESTRICTION_TYPES:
                restrictions.append(_restriction(obj, region))
    if refs - nodes.keys():
        raise ValueError(f"{len(refs - nodes.keys())} referenced nodes are missing")
    return list(nodes.values()) + list(ways.values()), restrictions


def build_graph(elements, polygon):
    """OSMnx ``drive_service`` graph for ``polygon`` from local OSM elements."""
    import osmnx as ox
    from osmnx import _overpass
    from shapely.geometry import Point

    if ox.__version__ != OSMNX_VERSION:
        raise RuntimeError(f"OSMnx {OSMNX_VERSION} is required, found {ox.__version__}")
    if (
        ox.settings.all_oneway
        or "drive_service" in ox.settings.bidirectional_network_types
    ):
        raise RuntimeError("OSMnx direction settings have been changed")
    accepts = way_filter(_overpass._get_network_filter("drive_service"))
    nodes, ways = {}, {}
    for element in elements:
        if element["type"] in ("node", "way"):
            store = nodes if element["type"] == "node" else ways
            _add(store, element)
    if any(ref not in nodes for way in ways.values() for ref in way["nodes"]):
        raise ValueError("a way references a missing node")
    query, footprint = query_polygon(polygon)
    selected = [
        w
        for _, w in sorted(ways.items())
        if accepts(w.get("tags", {}))
        and any(
            footprint.covers(Point(nodes[n]["lon"], nodes[n]["lat"]))
            for n in w["nodes"]
        )
    ]
    refs = {n for w in selected for n in w["nodes"]}
    payload = {"elements": [nodes[n] for n in sorted(refs)] + selected}

    def local_feed(requested, network_type, custom_filter):
        if network_type != "drive_service" or custom_filter is not None:
            raise ValueError("unexpected OSMnx download request")
        if not requested.equals_exact(query, 1e-10):
            raise ValueError("OSMnx requested a different area")
        return iter([payload])

    default_tags = ox.settings.useful_tags_way
    ox.settings.useful_tags_way = sorted(set(default_tags) | EXTRA_WAY_TAGS)
    try:
        with patch.object(_overpass, "_download_overpass_network", local_feed):
            return ox.graph_from_polygon(
                polygon,
                network_type="drive_service",
                retain_all=True,
                truncate_by_edge=True,
                simplify=True,
            )
    finally:
        ox.settings.useful_tags_way = default_tags


def edge_rows(graph):
    """One row per directed edge, values as OSMnx writes them to GraphML.

    Merged edges keep list-valued attributes as their string form (for example
    ``"['residential', 'service']"``); such classes have no speed rule. OSMnx
    builds those lists from sets, so their order depends on string hashing;
    they are sorted here to make the extract reproducible.
    """
    rows = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        text = {k: str(_stable(value)) for k, value in data.items()}
        ids = ast.literal_eval(text["osmid"])
        rows.append(
            {
                "edge": [int(u), int(v), int(key)],
                "highway": text.get("highway"),
                "maxspeed": text.get("maxspeed"),
                "length": float(text["length"]),
                "geometry": text.get("geometry"),
                "ways": sorted(ids if isinstance(ids, list) else [ids]),
            }
        )
    return rows


def _stable(value):
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return sorted(value)
    return value


def _add(store, record):
    previous = store.get(record["id"])
    if previous is not None and previous != record:
        raise ValueError(f"extracts disagree on {record['type']} {record['id']}")
    store[record["id"]] = record


def _node(obj):
    return {
        "type": "node",
        "id": obj.id,
        "version": obj.version,
        "lon": obj.location.lon,
        "lat": obj.location.lat,
        "tags": dict(obj.tags),
    }


def _way(obj):
    return {
        "type": "way",
        "id": obj.id,
        "version": obj.version,
        "nodes": [n.ref for n in obj.nodes],
        "tags": dict(obj.tags),
    }


def _restriction(obj, region):
    return {
        "id": obj.id,
        "version": obj.version,
        "tags": dict(obj.tags),
        "members": [
            {"type": m.type, "ref": m.ref, "role": m.role} for m in obj.members
        ],
        "region": region,
    }


def extract_roads(config, downloads, out, log=print):
    """Write road extracts, source ways and nodes, and turn restrictions.

    Outputs under ``out``:
      roads/<buffer>-extract.jsonl.gz  graph edges (see :func:`edge_rows`)
      osm/ways.json.gz                 source ways of graph edges
      osm/nodes.json.gz                every node of those ways
      osm/restrictions.jsonl.gz        every turn-restriction relation
    """
    out = Path(out)
    (out / "roads").mkdir(parents=True, exist_ok=False)
    (out / "osm").mkdir()
    paths = [
        fetched(downloads, f"{n}.osm.pbf", config) for n in config["osm"]["extracts"]
    ]
    bbox, buffers = config["area"]["origin_bbox"], config["area"]["buffers_m"]
    _, footprint = query_polygon(routing_polygon(bbox, max(buffers.values())))
    elements, restrictions = read_osm(paths, footprint)
    log("read", len(elements), "elements and", len(restrictions), "restrictions")
    way_ids = set()
    for buffer, metres in buffers.items():
        rows = edge_rows(build_graph(elements, routing_polygon(bbox, metres)))
        write_lines(out / "roads" / f"{buffer}-extract.jsonl.gz", rows)
        way_ids.update(w for row in rows for w in row["ways"])
        log(buffer, len(rows), "edges")
    ways = {e["id"]: e for e in elements if e["type"] == "way" and e["id"] in way_ids}
    node_ids = {n for w in ways.values() for n in w["nodes"]}
    nodes = {
        e["id"]: e for e in elements if e["type"] == "node" and e["id"] in node_ids
    }
    write_json(
        out / "osm" / "ways.json.gz",
        {
            str(i): {k: w[k] for k in ("version", "tags", "nodes")}
            for i, w in sorted(ways.items())
        },
    )
    write_json(
        out / "osm" / "nodes.json.gz",
        {
            str(i): {k: n[k] for k in ("version", "tags", "lon", "lat")}
            for i, n in sorted(nodes.items())
        },
    )
    write_lines(out / "osm" / "restrictions.jsonl.gz", restrictions)
    log("ways", len(ways), "nodes", len(nodes))
