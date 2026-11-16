"""Prepared study inputs.

A bundle is a directory of plain JSON files produced by the preparation
stage. Everything the analysis needs is in it, so runs are reproducible from
the bundle alone:

    nodes.json.gz              {node_id: [lon, lat]}
    ways.json.gz               {way_id: [node_id, ...]}
    restrictions.jsonl.gz      OpenStreetMap turn-restriction relations
    nyc-boundary.geojson       borough polygons including water
    <buffer>/edges.jsonl.gz    usable directed edges, in routing order
    <buffer>/endpoints.json    hospital arrivals and origin departures
    <buffer>/ordinary.jsonl.gz ordinary closure candidates
    <buffer>/structures.jsonl.gz  local structure (bridge/tunnel) features
    <buffer>/crossings.jsonl.gz   river crossing corridors
    <buffer>/basemap.jsonl.gz     every mapped road edge, for the map
    origins.json               origin coordinates and 2020 population
    hospitals.json             hospital names and coordinates
    way-names.json.gz          OSM way names
    map.json                   optional map title, credit and margin
    study.json                 dataset kind, projection and configured buffers/grids

``<buffer>`` is ``primary`` (10 km routing buffer) or ``wide`` (20 km).
Edges are ``[u, v, key]`` OSM node pairs; times are in seconds per speed
scenario.

Distances are measured in a UTM zone. The NYC study uses zone 18N
(EPSG:32618); a ``place`` bundle names its zone in ``study.json`` (``epsg``).
A ``place`` bundle has no ``nyc-boundary.geojson``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from pyproj import Transformer
from shapely import transform, union_all
from shapely.geometry import shape

BUFFERS = ("primary", "wide")
SPEEDS = ("baseline", "outer35", "special_slow", "special_fast")
KINDS = ("nyc", "synthetic", "place")
NYC_EPSG = 32618


def _lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def to_utm(epsg=NYC_EPSG):
    """Projection from WGS84 lon/lat to UTM metres (default zone 18N)."""
    return Transformer.from_crs(4326, epsg, always_xy=True).transform


def from_utm(epsg=NYC_EPSG):
    """Projection from UTM metres back to WGS84 lon/lat."""
    return Transformer.from_crs(epsg, 4326, always_xy=True).transform


def utm_epsg(lon, lat):
    """EPSG code of the WGS84 UTM zone containing ``(lon, lat)``.

    Norway and Svalbard's irregular zones are not applied; the standard
    6-degree zone is accurate enough for a city-scale study anywhere.
    """
    if not (-180 <= lon <= 180 and -80 <= lat <= 84):
        raise ValueError("UTM covers latitudes from 80 S to 84 N")
    zone = min(int((lon + 180) // 6) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


def read_boundary(path, epsg=NYC_EPSG):
    """The union of a GeoJSON file's polygons, in UTM metres."""
    features = json.loads(Path(path).read_text())["features"]
    merged = union_all([shape(f["geometry"]) for f in features])
    return transform(merged, to_utm(epsg), interleaved=False)


class Bundle:
    def __init__(self, path):
        self.path = Path(path)
        if not (self.path / "nodes.json.gz").exists():
            raise FileNotFoundError(f"{self.path} is not a prepared bundle")
        self._cache = {}

    def _cached(self, key, load):
        if key not in self._cache:
            self._cache[key] = load()
        return self._cache[key]

    def nodes(self):
        return self._cached("nodes", lambda: _int_keys(self.path / "nodes.json.gz"))

    def way_nodes(self):
        return self._cached("ways", lambda: _int_keys(self.path / "ways.json.gz"))

    def restrictions(self):
        return self._cached(
            "restrictions", lambda: _lines(self.path / "restrictions.jsonl.gz")
        )

    def nyc_boundary(self):
        """NYC (water included) as one polygon in UTM metres."""
        return self._cached(
            "nyc", lambda: read_boundary(self.path / "nyc-boundary.geojson")
        )

    def edges(self, buffer):
        return self._cached(
            ("edges", buffer), lambda: _lines(self.path / buffer / "edges.jsonl.gz")
        )

    def endpoints(self, buffer):
        path = self.path / buffer / "endpoints.json"
        return self._cached(("endpoints", buffer), lambda: json.loads(path.read_text()))

    def ordinary(self, buffer):
        return _lines(self.path / buffer / "ordinary.jsonl.gz")

    def structures(self, buffer):
        return _lines(self.path / buffer / "structures.jsonl.gz")

    def crossings(self, buffer):
        return _lines(self.path / buffer / "crossings.jsonl.gz")

    def origins(self):
        """``{"coordinates": {spacing: {id: [lon, lat]}}, "population_2020": ...}``."""
        return self._cached(
            "origins", lambda: json.loads((self.path / "origins.json").read_text())
        )

    def hospitals(self):
        """``{hospital_id: {"name", "lon", "lat"}}``."""
        return self._cached(
            "hospitals", lambda: json.loads((self.path / "hospitals.json").read_text())
        )

    def way_names(self):
        return self._cached("names", lambda: _int_keys(self.path / "way-names.json.gz"))

    def map_labels(self):
        """Overrides for the report map (``title``, ``credit``, ``margin_m``)."""
        path = self.path / "map.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def study(self):
        """Study context; older NYC bundles predate study.json."""
        path = self.path / "study.json"
        if path.exists():
            result = json.loads(path.read_text())
            if result.get("kind") not in KINDS:
                raise ValueError("unknown study kind")
            if result["kind"] == "place" and not isinstance(result.get("epsg"), int):
                raise ValueError("a place bundle must name its UTM zone (epsg)")
            return result
        return {
            "kind": "nyc",
            "buffers_m": {"primary": 10000, "wide": 20000},
            "spacings_m": [500, 250],
        }

    def epsg(self):
        """EPSG code of the bundle's metric projection."""
        return self.study().get("epsg", NYC_EPSG)

    def project(self):
        """Projection from lon/lat to the bundle's UTM metres."""
        return to_utm(self.epsg())

    def basemap(self, buffer):
        """Every mapped road edge with a geometry, for drawing."""
        return _lines(self.path / buffer / "basemap.jsonl.gz")


def _int_keys(path):
    with gzip.open(path, "rt") as f:
        return {int(k): v for k, v in json.load(f).items()}
