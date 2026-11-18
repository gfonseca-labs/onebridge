"""Studies of any place, from OpenStreetMap alone.

The NYC study rests on reviewed tables. A *place* study replaces them with
documented default rules, so the same analysis runs anywhere OpenStreetMap
covers. Its results are a first screen, not a reviewed study.

``onebridge init`` writes a study folder::

    config.toml       area, UTM zone, buffers, speeds, extracts and their SHA-256
    hospitals.json    every OpenStreetMap hospital found, with an ``included``
                      flag and the reason; edit it to correct the list
    downloads/        the OpenStreetMap extract(s)

``onebridge extract`` then runs unchanged, and ``onebridge prepare`` sees
``kind = "place"`` in the configuration and builds the bundle with
:func:`prepare_place`.

Default rules
-------------

Roads
    Every edge of the OSMnx ``drive_service`` graph is usable unless one of
    its source ways is tagged ``access``, ``vehicle``, ``motor_vehicle`` or
    ``motorcar`` = ``no`` or ``private``. Speed is the posted ``maxspeed``
    (the slowest when an edge merges several), else the configured default
    for the road class (the slowest class when several). An edge whose class
    has no default (for example ``busway``) is left out and counted.

Origins
    Points of a square grid in the study's UTM zone inside the study
    rectangle, kept when a usable non-motorway road lies within 150 m. Each
    departs from the nearest such road, and in the opposite direction where
    that is also usable. Origins carry no population weights.

Hospitals
    ``amenity=hospital`` or ``healthcare=hospital`` features. Only those
    tagged ``emergency=yes`` are included: outside the US the hospital tag
    also covers clinics, practices, laboratories and care homes. Untagged
    ones are listed for review (set ``included`` to true in hospitals.json
    for one that has an emergency department). Only when no hospital in the
    area is tagged ``emergency=yes`` are untagged general hospitals assumed
    to have one, unless their tags or name mark a speciality that rarely
    does (psychiatric, rehabilitation and similar). Arrivals are the campus frontage rule of the NYC study (public
    roads within 20 m of the grounds; a mapped point gets a 50 m circle) plus
    mapped ``emergency=emergency_ward_entrance`` nodes, each joined to the
    nearest public road within 60 m.

Closures
    Ordinary segments and bridge/tunnel structures, as in the NYC study.
    There are no curated river-crossing corridors; a named bridge closes as
    one structure.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import math
import re
import tomllib
from collections import Counter
from pathlib import Path

from shapely import STRtree, from_wkt, transform
from shapely.geometry import LineString, Point, shape
from shapely.ops import nearest_points

from .. import __version__
from ..study.bundle import to_utm, utm_epsg
from .closures import ordinary_candidates, structure_candidates
from .downloads import fetched
from .hospitals import Roads, hospital_arrivals
from .origins import candidate_grid, departures, reverse_departures
from .output import write_gzip, write_json, write_lines
from .roads import LINKS, MPH, fallback_mph, load_speeds, posted_kph, read_lines
from .roads import read_table as read_osm_table
from .shared import basemap, bundle_nodes, bundle_ways, used_ways, way_names

GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1.json"
USER_AGENT = f"onebridge/{__version__} (+https://github.com/gfonseca-labs/onebridge)"
BUFFERS_M = {"primary": 10000, "wide": 20000}
# The primary routing buffer grows from 10 km until every part of the study
# rectangle has REACH_HOSPITALS emergency departments within reach, so a
# rural study does not report clipped roads as lost access. The wide
# sensitivity is always WIDE_EXTRA_M larger; hospitals and extracts are
# searched out to the largest wide buffer.
PRIMARY_STEPS_M = (10000, 15000, 20000)
WIDE_EXTRA_M = 10000
REACH_HOSPITALS = 2
# Roads are longer than the straight line; reach counts a hospital only if it
# lies within buffer / ROAD_FACTOR.
ROAD_FACTOR = 1.4
SPACINGS_M = [500, 250]
MAX_CONNECTOR_M = 150.0
ENTRANCE_SEARCH_M = 60.0
POINT_CAMPUS_M = 50.0
LARGE_SIDE_M = 60000
ACCESS_KEYS = ("access", "vehicle", "motor_vehicle", "motorcar")
CLOSED = frozenset({"no", "private"})
FAST_CLASSES = frozenset({"motorway", "trunk"}) | LINKS
# Speeds (mph) for roads without a usable posted limit. A place study has no
# NYC/outer split, so both areas take the same values. Edit them in config.toml.
DEFAULT_SPEEDS = {
    "baseline": {"ordinary": 30, "slow": 10, "motorway": 55, "trunk": 45, "link": 25},
    "special_slow": {"motorway": 45, "trunk": 35, "link": 15},
    "special_fast": {"motorway": 65, "trunk": 55, "link": 35},
}
UNTAGGED = "no emergency tag; set included to true if it has an ED"
ASSUMED = "no emergency tag; assumed to have an ED (none in the area is tagged)"
NO_ED = re.compile(
    r"psychiatr|behaviou?ral|mental|rehabilit|long[- ]term|nursing|hospice|"
    r"veterinar|animal|state hospital|dental|eye and ear|cancer|surgery center|"
    r"surgical center|maternity|addiction|substance",
    re.I,
)


# ---------------------------------------------------------------- init


def init_place(
    place, out, *, bbox=None, pbf=None, buffers_m=None, force=False, log=print
):
    """Write a new study folder for ``place`` and return its config path.

    ``bbox`` (west, south, east, north) replaces geocoding ``place``.
    ``pbf`` lists local OpenStreetMap extracts to use instead of downloading
    the Geofabrik extracts that cover the routing area.
    """
    from .extract import routing_polygon

    out = Path(out)
    chosen = dict(buffers_m) if buffers_m else None
    if bbox is None:
        bbox = geocode_bbox(place)
        log("geocoded", repr(place), "to", [round(v, 5) for v in bbox])
    west, south, east, north = (float(v) for v in bbox)
    if not (west < east and south < north):
        raise ValueError("the study rectangle must have west < east and south < north")
    epsg = utm_epsg((west + east) / 2, (south + north) / 2)
    project = to_utm(epsg)
    (x0, y0), (x1, y1) = project(west, south), project(east, north)
    side = max(x1 - x0, y1 - y0)
    if side > LARGE_SIDE_M and not force:
        raise ValueError(
            f"the study rectangle is {side / 1000:.0f} km across; studies are "
            "designed for a town, city or county part (pass --force to go ahead, "
            "or give a smaller --bbox)"
        )
    search_m = max(chosen.values()) if chosen else PRIMARY_STEPS_M[-1] + WIDE_EXTRA_M
    polygon = routing_polygon([west, south, east, north], search_m, epsg)

    out.mkdir(parents=True, exist_ok=False)
    downloads = out / "downloads"
    downloads.mkdir()
    pins, names = {}, []
    if pbf:
        sources = [Path(p) for p in pbf]
        for source in sources:
            name = source.name
            if not name.endswith(".osm.pbf"):
                raise ValueError(f"{source} is not an .osm.pbf file")
            target = downloads / name
            target.write_bytes(source.read_bytes())
            pins[name] = _sha256(target)
            names.append(name.removesuffix(".osm.pbf"))
    else:
        for region in covering_extracts(polygon, log=log):
            name = f"{region['id'].replace('/', '-')}.osm.pbf"
            log("downloading", region["urls"]["pbf"])
            _download(region["urls"]["pbf"], downloads / name)
            pins[name] = _sha256(downloads / name)
            names.append(name.removesuffix(".osm.pbf"))
    snapshot = _snapshot([downloads / f"{n}.osm.pbf" for n in names])
    log("OpenStreetMap snapshot:", snapshot or "unknown")

    hospitals = find_hospitals([downloads / f"{n}.osm.pbf" for n in names], polygon)
    assume_untagged_if_none_tagged(hospitals, log)
    (out / "hospitals.json").write_text(json.dumps(hospitals, indent=2) + "\n")
    included = sum(h["included"] for h in hospitals)
    untagged = sum(h["reason"] == UNTAGGED for h in hospitals)
    log(len(hospitals), "hospitals found,", included, "included; see hospitals.json")
    if untagged:
        log(untagged, "untagged hospitals left out; check them in hospitals.json")
    if not included:
        raise ValueError("no hospital with an emergency department in the area")
    buffers_m = chosen or choose_buffers(
        (x0, y0, x1, y1),
        [project(h["lon"], h["lat"]) for h in hospitals if h["included"]],
        log,
    )

    config = out / "config.toml"
    config.write_text(
        _config_toml(place, [west, south, east, north], epsg, buffers_m, names, pins)
        + f'snapshot = "{snapshot or "unknown"}"\n'
    )
    log("study written to", out)
    return config


def geocode_bbox(place):
    """``[west, south, east, north]`` of ``place`` from Nominatim (via OSMnx)."""
    import osmnx as ox

    west, south, east, north = ox.geocode_to_gdf(place).total_bounds
    return [float(west), float(south), float(east), float(north)]


def covering_extracts(polygon, log=print, index=None):
    """The Geofabrik extracts to download for ``polygon``, smallest first.

    Extracts that touch the area are taken smallest first, each kept only
    when it covers part of the area the ones before it do not (so two
    neighbouring states, not the region around them). Uncovered slivers under
    1% of the area (open sea beyond every extract's boundary) are accepted.
    When that fails, the smallest single extract that covers the area is used.
    ``index`` is Geofabrik's ``index-v1.json`` (fetched when not given).
    """
    from shapely import union_all

    if index is None:
        index = _get(GEOFABRIK_INDEX).json()
    regions = [
        (f["properties"], shape(f["geometry"]))
        for f in index["features"]
        if f.get("geometry") and "pbf" in f["properties"]["urls"]
    ]
    touching = sorted(
        ((p, g) for p, g in regions if g.intersects(polygon)), key=lambda r: r[1].area
    )
    chosen, left = [], polygon
    for p, g in touching:
        if left.area <= 0.01 * polygon.area:
            break
        remaining = left.difference(g)
        if left.area - remaining.area > 0.001 * polygon.area:
            chosen.append((p, g))
            left = remaining
    if left.area > 0.01 * polygon.area:
        single = [(p, g) for p, g in touching if g.covers(polygon)][:1]
        if not single:
            raise ValueError("no Geofabrik extracts cover the routing area; use --pbf")
        chosen = single
    # Drop an extract whose part of the area a later, larger one also covers.
    for item in list(chosen):
        others = [g for p, g in chosen if p is not item[0]]
        part = polygon.intersection(item[1])
        if others and part.difference(union_all(others)).area <= 1e-9 * polygon.area:
            chosen.remove(item)
    log("extracts:", ", ".join(f"{p['name']} ({p['id']})" for p, _ in chosen))
    return [p for p, _ in chosen]


def _config_toml(place, bbox, epsg, buffers_m, names, pins):
    speeds = []
    for scenario, values in DEFAULT_SPEEDS.items():
        speeds.append(f"[speeds.{scenario}]")
        for key, mph in values.items():
            if key in ("ordinary", "slow"):
                speeds.append(f"nyc_{key} = {mph}")
                speeds.append(f"outer_{key} = {mph}")
            else:
                speeds.append(f"{key} = {mph}")
        speeds.append("")
    return (
        "\n".join(
            [
                "# OneBridge place study, written by `onebridge init`.",
                "# Edit the speeds or hospitals.json, then rerun `prepare`.",
                "",
                "[study]",
                'kind = "place"',
                f"name = {json.dumps(place)}",
                "",
                "# Speeds (mph) used when a road has no usable posted limit.",
                *speeds,
                "[area]",
                f"origin_bbox = {json.dumps([round(v, 7) for v in bbox])}",
                f"epsg = {epsg}",
                "buffers_m = { "
                + ", ".join(f"{k} = {v}" for k, v in buffers_m.items())
                + " }",
                "",
                "[origins]",
                f"spacings_m = {json.dumps(SPACINGS_M)}",
                f"max_connector_m = {MAX_CONNECTOR_M}",
                "",
                "[downloads]",
                *(f'"{name}" = "{digest}"' for name, digest in sorted(pins.items())),
                "",
                "[osm]",
                f"extracts = {json.dumps(names)}",
            ]
        )
        + "\n"
    )


def _get(url, stream=False):
    """HTTP GET with requests (installed with OSMnx), which carries its own CA
    certificates; a bare python.org build on macOS has none."""
    import requests

    response = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=600, stream=stream
    )
    response.raise_for_status()
    return response


def _download(url, path):
    with _get(url, stream=True) as r, path.open("xb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)


def _sha256(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _snapshot(paths):
    import osmium

    stamps = set()
    for path in paths:
        header = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
        stamps.add(header.header().get("osmosis_replication_timestamp", ""))
        header.close()
    stamps.discard("")
    return min(stamps) if stamps else None


# ----------------------------------------------------------- hospitals


def find_hospitals(paths, polygon):
    """Hospitals with a point or area inside ``polygon`` (lon/lat).

    Returns a list sorted by ID of ``{"id", "name", "lon", "lat", "campus",
    "entrances", "emergency", "included", "reason"}``; ``campus`` is WKT in
    lon/lat or ``None`` for a mapped point.
    """
    import osmium
    from shapely import wkb

    factory = osmium.geom.WKBFactory()
    found, entrances = {}, []
    for path in paths:
        processor = osmium.FileProcessor(str(path)).with_locations().with_areas()
        for obj in processor:
            tags = obj.tags
            if obj.is_node() and tags.get("emergency") == "emergency_ward_entrance":
                entrances.append((obj.location.lon, obj.location.lat))
                continue
            if not _is_hospital(tags):
                continue
            if obj.is_node():
                geometry = Point(obj.location.lon, obj.location.lat)
                hid = f"osm-node-{obj.id}"
            elif obj.is_area():
                try:
                    geometry = wkb.loads(factory.create_multipolygon(obj), hex=True)
                except RuntimeError:
                    continue
                kind = "way" if obj.from_way() else "relation"
                hid = f"osm-{kind}-{obj.orig_id()}"
            else:
                continue
            if not polygon.intersects(geometry):
                continue
            found[hid] = _hospital_row(hid, dict(tags), geometry)
    # A hospital mapped as both a point and an area keeps the area.
    areas = [h for h in found.values() if h["campus"]]
    for hid in [h for h, row in found.items() if not row["campus"]]:
        point = Point(found[hid]["lon"], found[hid]["lat"])
        if any(from_wkt(a["campus"]).contains(point) for a in areas):
            del found[hid]
    for row in found.values():
        zone = from_wkt(row["campus"]) if row["campus"] else None
        near = Point(row["lon"], row["lat"]).buffer(0.003)
        row["entrances"] = sorted(
            [lon, lat]
            for lon, lat in entrances
            if (zone or near).distance(Point(lon, lat)) <= 0.0015
        )
    return [found[h] for h in sorted(found)]


def _is_hospital(tags):
    return tags.get("amenity") == "hospital" or tags.get("healthcare") == "hospital"


def choose_buffers(box, hospitals_xy, log=print):
    """Routing buffers whose primary reaches enough EDs from the whole study.

    ``box`` is the study rectangle and ``hospitals_xy`` the included
    hospitals, both in the study's UTM metres. Samples a 5 x 5 grid of the
    rectangle; each sample needs ``REACH_HOSPITALS`` hospitals (or all of
    them, if fewer) within ``primary / ROAD_FACTOR`` in a straight line.
    """
    x0, y0, x1, y1 = box
    samples = [
        (x0 + (x1 - x0) * i / 4, y0 + (y1 - y0) * j / 4)
        for i in range(5)
        for j in range(5)
    ]
    need = min(REACH_HOSPITALS, len(hospitals_xy))

    def reaches(primary):
        limit = primary / ROAD_FACTOR
        return all(
            sum(math.dist(p, h) <= limit for h in hospitals_xy) >= need for p in samples
        )

    primary = next((b for b in PRIMARY_STEPS_M if reaches(b)), None)
    if primary is None:
        primary = PRIMARY_STEPS_M[-1]
        log(
            f"some of the study is more than {primary / ROAD_FACTOR / 1000:.0f} km "
            f"from its {need} nearest EDs; disconnections near the edge may "
            "reflect the routing buffer, so compare the wide run"
        )
    buffers = {"primary": primary, "wide": primary + WIDE_EXTRA_M}
    if primary != PRIMARY_STEPS_M[0]:
        log("routing buffers widened to reach nearby EDs:", buffers)
    return buffers


def assume_untagged_if_none_tagged(hospitals, log=print):
    """Where nobody tags emergency departments, assume general hospitals have one."""
    if any(h["included"] for h in hospitals):
        return
    for h in hospitals:
        if h["reason"] == UNTAGGED:
            h["included"], h["reason"] = True, ASSUMED
    if any(h["included"] for h in hospitals):
        log("no hospital is tagged emergency=yes; assuming untagged general ones")


def _hospital_row(hid, tags, geometry):
    name = tags.get("name") or hid
    centre = geometry.representative_point()
    emergency = tags.get("emergency")
    speciality = " ".join(
        tags.get(k, "") for k in ("healthcare:speciality", "healthcare:specialty")
    )
    if emergency == "yes":
        included, reason = True, "emergency=yes"
    elif emergency == "no":
        included, reason = False, "emergency=no"
    elif NO_ED.search(speciality) or NO_ED.search(name):
        included, reason = False, "speciality hospital without an emergency tag"
    else:
        included, reason = False, UNTAGGED
    return {
        "id": hid,
        "name": name,
        "lon": round(centre.x, 7),
        "lat": round(centre.y, 7),
        "campus": None if geometry.geom_type == "Point" else geometry.wkt,
        "emergency": emergency,
        "included": included,
        "reason": reason,
    }


# ------------------------------------------------------------- prepare


def prepare_place(config_path, extract, study, downloads, out, log=print):
    """Write a new place bundle ``out`` from an extraction and default rules."""
    config_path = Path(config_path)
    config = tomllib.loads(config_path.read_text())
    if config.get("study", {}).get("kind") != "place":
        raise ValueError("not a place study configuration")
    for name in config["osm"]["extracts"]:
        fetched(downloads, f"{name}.osm.pbf", config)
    extract, study, out = Path(extract), Path(study), Path(out)
    epsg = config["area"]["epsg"]
    buffers = list(config["area"]["buffers_m"])
    scenarios = load_speeds(config_path)
    ways = read_osm_table(extract / "osm/ways.json.gz")
    nodes = read_osm_table(extract / "osm/nodes.json.gz")
    points = {n: [r["lon"], r["lat"]] for n, r in nodes.items()}

    roads, edges, fast = {}, {}, set()
    for buffer in buffers:
        roads[buffer] = read_lines(extract / f"roads/{buffer}-extract.jsonl.gz")
        edges[buffer], skipped, quick = place_edges(roads[buffer], ways, scenarios)
        fast |= quick
        log(buffer, len(edges[buffer]), "usable edges; left out:", dict(skipped))

    origins, by_buffer = place_origins(config, edges, points, epsg, fast)
    log(
        "origins kept:",
        {s: len(p) for s, p in origins["kept"].items()},
        "of",
        {s: len(c) for s, c in origins["coordinates"].items()},
    )
    hospitals = [
        h for h in json.loads((study / "hospitals.json").read_text()) if h["included"]
    ]
    project = to_utm(epsg)
    campuses = {
        h["id"]: transform(from_wkt(h["campus"]), project, interleaved=False)
        if h["campus"]
        else Point(project(h["lon"], h["lat"])).buffer(POINT_CAMPUS_M)
        for h in hospitals
    }

    out.mkdir(parents=True, exist_ok=False)
    endpoints, structures = {}, {}
    for buffer in buffers:
        usable = {tuple(e["edge"]) for e in edges[buffer]}
        public = Roads(roads[buffer], usable, ways, points, epsg)
        arrivals = entrance_rows(hospitals, public, project)
        splits, by_node = hospital_arrivals(arrivals, campuses, public)
        log(buffer, len(set(splits) | set(by_node)), "hospitals reachable by road")
        endpoints[buffer] = {
            "hospital_splits": splits,
            "hospital_nodes": by_node,
            "origins": by_buffer[buffer],
        }
        structures[buffer] = group_structures(
            structure_candidates(roads[buffer], usable, ways, set())
        )
        ordinary = ordinary_candidates(
            roads[buffer], usable, ways, nodes, separated_nodes=False
        )
        # Every usable edge must belong to some closure, or `run` would find
        # a route edge it cannot test.
        closable = {tuple(e) for g in ordinary + structures[buffer] for e in g["edges"]}
        if usable - closable:
            raise ValueError(
                f"{buffer}: {len(usable - closable)} usable edges are in no closure"
            )
        folder = out / buffer
        folder.mkdir()
        write_lines(folder / "edges.jsonl.gz", edges[buffer])
        (folder / "endpoints.json").write_text(json.dumps(endpoints[buffer]))
        write_lines(folder / "ordinary.jsonl.gz", ordinary)
        write_lines(folder / "structures.jsonl.gz", structures[buffer])
        write_lines(folder / "crossings.jsonl.gz", [])
        log(buffer, len(ordinary), "ordinary,", len(structures[buffer]), "structure")
    if not endpoints["primary"]["hospital_splits"] and not any(
        endpoints["primary"]["hospital_nodes"].values()
    ):
        raise ValueError("no included hospital touches a usable road")

    hospital_nodes = {b: e["hospital_nodes"] for b, e in endpoints.items()}
    used = used_ways(edges, structures)
    write_json(out / "nodes.json.gz", bundle_nodes(edges, hospital_nodes, nodes))
    write_json(out / "ways.json.gz", bundle_ways(used, ways))
    write_json(out / "way-names.json.gz", way_names(used, ways))
    write_lines(out / "primary/basemap.jsonl.gz", basemap(roads["primary"]))
    with gzip.open(extract / "osm/restrictions.jsonl.gz", "rb") as f:
        write_gzip(out / "restrictions.jsonl.gz", f.read())
    (out / "origins.json").write_text(
        json.dumps({"coordinates": origins["coordinates"]})
    )
    (out / "hospitals.json").write_text(
        json.dumps(
            {
                h["id"]: {"name": h["name"], "lon": h["lon"], "lat": h["lat"]}
                for h in hospitals
            },
            sort_keys=True,
        )
    )
    name = config["study"]["name"]
    (out / "study.json").write_text(
        json.dumps(
            {
                "kind": "place",
                "name": name,
                "epsg": epsg,
                "buffers_m": config["area"]["buffers_m"],
                "spacings_m": config["origins"]["spacings_m"],
            },
            sort_keys=True,
        )
        + "\n"
    )
    snapshot = config["osm"].get("snapshot", "unknown")
    (out / "map.json").write_text(
        json.dumps(
            {
                "title": f"OneBridge: {name} - top-10 single closures (red)\n"
                "automatic OpenStreetMap inputs, not reviewed - research model, "
                "not field-verified",
                "credit": "Map data: OpenStreetMap contributors (ODbL), "
                f"snapshot {snapshot}.",
                "margin_m": 1500,
                "origin_label": "Grid origins (500 m)",
                "hospital_heading": "Hospitals with an ED (OpenStreetMap)",
            },
            sort_keys=True,
        )
    )
    log("bundle written to", out)


def group_structures(features):
    """Merge structure ways that share a road edge into one structure row.

    A long bridge is often many OpenStreetMap ways merged by OSMnx into one
    road edge, with unnamed approach ways between its pieces. The study
    groups structures by shared name and node, so each piece would close the
    same bridge as a separate scenario. Ways linked through shared edges
    become one row: the lowest way ID, the group's sorted names (or
    ``structure-way-<id>``) and every edge of the group.
    """
    parent = {f["way"]: f["way"] for f in features}

    def find(w):
        while parent[w] != w:
            parent[w] = parent[parent[w]]
            w = parent[w]
        return w

    by_edge = {}
    for f in features:
        for edge in f["edges"]:
            other = by_edge.setdefault(tuple(edge), f["way"])
            parent[find(f["way"])] = find(other)
    groups = {}
    for f in features:
        groups.setdefault(find(f["way"]), []).append(f)
    result = []
    for members in groups.values():
        if len(members) == 1:
            result.append(members[0])
            continue
        names = sorted({f["name"] for f in members if f["name"]})
        first = min(f["way"] for f in members)
        edges = sorted({tuple(e) for f in members for e in f["edges"]})
        result.append(
            {
                "way": first,
                "name": " / ".join(names) or f"structure-way-{first}",
                "edges": [list(e) for e in edges],
            }
        )
    return sorted(result, key=lambda f: f["way"])


def place_edges(rows, ways, scenarios):
    """Usable edges with travel times in routing order, skip counts, and the
    set of usable motorway, trunk and link edges (no origin departs from one).
    """
    usable, skipped, fast = [], Counter(), set()
    for row in rows:
        tags = [ways[w]["tags"] for w in row["ways"]]
        if any(t.get(k) in CLOSED for t in tags for k in ACCESS_KEYS):
            skipped["no or private access"] += 1
            continue
        seconds = place_seconds(row, scenarios)
        if seconds is None:
            skipped[f"no speed for {row['highway']}"] += 1
            continue
        if set(_values(row["highway"])) & FAST_CLASSES:
            fast.add(tuple(row["edge"]))
        usable.append(
            {
                "edge": row["edge"],
                "length": row["length"],
                "geometry": row["geometry"],
                "ways": row["ways"],
                "seconds": seconds,
            }
        )
    usable.sort(key=lambda r: (str(r["edge"][0]), str(r["edge"][1]), r["edge"][2]))
    return usable, skipped, fast


def place_seconds(row, scenarios):
    """Travel time per scenario: slowest posted limit, else slowest default."""
    posted = [k for k in map(posted_kph, _values(row["maxspeed"])) if k is not None]
    if posted:
        kph = dict.fromkeys(scenarios, min(posted))
    else:
        kph = {}
        for name, speeds in scenarios.items():
            rules = [fallback_mph(c, False, speeds) for c in _values(row["highway"])]
            if not rules or any(r is None for r in rules):
                return None
            kph[name] = min(rules) * MPH
    if row["length"] <= 0:
        return None
    return {s: 3.6 * row["length"] / kph[s] for s in scenarios}


def _values(text):
    """A tag value, or the members of OSMnx's merged list form."""
    if text in (None, "None", "nan"):
        return []
    if text.startswith("["):
        return [str(v) for v in ast.literal_eval(text)]
    return [text]


def place_origins(config, edges, nodes, epsg, fast=frozenset()):
    """Grid origins near a usable road, and their departures per buffer.

    ``fast`` holds motorway, trunk and link edges; origins never depart from
    them.
    """
    bbox = config["area"]["origin_bbox"]
    limit = config["origins"].get("max_connector_m", MAX_CONNECTOR_M)
    project = to_utm(epsg)
    local = [e for e in edges["primary"] if tuple(e["edge"]) not in fast]
    lines = [_line(e, nodes, project) for e in local]
    tree = STRtree(lines)
    coordinates, kept, points = {}, {}, {}
    for spacing in config["origins"]["spacings_m"]:
        grid = candidate_grid(bbox, spacing, epsg)
        coordinates[str(spacing)] = {o: [p[2], p[3]] for o, p in grid.items()}
        keep = {}
        for origin, (x, y, _, _) in grid.items():
            point = Point(x, y)
            nearest = tree.query_nearest(point, max_distance=limit)
            if len(nearest):
                keep[origin] = (x, y)
        kept[str(spacing)] = sorted(keep)
        points[str(spacing)] = keep
    by_buffer = {}
    for buffer, rows in edges.items():
        usable = {tuple(e["edge"]) for e in rows}
        candidates = [e for e in rows if tuple(e["edge"]) not in fast]
        forward = departures(
            candidates,
            nodes,
            {o: p for group in points.values() for o, p in group.items()},
            epsg,
        )
        by_buffer[buffer], start = {}, 0
        for spacing, group in points.items():
            part, start = forward[start : start + len(group)], start + len(group)
            by_buffer[buffer][spacing] = {
                "departures": part,
                "reverse": reverse_departures(part, usable),
            }
    return {"coordinates": coordinates, "kept": kept}, by_buffer


def _line(edge, nodes, project):
    wkt = edge["geometry"]
    u, v = edge["edge"][:2]
    line = from_wkt(wkt) if wkt else LineString([nodes[u], nodes[v]])
    return transform(line, project, interleaved=False)


def entrance_rows(hospitals, roads, project):
    """Arrival rows for mapped emergency entrances near a public road."""
    rows = []
    for hospital in hospitals:
        for lon, lat in hospital.get("entrances", []):
            point = Point(project(lon, lat))
            best = None
            for row in roads.near(point.buffer(1)):
                line = roads.line(row)
                distance = line.distance(point)
                if distance <= ENTRANCE_SEARCH_M and (
                    best is None or distance < best[0]
                ):
                    best = (distance, row, line)
            if best is None:
                continue
            _, row, line = best
            along = line.project(nearest_points(point, line)[1])
            edge = row["edge"]
            entry = {"hospital": hospital["id"], "kind": "entrance", "edge": edge}
            if along <= 0.01:
                entry["node"] = edge[0]
            elif line.length - along <= 0.01:
                entry["node"] = edge[1]
            else:
                entry["fraction"] = along / line.length
            rows.append(entry)
    return rows


def is_place_config(config_path):
    """True when ``config_path`` describes a place study."""
    config = tomllib.loads(Path(config_path).read_text())
    return config.get("study", {}).get("kind") == "place"


__all__ = [
    "find_hospitals",
    "init_place",
    "is_place_config",
    "place_edges",
    "place_origins",
    "place_seconds",
    "prepare_place",
]
