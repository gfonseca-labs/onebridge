import random

import networkx as nx
import pytest

from onebridge.closures import (
    _best_label,
    _Guide,
    best_route,
    evaluate_closures,
    evaluate_local,
)
from onebridge.routing import routes_with_paths


def random_case(seed, nodes=28, edges=85):
    rng = random.Random(seed)
    g = nx.MultiDiGraph()
    g.add_nodes_from(range(nodes))
    for _ in range(edges):
        u, v = rng.sample(range(nodes), 2)
        g.add_edge(u, v, travel_time=rng.choice([1.0, 1.5, 2.0, 3.0]))
    hospitals = {"h-a": (0,), "h-b": (1,), "h-c": (2, 3)}
    origins = {f"o-{i}": (i,) for i in range(4, nodes)}
    all_edges = list(g.edges(keys=True))
    closures = {
        f"c-{i:03d}": tuple(rng.sample(all_edges, rng.choice([1, 2])))
        for i in range(25)
    }
    return g, hospitals, origins, closures


@pytest.mark.parametrize("seed", range(30))
def test_local_evaluation_matches_the_reference(seed):
    g, hospitals, origins, closures = random_case(seed)
    base = routes_with_paths(g, hospitals, origins)
    if not any(r["status"] == "reachable" for r in base.values()):
        pytest.skip("no origin reaches a hospital in this graph")
    full = evaluate_closures(g, hospitals, origins, closures)
    local = evaluate_local(g, hospitals, origins, closures, base)
    assert local["ranking"] == full["ranking"]
    by_key = {(r["closure_id"], r["origin_id"]): r for r in full["route_outcomes"]}
    affected = set()
    for record in local["affected_route_outcomes"]:
        key = (record["closure_id"], record["origin_id"])
        assert by_key[key] == record
        affected.add(key)
    for key, record in by_key.items():
        if record["scenario"] != record["baseline"]:
            assert key in affected


def test_ties_after_a_closure_go_to_the_smaller_hospital_id():
    g = nx.MultiDiGraph()
    g.add_edge("o", "x", travel_time=1.0)
    g.add_edge("x", "hb", travel_time=1.0)
    g.add_edge("x", "ha", travel_time=1.0)
    g.add_edge("o", "ha", travel_time=1.5)
    hospitals, origins = {"h-a": ("ha",), "h-b": ("hb",)}, {"o-1": ("o",)}
    base = routes_with_paths(g, hospitals, origins)
    result = evaluate_local(g, hospitals, origins, {"c-1": (("o", "ha", 0),)}, base)
    assert result["affected_route_outcomes"][0]["scenario"]["hospital_id"] == "h-a"


def test_disconnection_is_counted_separately():
    g = nx.MultiDiGraph()
    g.add_edge("o", "h", travel_time=1.0)
    hospitals, origins = {"h-1": ("h",)}, {"o-1": ("o",)}
    base = routes_with_paths(g, hospitals, origins)
    row = evaluate_local(g, hospitals, origins, {"c-1": (("o", "h", 0),)}, base)[
        "ranking"
    ][0]
    assert row["total_finite_added_minutes"] == 0
    assert row["newly_disconnected_origins"] == 1
    assert row["mean_finite_added_minutes"] is None


def test_unknown_closure_edge_is_rejected():
    g = nx.MultiDiGraph()
    g.add_edge("o", "h", travel_time=1.0)
    base = routes_with_paths(g, {"h-1": ("h",)}, {"o-1": ("o",)})
    with pytest.raises(ValueError, match="unknown edge"):
        evaluate_local(
            g, {"h-1": ("h",)}, {"o-1": ("o",)}, {"c-1": (("h", "o", 0),)}, base
        )


@pytest.mark.parametrize("seed", range(20))
def test_best_route_matches_the_label_search(seed):
    g, hospitals, origins, closures = random_case(seed)
    hospital_at = {}
    for hid, nodes in hospitals.items():
        for node in nodes:
            hospital_at.setdefault(node, []).append(hid)
    for edges in list(closures.values())[:5]:
        closed = set(edges)
        for starts in origins.values():
            label, path = best_route(g, starts, hospital_at, closed)
            assert label == _best_label(g, starts, hospital_at, closed)
            if label is None:
                continue
            assert not closed & set(path)
            assert sum(g.edges[e]["travel_time"] for e in path) == pytest.approx(
                label[0]
            )
            end = path[-1][1] if path else starts[0]
            assert label[1] in hospital_at[end]


@pytest.mark.parametrize("seed", range(40))
def test_guided_search_settles_exactly_the_same_labels(seed):
    """A* re-solves must return bit-identical seconds and the same hospital."""
    rng = random.Random(1000 + seed)
    nodes = rng.choice([30, 60, 120])
    g = nx.MultiDiGraph()
    g.add_nodes_from(range(nodes))
    for _ in range(nodes * 3):
        u, v = rng.sample(range(nodes), 2)
        # Awkward decimals make rounding differences show up; repeats make ties.
        cost = rng.choice([0.1, 0.2, 0.3, 0.7, 1 / 3, 2.5, rng.uniform(0.01, 9.0)])
        g.add_edge(u, v, travel_time=cost)
    hospital_at = {0: ["h-b"], 1: ["h-a"], 2: ["h-a", "h-c"]}
    guide = _Guide(g, hospital_at)
    edges = list(g.edges(keys=True))
    for _ in range(30):
        closed = set(rng.sample(edges, rng.choice([1, 2, 5, 20])))
        starts = tuple(rng.sample(range(nodes), rng.choice([1, 2])))
        expected = _best_label(g, starts, hospital_at, closed)
        assert guide.label(starts, closed) == expected


def test_guided_search_skips_nodes_that_never_reach_a_hospital():
    g = nx.MultiDiGraph()
    g.add_edge("o", "h", travel_time=1.0)
    g.add_edge("o", "dead", travel_time=0.5)
    g.add_edge("dead", "end", travel_time=0.5)
    guide = _Guide(g, {"h": ["h-1"]})
    assert "dead" not in guide.bound and "end" not in guide.bound
    assert guide.label(("o",), {("o", "h", 0)}) is None
    assert guide.label(("o",), set()) == (1.0, "h-1")
