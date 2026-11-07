import pytest
from pyproj import Transformer

from onebridge.prepare.origins import (
    candidate_grid,
    cell_population,
    departures,
    kept_origins,
    reverse_departures,
)

BBOX = [-74.035, 40.690, -73.930, 40.770]
UNPROJECT = Transformer.from_crs(32618, 4326, always_xy=True).transform


def lonlat(x, y):
    return list(UNPROJECT(x, y))


def test_grid_points_lie_in_the_rectangle_on_the_metre_grid():
    grid = candidate_grid(BBOX, 500)
    assert list(grid) == sorted(grid)
    for origin, (x, y, lon, lat) in grid.items():
        _, spacing, i, j = origin.split("-")
        assert (x, y) == (int(i) * 500, int(j) * 500) and spacing == "500"
        assert BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]
    assert len(candidate_grid(BBOX, 250)) > 3.9 * len(grid)


def test_kept_origins_are_clear_then_building_context():
    grid = dict.fromkeys(["a", "b", "c", "d"])
    review = [
        {"origin": "d", "status": "clear", "reasons": []},
        {"origin": "a", "status": "building_context", "reasons": ["courtyard"]},
        {"origin": "c", "status": "clear", "reasons": []},
        {"origin": "b", "status": "held", "reasons": ["open_water"]},
    ]
    assert kept_origins(grid, review) == ["c", "d", "a"]
    with pytest.raises(ValueError, match="does not match"):
        kept_origins(grid, review[:3])
    with pytest.raises(ValueError, match="twice"):
        kept_origins(grid, review + review[:1])
    with pytest.raises(ValueError, match="status"):
        kept_origins({"a": None}, [{"origin": "a", "status": "kept", "reasons": []}])


def test_departure_uses_the_nearest_edge_and_breaks_ties_by_routing_order():
    x0, y0 = 584000, 4510000
    nodes = {1: lonlat(x0, y0), 2: lonlat(x0 + 100, y0), 3: lonlat(x0, y0 + 300)}
    edges = [
        {"edge": [1, 2, 0], "geometry": None},
        {"edge": [1, 3, 0], "geometry": None},
        {"edge": [2, 1, 0], "geometry": None},
    ]
    rows = departures(edges, nodes, {"o": (x0 + 25, y0 + 10), "p": (x0 - 5, y0 + 150)})
    assert [r[:2] for r in rows] == [["o", [1, 2, 0]], ["p", [1, 3, 0]]]
    assert rows[0][2] == pytest.approx(0.25, abs=1e-6)
    assert rows[1][2] == pytest.approx(0.5, abs=1e-6)
    reverse = reverse_departures(rows, {(1, 2, 0), (2, 1, 0), (1, 3, 0)})
    assert reverse == [["o", [2, 1, 0], 1 - rows[0][2]]]


def test_departure_follows_edge_geometry():
    x0, y0 = 584000, 4510000
    bend = [lonlat(x0, y0), lonlat(x0, y0 + 100), lonlat(x0 + 100, y0 + 100)]
    wkt = "LINESTRING (" + ", ".join(f"{lon!r} {lat!r}" for lon, lat in bend) + ")"
    nodes = {1: bend[0], 2: bend[2]}
    edges = [{"edge": [1, 2, 0], "geometry": wkt}]
    [(_, _, fraction)] = departures(edges, nodes, {"o": (x0 + 50, y0 + 110)})
    assert fraction == pytest.approx(0.75, abs=1e-6)


def test_block_population_goes_to_the_cell_around_its_point():
    grid = {"a": (1000, 2000, 0, 0), "b": (1500, 2000, 0, 0)}
    blocks = [(10, 1240, 1760), (5, 1260, 2000), (7, 3000, 3000)]
    assert cell_population(blocks, grid, 500) == {"a": 10, "b": 5}
