from shapely.geometry import box

from onebridge.prepare.closures import (
    crossing_corridors,
    grade_separated,
    ordinary_candidates,
    structure_candidates,
)
from onebridge.prepare.shared import (
    basemap,
    bundle_nodes,
    bundle_ways,
    hospital_locations,
    used_ways,
    way_names,
)


def node(lon, lat, **tags):
    return {"lon": lon, "lat": lat, "tags": tags}


NODES = {
    1: node(0.0, 0.0),
    2: node(0.001, 0.0),
    3: node(0.002, 0.0),
    4: node(0.003, 0.0, ford="yes"),
}
WAYS = {
    10: {"nodes": [1, 2], "tags": {"name": "Main Street"}},
    11: {"nodes": [2, 3], "tags": {"name": "Main Street", "bridge": "yes"}},
    12: {"nodes": [3, 4], "tags": {}},
    13: {"nodes": [1, 2], "tags": {"name": "Elm Street"}},
}


def row(u, v, ways, geometry=None):
    return {"edge": [u, v, 0], "ways": ways, "geometry": geometry}


EXTRACT = [
    row(1, 2, [10]),
    row(2, 1, [10]),
    row(2, 3, [11]),
    row(3, 2, [11]),
    row(3, 4, [12]),
]


def test_grade_separation_tags():
    assert grade_separated({"tunnel": "building_passage"}) and grade_separated(
        {"ford": "yes"}
    )
    assert grade_separated({"layer": "1"}) and not grade_separated(
        {"bridge": "no", "layer": "0"}
    )


def test_ordinary_segments_group_both_directions_and_skip_structures():
    usable = {tuple(r["edge"]) for r in EXTRACT}
    [segment] = ordinary_candidates(EXTRACT, usable, WAYS, NODES)
    assert segment["edges"] == [[1, 2, 0], [2, 1, 0]]
    assert segment["names"] == ["Main Street"] and segment["ways"] == [10]
    assert segment["closure_id"].startswith("ordinary-source-segment-")
    # Way 12 touches a ford node, so it is not ordinary either.
    again = ordinary_candidates(list(reversed(EXTRACT)), usable, WAYS, NODES)
    assert again == [segment]


def test_ordinary_segments_differ_by_source_and_list_every_name():
    extract = [row(1, 2, [10]), row(2, 1, [10, 13])]
    usable = {(1, 2, 0), (2, 1, 0)}
    rows = ordinary_candidates(extract, usable, WAYS, NODES)
    assert len(rows) == 2
    assert sorted(r["names"] for r in rows) == [
        ["Elm Street", "Main Street"],
        ["Main Street"],
    ]
    assert ordinary_candidates(extract, {(1, 2, 0)}, WAYS, NODES)[0]["edges"] == [
        [1, 2, 0]
    ]


def test_structures_list_usable_edges_except_crossing_only_ones():
    usable = {(2, 3, 0), (3, 2, 0)}
    rows = structure_candidates(EXTRACT, usable, WAYS, opened={(3, 2, 0)})
    assert rows == [{"way": 11, "name": "Main Street", "edges": [[2, 3, 0]]}]


def test_crossing_rows_drop_the_opened_list():
    table = [
        {
            "corridor": "c",
            "river": "Hudson",
            "edges": [[1, 2, 0]],
            "ways": [10],
            "opened_edges": [[1, 2, 0]],
        }
    ]
    rows, opened = crossing_corridors(table)
    assert rows == [
        {"corridor_id": "c", "river": "Hudson", "edges": [[1, 2, 0]], "ways": [10]}
    ]
    assert opened == {(1, 2, 0)}


def test_shared_tables():
    edges = {
        "primary": [{"edge": [2, 3, 0], "ways": [11]}],
        "wide": [{"edge": [1, 2, 0], "ways": [10]}],
    }
    structures = {"primary": [{"way": 12, "name": None, "edges": []}], "wide": []}
    assert bundle_nodes(edges, {"primary": {"h": [4]}}, NODES) == {
        "1": [0.0, 0.0], "2": [0.001, 0.0], "3": [0.002, 0.0], "4": [0.003, 0.0]
    }  # fmt: skip
    used = used_ways(edges, structures)
    assert used == [10, 11, 12]
    assert bundle_ways(used, WAYS) == {"10": [1, 2], "11": [2, 3], "12": [3, 4]}
    assert way_names(used, WAYS) == {"10": "Main Street", "11": "Main Street"}
    assert basemap([row(1, 2, [10], "LINESTRING (0 0, 1 1)"), row(2, 1, [10])]) == [
        {"edge": [1, 2, 0], "geometry": "LINESTRING (0 0, 1 1)"}
    ]


def test_hospital_without_a_state_point_uses_its_campus_centroid():
    registry = {
        "b": {"name": "B", "lon": None, "lat": None},
        "a": {"name": "A", "lon": 1.0, "lat": 2.0},
        "c": {"name": "C", "lon": None, "lat": None},
    }
    out = hospital_locations(registry, {"b": box(0, 0, 2, 4)})
    assert out == {
        "a": {"name": "A", "lon": 1.0, "lat": 2.0},
        "b": {"name": "B", "lon": 1.0, "lat": 2.0},
    }
    assert list(out) == ["a", "b"]
