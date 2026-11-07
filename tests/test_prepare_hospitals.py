import json

import pytest
from pyproj import Transformer
from shapely.geometry import box, mapping

from onebridge.prepare.hospitals import (
    Roads,
    frontage,
    hospital_arrivals,
    on_structure,
    read_campuses,
)

X0, Y0 = 584000, 4510000
UNPROJECT = Transformer.from_crs(32618, 4326, always_xy=True).transform
CAMPUS = box(X0, Y0, X0 + 100, Y0 + 100)


def node(x, y):
    return list(UNPROJECT(X0 + x, Y0 + y))


NODES = {
    1: node(60, -50),  # a diagonal passing 7.1 m from the south-east corner,
    2: node(160, 50),  # closest at (105, -5): 45% along from node 1
    3: node(0, -40),  # 40 m below: too far
    4: node(100, -40),
    5: node(110, 50),  # a stub pointing at the east side, ending 10 m away
    6: node(160, 50),
    7: node(20, 20),  # inside the campus
    8: node(80, 20),
}


def row(u, v, highway="residential", way=100):
    return {"edge": [u, v, 0], "highway": highway, "geometry": None, "ways": [way]}


WAYS = {100: {"tags": {}}, 200: {"tags": {"bridge": "yes"}}}


def roads(rows, unusable=()):
    usable = {tuple(r["edge"]) for r in rows} - set(unusable)
    return Roads(rows, usable, WAYS, NODES)


def test_frontage_splits_nearby_public_roads_and_uses_end_nodes():
    rows = [row(1, 2), row(2, 1), row(3, 4), row(6, 5), row(7, 8)]
    options = frontage(CAMPUS, roads(rows))
    assert [o["edge"] for o in options] == [[1, 2, 0], [2, 1, 0], [6, 5, 0]]
    assert options[0]["fraction"] == pytest.approx(0.45, abs=1e-6)
    assert options[1]["fraction"] == pytest.approx(0.55, abs=1e-6)
    assert options[2] == {"edge": [6, 5, 0], "node": 5}


def test_frontage_skips_unusable_structures_and_other_classes():
    rows = [row(1, 2, way=200), row(2, 1, highway="service"), row(6, 5)]
    assert [o["edge"] for o in frontage(CAMPUS, roads(rows, {(6, 5, 0)}))] == []


def test_structure_tags():
    assert on_structure({"bridge": "yes"}) and on_structure({"layer": "-1"})
    assert not on_structure({"bridge": "no", "layer": "0"})


def test_arrivals_put_entrances_first_and_apply_exclusions():
    rows = [row(1, 2), row(2, 1), row(6, 5)]
    campuses = {"h": CAMPUS}
    review = [
        {"hospital": "h", "kind": "entrance", "edge": [1, 2, 0], "fraction": 0.5},
        {"hospital": "h", "kind": "entrance", "node": 9},
        {"hospital": "h", "kind": "excluded", "edge": [6, 5, 0], "reasons": ["x"]},
    ]
    splits, nodes = hospital_arrivals(review, campuses, roads(rows))
    assert [s[0] for s in splits["h"]] == [[1, 2, 0], [2, 1, 0]]
    assert splits["h"][0][1] == 0.5
    assert nodes == {"h": [9]}


def test_exclusions_must_name_a_frontage_option():
    review = [{"hospital": "h", "kind": "excluded", "edge": [3, 4, 0], "reasons": []}]
    with pytest.raises(ValueError, match="exclusion"):
        hospital_arrivals(review, {"h": CAMPUS}, roads([row(3, 4)]))
    with pytest.raises(ValueError, match="no campus"):
        hospital_arrivals(review, {}, roads([]))


def test_campuses_are_read_in_utm(tmp_path):
    ring = box(*node(0, 0), *node(100, 100))
    path = tmp_path / "campuses.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"hospital": "h", "osm": "way/1", "name": "H"},
                        "geometry": mapping(ring),
                    }
                ],
            }
        )
    )
    campus = read_campuses(path)["h"]
    assert campus.area == pytest.approx(10000, rel=0.01)
