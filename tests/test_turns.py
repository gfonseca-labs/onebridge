import random

import networkx as nx
import pytest

from onebridge.closures import evaluate_closures, evaluate_local
from onebridge.routing import routes_with_paths
from onebridge.turns import (
    audit_route,
    build_turn_graph,
    compile_restrictions,
    hospital_states,
    index_restrictions,
    path_pieces,
)

# 1 -> 2 -> 3 on way 10; 2 -> 4 on way 20 (the hospital is at 4);
# detour 2 -> 5 -> 6 -> 4 on ways 30, 40, 50; slow link 3 -> 4 on way 60.
EDGES = {
    (1, 2, 0): (10, 1.0),
    (2, 3, 0): (10, 1.0),
    (2, 4, 0): (20, 1.0),
    (2, 5, 0): (30, 1.0),
    (5, 6, 0): (40, 1.0),
    (6, 4, 0): (50, 1.0),
    (3, 4, 0): (60, 5.0),
}
WAY_NODES = {10: {1, 2, 3}, 20: {2, 4}, 30: {2, 5}, 40: {5, 6}, 50: {6, 4}, 60: {3, 4}}


def road():
    g = nx.MultiDiGraph()
    for (u, v, k), (_, seconds) in EDGES.items():
        g.add_edge(u, v, key=k, travel_time=seconds)
    return g


def edge_ways(edge):
    return [EDGES[edge][0]]


def restriction(value, from_way, via, to_way, **tags):
    via_member = (
        {"type": "w", "ref": via[0], "role": "via"}
        if isinstance(via, list)
        else {"type": "n", "ref": via, "role": "via"}
    )
    tags = {"type": "restriction", **({"restriction": value} if value else {}), **tags}
    members = [
        {"type": "w", "ref": from_way, "role": "from"},
        {"type": "w", "ref": to_way, "role": "to"},
        via_member,
    ]
    return {"id": 21, "tags": tags, "members": members}


def route(relations, conditional=False):
    by_node, groups, _ = compile_restrictions(
        relations, include_conditional=conditional
    )
    tg, arcs = build_turn_graph(road(), edge_ways, WAY_NODES, by_node, groups, [1])
    seeds = tuple(hospital_states(tg, {4}))
    result = routes_with_paths(tg, {"h-1": seeds}, {"o-1": (("start", 1),)})["o-1"]
    return tg, arcs, seeds, result


def test_no_restrictions_matches_the_plain_graph():
    assert route([])[3]["travel_time_seconds"] == 2.0


def test_no_left_turn_forces_the_detour():
    tg, _, _, result = route([restriction("no_left_turn", 10, 2, 20)])
    assert result["travel_time_seconds"] == 4.0
    assert path_pieces(tg, result["path_edges"])[1] == (2, 5, 0)


def test_only_straight_on_forces_the_long_way():
    assert (
        route([restriction("only_straight_on", 10, 2, 10)])[3]["travel_time_seconds"]
        == 7.0
    )


def test_time_limited_rules_apply_only_when_requested():
    rule = [
        restriction(
            None,
            10,
            2,
            20,
            **{"restriction:conditional": "no_left_turn @ (07:00-19:00)"},
        )
    ]
    assert route(rule)[3]["travel_time_seconds"] == 2.0
    assert route(rule, conditional=True)[3]["travel_time_seconds"] == 4.0


def test_via_way_restriction_blocks_only_its_approach():
    rules = [
        restriction("no_left_turn", 10, 2, 20),
        restriction("no_straight_on", 30, [40], 50),
    ]
    assert route(rules)[3]["travel_time_seconds"] == 7.0


def test_multi_way_via_chain():
    chain = {
        "id": 99,
        "tags": {"type": "restriction", "restriction": "no_straight_on"},
        "members": [
            {"type": "w", "ref": 10, "role": "from"},
            {"type": "w", "ref": 30, "role": "via"},
            {"type": "w", "ref": 40, "role": "via"},
            {"type": "w", "ref": 50, "role": "to"},
        ],
    }
    _, groups, unrepresented = compile_restrictions([chain], include_conditional=False)
    assert unrepresented == [] and (10, (30, 40)) in groups
    result = route([restriction("no_left_turn", 10, 2, 20), chain])[3]
    assert result["travel_time_seconds"] == 7.0


def test_routes_pass_the_independent_audit():
    rules = [restriction("no_left_turn", 10, 2, 20)]
    tg, _, _, result = route(rules)
    by_node, by_way = index_restrictions(rules)
    source = {e: [EDGES[e][0]] for e in EDGES}
    assert (
        audit_route(
            path_pieces(tg, result["path_edges"]), source, WAY_NODES, by_node, by_way
        )
        == []
    )


def test_audit_flags_a_prohibited_turn():
    by_node, by_way = index_restrictions([restriction("no_left_turn", 10, 2, 20)])
    source = {e: [EDGES[e][0]] for e in EDGES}
    findings = audit_route([(1, 2, 0), (2, 4, 0)], source, WAY_NODES, by_node, by_way)
    assert [f["kind"] for f in findings] == ["RESTRICTION_VIOLATED"]


def test_closures_on_the_turn_graph_match_the_reference():
    tg, arcs, seeds, base = route([restriction("no_left_turn", 10, 2, 20)])
    closures = {"c-1": tuple(arcs[(2, 5, 0)]), "c-2": tuple(arcs[(3, 4, 0)])}
    hospitals, origins = {"h-1": seeds}, {"o-1": (("start", 1),)}
    local = evaluate_local(tg, hospitals, origins, closures, {"o-1": base})
    full = evaluate_closures(tg, hospitals, origins, closures)
    assert local["ranking"] == full["ranking"]
    assert full["ranking"][0]["total_finite_added_minutes"] == pytest.approx(3 / 60)


def test_u_turn_ban_applies_only_at_listed_nodes():
    g = nx.MultiDiGraph()
    for u, v, t in [(1, 2, 1.0), (2, 1, 1.0), (1, 9, 1.0), (2, 3, 5.0), (3, 9, 5.0)]:
        g.add_edge(u, v, travel_time=t)
    ways = {
        (1, 2, 0): [10],
        (2, 1, 0): [10],
        (1, 9, 0): [11],
        (2, 3, 0): [12],
        (3, 9, 0): [13],
    }
    way_nodes = {10: {1, 2}, 11: {1, 9}, 12: {2, 3}, 13: {3, 9}}
    tg, _ = build_turn_graph(g, ways.get, way_nodes, {}, {}, [1])
    assert tg.has_edge((1, 2, 0), (2, 1, 0))
    tg, _ = build_turn_graph(g, ways.get, way_nodes, {}, {}, [1], u_turn_banned={2})
    assert not tg.has_edge((1, 2, 0), (2, 1, 0))


@pytest.mark.parametrize("seed", range(15))
def test_without_restrictions_the_turn_graph_equals_the_road_graph(seed):
    rng = random.Random(seed)
    g = nx.MultiDiGraph()
    for _ in range(70):
        u, v = rng.sample(range(22), 2)
        g.add_edge(u, v, travel_time=rng.choice([1.0, 2.0, 3.0]))
    hospitals = {
        h: tuple(n for n in ns if n in g and g.in_degree(n))
        for h, ns in {"h-a": (0,), "h-b": (1, 2)}.items()
    }
    hospitals = {h: ns for h, ns in hospitals.items() if ns}
    if not hospitals:
        pytest.skip("no reachable hospital node")
    targets = {n for ns in hospitals.values() for n in ns}
    starts = [n for n in g if n not in targets]
    tg, _ = build_turn_graph(g, lambda e: [1], {}, {}, {}, starts)
    turn_hospitals = {
        h: tuple(hospital_states(tg, set(ns))) for h, ns in hospitals.items()
    }
    plain = routes_with_paths(g, hospitals, {f"o-{n}": (n,) for n in starts})
    turned = routes_with_paths(
        tg, turn_hospitals, {f"o-{n}": (("start", n),) for n in starts}
    )
    for oid, result in plain.items():
        for key in ("status", "hospital_id", "travel_time_seconds"):
            assert turned[oid][key] == result[key]
