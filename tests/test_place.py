"""Studies of any place: projection, default rules and the place bundle path."""

import json

import pytest
from shapely.geometry import Point, box, mapping

from onebridge.cli import main
from onebridge.prepare.origins import candidate_grid, zone_label
from onebridge.prepare.place import (
    _config_toml,
    _hospital_row,
    covering_extracts,
    group_structures,
    is_place_config,
    place_edges,
    place_origins,
    place_seconds,
)
from onebridge.prepare.roads import MPH, load_speeds
from onebridge.study.bundle import Bundle, to_utm, utm_epsg
from onebridge.study.demo import write_bundle
from onebridge.study.report import build_report
from onebridge.study.runs import PLACE_RUNS, RUNS, default_runs, run_study


def quiet(*_):
    pass


def test_utm_zone_for_any_point():
    assert utm_epsg(-73.98, 40.75) == 32618  # NYC
    assert utm_epsg(-71.31, 41.49) == 32619  # Newport, RI
    assert utm_epsg(151.2, -33.87) == 32756  # Sydney
    assert utm_epsg(180, 0) == 32660
    with pytest.raises(ValueError):
        utm_epsg(0, 85)


def test_zone_labels_keep_nyc_origin_ids():
    assert zone_label(32618) == "utm18n"
    assert zone_label(32756) == "utm56s"
    with pytest.raises(ValueError):
        zone_label(4326)
    grid = candidate_grid([-71.32, 41.48, -71.30, 41.50], 500, 32619)
    assert grid and all(o.startswith("utm19n-500-") for o in grid)
    lon, lat = next(iter(grid.values()))[2:]
    assert -71.32 <= lon <= -71.30 and 41.48 <= lat <= 41.50


@pytest.fixture
def scenarios(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(_config_toml("Here", [0, 0, 1, 1], 32631, {}, [], {}))
    return load_speeds(config)


def road(edge, highway="residential", maxspeed="None", length=1000.0, ways=(1,)):
    return {
        "edge": list(edge),
        "highway": highway,
        "maxspeed": maxspeed,
        "length": length,
        "geometry": None,
        "ways": list(ways),
    }


def test_speeds_posted_then_slowest_default(scenarios):
    posted = place_seconds(road((1, 2, 0), maxspeed="40 mph"), scenarios)
    assert posted["baseline"] == pytest.approx(3.6 * 1000 / (40 * MPH))
    merged = place_seconds(road((1, 2, 0), maxspeed="['25 mph', '35 mph']"), scenarios)
    assert merged["baseline"] == pytest.approx(3.6 * 1000 / (25 * MPH))
    default = place_seconds(road((1, 2, 0)), scenarios)
    assert default["baseline"] == pytest.approx(3.6 * 1000 / (30 * MPH))
    # A merged class takes its slowest default: service is 10 mph.
    mixed = place_seconds(road((1, 2, 0), "['residential', 'service']"), scenarios)
    assert mixed["baseline"] == pytest.approx(3.6 * 1000 / (10 * MPH))
    fast = place_seconds(road((1, 2, 0), "motorway"), scenarios)
    assert fast["special_slow"] > fast["baseline"] > fast["special_fast"]
    assert place_seconds(road((1, 2, 0), "busway"), scenarios) is None


def test_private_roads_and_unknown_classes_are_left_out(scenarios):
    ways = {
        1: {"tags": {}},
        2: {"tags": {"access": "private"}},
        3: {"tags": {"motor_vehicle": "no"}},
        4: {"tags": {}},
    }
    rows = [
        road((2, 1, 0), ways=[1]),
        road((1, 2, 0), ways=[1]),
        road((3, 4, 0), ways=[2]),
        road((4, 5, 0), ways=[3]),
        road((5, 6, 0), "busway", ways=[4]),
        road((6, 7, 0), "trunk_link", ways=[4]),
    ]
    usable, skipped, fast = place_edges(rows, ways, scenarios)
    assert [r["edge"] for r in usable] == [[1, 2, 0], [2, 1, 0], [6, 7, 0]]
    assert skipped == {"no or private access": 2, "no speed for busway": 1}
    assert fast == {(6, 7, 0)}


def test_origins_need_a_nearby_local_road(scenarios):
    # One east-west street 50 m north of a grid row near Newport.
    project = to_utm(32619)
    x, y = 304000.0, 4596000.0
    from pyproj import Transformer

    back = Transformer.from_crs(32619, 4326, always_xy=True).transform
    nodes = {1: list(back(x - 2000, y + 50)), 2: list(back(x + 2000, y + 50))}
    nodes |= {3: list(back(x - 2000, y + 400)), 4: list(back(x + 2000, y + 400))}
    edges = [
        {"edge": [1, 2, 0], "length": 4000.0, "geometry": None, "ways": [1]},
        {"edge": [2, 1, 0], "length": 4000.0, "geometry": None, "ways": [1]},
        {"edge": [3, 4, 0], "length": 4000.0, "geometry": None, "ways": [2]},
    ]
    w, s = back(x - 1000, y - 1000)
    e, n = back(x + 1000, y + 1000)
    config = {
        "area": {"origin_bbox": [w, s, e, n]},
        "origins": {"spacings_m": [500], "max_connector_m": 150},
    }
    # The 3-4 road is a motorway: nothing departs from it.
    origins, by_buffer = place_origins(
        config, {"primary": edges}, nodes, 32619, fast={(3, 4, 0)}
    )
    kept = origins["kept"]["500"]
    assert kept and len(kept) < len(origins["coordinates"]["500"])
    for oid in kept:
        assert abs(project(*origins["coordinates"]["500"][oid])[1] - (y + 50)) <= 150
    grid = by_buffer["primary"]["500"]
    assert {e[1][0] for e in grid["departures"]} == {1}
    assert len(grid["reverse"]) == len(grid["departures"])


def test_bridge_pieces_on_one_edge_close_as_one_structure():
    from onebridge.study.scenarios import local_structures

    features = [
        {"way": 10, "name": None, "edges": [[1, 2, 0]]},
        {"way": 11, "name": "Long Bridge", "edges": [[1, 2, 0], [2, 1, 0]]},
        {"way": 12, "name": None, "edges": [[2, 1, 0]]},
        {"way": 20, "name": None, "edges": [[7, 8, 0]]},
    ]
    grouped = group_structures(features)
    assert grouped == [
        {"way": 10, "name": "Long Bridge", "edges": [[1, 2, 0], [2, 1, 0]]},
        features[3],
    ]
    # The pieces need not share a node: unnamed approaches can sit between.
    way_nodes = {10: [1, 3], 11: [5, 6], 12: [8, 2], 20: [7, 8]}
    used = {(1, 2, 0), (2, 1, 0), (7, 8, 0)}
    assert len(local_structures(features, used, way_nodes)) == 4
    assert len(local_structures(grouped, used, way_nodes)) == 2


def test_hospital_inclusion_rules():
    point = Point(-71.3, 41.5)
    assert _hospital_row("osm-node-1", {"emergency": "yes"}, point)["included"]
    assert not _hospital_row("osm-node-2", {"emergency": "no"}, point)["included"]
    untagged = _hospital_row("osm-node-3", {"name": "Newport Hospital"}, point)
    assert untagged["included"] and "assumed" in untagged["reason"]
    rehab = _hospital_row("osm-node-4", {"name": "Bay Rehabilitation Hospital"}, point)
    assert not rehab["included"]
    psych = {"healthcare:speciality": "psychiatry"}
    assert not _hospital_row("osm-node-5", psych, point)["included"]
    area = _hospital_row("osm-way-6", {"emergency": "yes"}, box(0, 0, 1, 1))
    assert area["campus"].startswith("POLYGON") and area["lon"] == 0.5


def _index(*regions):
    return {
        "features": [
            {
                "properties": {"id": i, "name": i, "urls": {"pbf": f"{i}.pbf"}},
                "geometry": mapping(g),
            }
            for i, g in regions
        ]
    }


def test_neighbouring_extracts_beat_the_region_around_them():
    index = _index(
        ("west", box(0, 0, 1, 1)),
        ("east", box(1, 0, 2, 1)),
        ("region", box(-5, -5, 5, 5)),
        ("far", box(10, 10, 11, 11)),
    )
    picked = covering_extracts(box(0.5, 0.2, 1.5, 0.8), quiet, index)
    assert [p["id"] for p in picked] == ["west", "east"]
    # One small extract covering everything is enough on its own.
    picked = covering_extracts(box(0.2, 0.2, 0.8, 0.8), quiet, index)
    assert [p["id"] for p in picked] == ["west"]
    # A gap only the big region fills falls back to it.
    picked = covering_extracts(box(0.5, 0.5, 1.5, 1.5), quiet, index)
    assert [p["id"] for p in picked] == ["region"]
    with pytest.raises(ValueError):
        covering_extracts(box(20, 20, 21, 21), quiet, index)


def test_config_marks_a_place_study(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        _config_toml("Somewhere, X", [1, 2, 3, 4], 32631, {"primary": 100}, ["a"], {})
    )
    assert is_place_config(config)
    nyc = tmp_path / "nyc.toml"
    nyc.write_text("[area]\nbuffers_m = { primary = 1 }\n")
    assert not is_place_config(nyc)


def place_bundle(tmp_path):
    """The demo grid relabelled as a place study (zone 18N, no population)."""
    bundle = write_bundle(tmp_path / "bundle")
    (bundle.path / "study.json").write_text(
        json.dumps(
            {
                "kind": "place",
                "name": "Demo Town",
                "epsg": 32618,
                "buffers_m": {"primary": 10000, "wide": 20000},
                "spacings_m": [500, 250],
            }
        )
    )
    (bundle.path / "nyc-boundary.geojson").unlink()
    origins = json.loads((bundle.path / "origins.json").read_text())
    del origins["population_2020"]
    (bundle.path / "origins.json").write_text(json.dumps(origins))
    return Bundle(bundle.path)


def test_place_kind_needs_its_zone(tmp_path):
    bundle = place_bundle(tmp_path)
    assert bundle.epsg() == 32618 and default_runs(bundle) == PLACE_RUNS
    assert default_runs(write_bundle(tmp_path / "nyc")) == RUNS
    (bundle.path / "study.json").write_text(json.dumps({"kind": "place"}))
    with pytest.raises(ValueError, match="UTM zone"):
        Bundle(bundle.path).study()


def test_place_bundle_runs_and_reports_without_nyc_inputs(tmp_path):
    bundle = place_bundle(tmp_path)
    summary = run_study(bundle, tmp_path / "runs", log=quiet)
    assert set(summary["runs"]) == {r.name for r in PLACE_RUNS}
    assert not any("outer35" in n or "uturn" in n for n in summary["runs"])
    evidence = build_report(bundle, tmp_path / "runs", tmp_path / "report", log=quiet)
    manifest = json.loads((evidence / "evidence.json").read_text())
    assert manifest["inputs"]["dataset"] == "place"
    assert manifest["inputs"]["configuration"]["place"] == "Demo Town"
    assert any("not reviewed" in x for x in manifest["methodology"]["limitations"])
    comparison = json.loads((tmp_path / "report" / "comparison.json").read_text())
    assert "census-weighted" not in comparison
    assert set(comparison) == {r.name for r in PLACE_RUNS} - {"primary-500-baseline"}


def test_prepare_requires_sources_for_nyc(tmp_path, capsys):
    config = tmp_path / "nyc.toml"
    config.write_text("[area]\n")
    with pytest.raises(SystemExit):
        main(
            [
                "prepare",
                "--config",
                str(config),
                "--extract",
                "x",
                "--downloads",
                "y",
                "--out",
                "z",
            ]
        )
    assert "--sources is required" in capsys.readouterr().err
