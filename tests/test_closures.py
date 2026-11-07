import random

import networkx as nx
import pytest

from onebridge.closures import (
    _best_label,
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
