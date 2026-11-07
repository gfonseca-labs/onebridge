import math
from copy import deepcopy

import networkx as nx
import pytest
from shapely.geometry import LineString

from onebridge.graph import attach_points, expand_closures


def road():
    g = nx.MultiDiGraph(crs="EPSG:32618")
    g.add_node("a", x=0, y=0)
    g.add_node("b", x=100, y=0)
    # Geometry stored in the opposite direction must be reoriented.
    g.add_edge(
        "a",
        "b",
        key=0,
        length=100,
        travel_time=10,
        geometry=LineString([(100, 0), (0, 0)]),
    )
    g.add_edge("b", "a", key=0, length=100, travel_time=20)
    g.add_edge("a", "b", key=1, length=100, travel_time=30)
    return g


def test_split_conserves_cost_and_only_splits_the_named_direction():
    original = road()
    before = deepcopy(original)
    split, nodes, pieces = attach_points(
        original,
        {
            "origin-a": {"edge": ("a", "b", 0), "fraction": 0.25},
            "origin-b": {"edge": ("a", "b", 0), "fraction": 0.25},
            "hospital-c": {"edge": ("a", "b", 0), "fraction": 0.75},
        },
    )
    assert nx.utils.graphs_equal(original, before)
    assert nodes["origin-a"] == nodes["origin-b"]
    assert split.nodes[nodes["origin-a"]]["x"] == 25
    parts = pieces[("a", "b", 0)]
    assert math.fsum(split.edges[e]["travel_time"] for e in parts) == 10
    assert math.fsum(split.edges[e]["length"] for e in parts) == 100
    assert split.has_edge("b", "a", 0) and split.has_edge("a", "b", 1)
    assert (
        nx.shortest_path_length(
            split, nodes["origin-a"], nodes["hospital-c"], weight="travel_time"
        )
        == 5
    )
    split.remove_edges_from(parts)
    assert not nx.has_path(split, nodes["origin-a"], "b")


def test_endpoint_fractions_reuse_existing_nodes():
    _, nodes, _ = attach_points(
        road(),
        {
            "a": {"edge": ("a", "b", 0), "fraction": 0},
            "b": {"edge": ("a", "b", 0), "fraction": 1},
        },
    )
    assert nodes == {"a": "a", "b": "b"}


@pytest.mark.parametrize("fraction", [-1, 1.01, True, float("nan")])
def test_invalid_fraction(fraction):
    with pytest.raises(ValueError):
        attach_points(road(), {"o": {"edge": ("a", "b", 0), "fraction": fraction}})


def test_rejects_unknown_edge_wrong_crs_and_mismatched_geometry():
    with pytest.raises(ValueError, match="unknown edge"):
        attach_points(road(), {"o": {"edge": ("a", "b", 99), "fraction": 0.5}})
    g = road()
    g.graph["crs"] = "EPSG:4326"
    with pytest.raises(ValueError, match="32618"):
        attach_points(g, {})
    g = road()
    g.edges["a", "b", 0]["geometry"] = LineString([(0, 20), (100, 20)])
    with pytest.raises(ValueError, match="endpoints"):
        attach_points(g, {"o": {"edge": ("a", "b", 0), "fraction": 0.5}})


def test_closures_expand_to_every_piece():
    g = road()
    split, _, pieces = attach_points(g, {"o": {"edge": ("a", "b", 0), "fraction": 0.5}})
    expanded = expand_closures(
        g, split, {"c-1": [("a", "b", 0)], "c-2": [("b", "a", 0)]}, pieces
    )
    assert expanded["c-1"] == pieces[("a", "b", 0)]
    assert expanded["c-2"] == [("b", "a", 0)]
    with pytest.raises(ValueError, match="unknown edge"):
        expand_closures(g, split, {"c-3": [("a", "b", 7)]}, pieces)
