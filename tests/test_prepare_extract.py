import networkx as nx
import pytest
from shapely.geometry import LineString

from onebridge.prepare.extract import (
    edge_rows,
    routing_polygon,
    way_filter,
)

FILTER = (
    '["highway"]["area"!~"yes"]["access"!~"private"]'
    '["highway"!~"footway|path|steps"]["service"!~"parking|private"]'
)


def test_way_filter_matches_overpass_semantics():
    accepts = way_filter(FILTER)
    assert accepts({"highway": "residential"})
    assert not accepts({"name": "no highway tag"})
    assert not accepts({"highway": "footway"})
    assert not accepts({"highway": "service", "service": "parking_aisle"})
    assert not accepts({"highway": "residential", "access": "private"})
    assert accepts({"highway": "residential", "access": "yes"})


def test_unsupported_filter_grammar_is_rejected():
    with pytest.raises(ValueError):
        way_filter('["highway"~"primary"]')


def test_routing_polygon_contains_the_rectangle_with_margin():
    bbox = [-74.035, 40.690, -73.930, 40.770]
    small, large = routing_polygon(bbox, 10000), routing_polygon(bbox, 20000)
    assert large.contains(small)
    west, south, east, north = small.bounds
    assert west < bbox[0] - 0.1 and north > bbox[3] + 0.08


def test_edge_rows_use_graphml_text_values():
    g = nx.MultiDiGraph()
    g.add_edge(
        1,
        2,
        key=0,
        osmid=[20, 10],
        highway=["residential", "service"],
        maxspeed="25 mph",
        length=12.5,
        geometry=LineString([(0, 0), (1, 1)]),
    )
    g.add_edge(2, 1, key=0, osmid=10, highway="residential", length=3.0)
    rows = edge_rows(g)
    assert rows[0] == {
        "edge": [1, 2, 0],
        "highway": "['residential', 'service']",
        "maxspeed": "25 mph",
        "length": 12.5,
        "geometry": "LINESTRING (0 0, 1 1)",
        "ways": [10, 20],
    }
    assert rows[1]["maxspeed"] is None and rows[1]["ways"] == [10]
