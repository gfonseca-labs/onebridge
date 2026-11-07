import math
import random

import networkx as nx
import pytest

from onebridge.routing import original_movements, routes_with_paths


def random_graph(rng, nodes, edges, times=(1.0, 2.0, 2.5, 3.0)):
    g = nx.MultiDiGraph()
    g.add_nodes_from(range(nodes))
    for _ in range(edges):
        u, v = rng.sample(range(nodes), 2)
        g.add_edge(u, v, travel_time=rng.choice(times))
    return g


def nearest_by_networkx(g, hospitals, origin):
    best = None
    for hid, nodes in hospitals.items():
        for node in nodes:
            try:
                t = nx.shortest_path_length(g, origin, node, weight="travel_time")
            except nx.NetworkXNoPath:
                continue
            if best is None or (t, hid) < best:
                best = (float(t), hid)
    return best


@pytest.mark.parametrize("seed", range(25))
def test_matches_networkx_and_paths_realize_the_time(seed):
    rng = random.Random(seed)
    g = random_graph(rng, 30, 90)
    hospitals = {"h-a": (0,), "h-b": (1, 2)}
    origins = {f"o-{n}": (n,) for n in range(3, 30)}
    routes = routes_with_paths(g, hospitals, origins)
    for oid, route in routes.items():
        expected = nearest_by_networkx(g, hospitals, origins[oid][0])
        if expected is None:
            assert route["status"] == "unreachable" and route["path_edges"] == []
            continue
        assert route["travel_time_seconds"] == pytest.approx(expected[0])
        assert route["hospital_id"] == expected[1]
        total = math.fsum(g.edges[e]["travel_time"] for e in route["path_edges"])
        assert total == pytest.approx(route["travel_time_seconds"])
        end = route["path_edges"][-1][1] if route["path_edges"] else origins[oid][0]
        assert end in hospitals[route["hospital_id"]]


def test_removing_an_edge_off_every_path_changes_nothing():
    rng = random.Random(3)
    g = random_graph(rng, 25, 120, times=(1.0, 2.0, 3.0))
    hospitals = {"h-a": (0,), "h-b": (1,)}
    origins = {f"o-{n}": (n,) for n in range(2, 25)}
    base = routes_with_paths(g, hospitals, origins)
    used = {e for r in base.values() for e in r["path_edges"]}
    for edge in list(g.edges(keys=True)):
        if edge in used:
            continue
        h = g.copy()
        h.remove_edge(*edge)
        after = routes_with_paths(h, hospitals, origins)
        for oid, route in base.items():
            assert after[oid]["hospital_id"] == route["hospital_id"]
            assert after[oid]["travel_time_seconds"] == route["travel_time_seconds"]


def test_cheapest_parallel_edge_and_unreachable_origin():
    g = nx.MultiDiGraph()
    g.add_edge("o", "h", travel_time=5.0)
    g.add_edge("o", "h", travel_time=2.0)
    g.add_node("lonely")
    routes = routes_with_paths(g, {"h-1": ("h",)}, {"o-1": ("o",), "o-2": ("lonely",)})
    assert routes["o-1"]["travel_time_seconds"] == 2.0
    assert routes["o-1"]["path_edges"] == [("o", "h", 1)]
    assert routes["o-2"]["status"] == "unreachable"


def test_equal_times_go_to_the_smaller_hospital_id():
    g = nx.MultiDiGraph()
    g.add_edge("o", "x", travel_time=1.0)
    g.add_edge("o", "y", travel_time=1.0)
    routes = routes_with_paths(g, {"h-b": ("x",), "h-a": ("y",)}, {"o-1": ("o",)})
    assert routes["o-1"]["hospital_id"] == "h-a"


def test_split_pieces_collapse_to_original_edges():
    g = nx.MultiDiGraph()
    g.add_edge("a", "s", travel_time=1.0, original_edge=("a", "b", 0))
    g.add_edge("s", "b", travel_time=1.0, original_edge=("a", "b", 0))
    g.add_edge("b", "c", travel_time=1.0)
    path = [("a", "s", 0), ("s", "b", 0), ("b", "c", 0)]
    assert original_movements(g, path) == [("a", "b", 0), ("b", "c", 0)]
