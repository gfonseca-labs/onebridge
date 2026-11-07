"""OpenStreetMap turn restrictions and the turn-expanded routing graph.

In the turn graph a state is "arrived on this edge". An arc ``a -> b`` costs
the travel time of ``b`` and exists only if turning from ``a`` into ``b`` is
allowed. Restrictions with a via node are checked at that node; restrictions
whose via is a chain of ways use cloned states that remember the approach
while the route is on the chain. Origins start from ``("start", node)``
states, and a hospital is reached on arriving at any of its nodes, so the
routing and closure functions work on the turn graph unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise

import networkx as nx

CAR_EXEMPT = {"motorcar", "motor_vehicle", "vehicle"}
IGNORED = {"no_right_turn_on_red", "no_left_turn_on_red"}


def applicable(tags):
    """Classify a restriction: ``"car"``, ``"conditional"`` or ``None``."""
    exempt = {x.strip() for x in tags.get("except", "").split(";") if x.strip()}
    if exempt & CAR_EXEMPT:
        return None
    if tags.get("restriction") and tags["restriction"] not in IGNORED:
        return "car"
    if tags.get("restriction:conditional") or tags.get("restriction:motorcar"):
        return "conditional"
    return None


def restriction_value(tags):
    """The ``no_*``/``only_*`` value, ignoring any ``@ (condition)`` part."""
    return (
        tags.get("restriction")
        or tags.get("restriction:motorcar")
        or tags.get("restriction:conditional", "").split("@")[0].strip()
    )


def compile_restrictions(relations, *, include_conditional):
    """Index restriction relations for :func:`build_turn_graph`.

    Returns ``(by_node, via_way_groups, unrepresented_ids)``. Time-limited
    restrictions are included only when ``include_conditional`` is set.
    Relations the graph cannot represent (missing members, mixed or multiple
    via nodes) are listed so they can be reported.
    """
    by_node, groups, unrepresented = defaultdict(list), defaultdict(list), []
    for relation in relations:
        kind = applicable(relation["tags"])
        if kind is None or (kind == "conditional" and not include_conditional):
            continue
        value = restriction_value(relation["tags"])
        members = relation["members"]
        froms = {m["ref"] for m in members if m["role"] == "from" and m["type"] == "w"}
        tos = {m["ref"] for m in members if m["role"] == "to" and m["type"] == "w"}
        via_nodes = [
            m["ref"] for m in members if m["role"] == "via" and m["type"] == "n"
        ]
        via_ways = [
            m["ref"] for m in members if m["role"] == "via" and m["type"] == "w"
        ]
        if not froms or not tos or not value or not value.startswith(("no_", "only_")):
            unrepresented.append(relation["id"])
        elif len(via_nodes) == 1 and not via_ways:
            by_node[via_nodes[0]].append((value, froms, tos))
        elif via_ways and not via_nodes:
            for from_way in froms:
                groups[(from_way, tuple(via_ways))].append((value, tos))
        else:
            unrepresented.append(relation["id"])
    return dict(by_node), dict(groups), sorted(unrepresented)


def _forbidden(value, hit):
    return hit if value.startswith("no_") else not hit


def piece_of(state):
    """The graph edge for a turn-graph state, or ``None`` for a start state."""
    if state[0] == "start":
        return None
    return state[0] if isinstance(state[0], tuple) else state


def build_turn_graph(
    graph, edge_ways, way_nodes, by_node, groups, start_nodes, *, u_turn_banned=()
):
    """Build the turn graph.

    ``edge_ways(edge)`` returns the OSM way IDs an edge was built from and
    ``way_nodes`` maps way IDs to their node sets. ``u_turn_banned`` lists
    nodes where U-turns are forbidden even without a mapped restriction.

    Returns ``(turn_graph, arcs_into)`` where ``arcs_into[edge]`` lists the
    turn-graph arcs that enter ``edge``, which is what a closure removes.
    """
    tg = nx.MultiDiGraph()
    arcs_into = defaultdict(list)
    ways = {e: frozenset(edge_ways(e)) for e in graph.edges(keys=True)}
    banned = frozenset(u_turn_banned)
    chain_entry = defaultdict(list)
    chain_positions = defaultdict(list)
    for group in groups:
        _, vias = group
        chain_entry[vias[0]].append(group)
        for i, way in enumerate(vias):
            chain_positions[way].append((group, i))

    def ways_at(edge, node):
        return {w for w in ways[edge] if node in way_nodes.get(w, ())}

    def node_allows(a, b, node):
        if node in banned and b[0] == a[1] and b[1] == a[0] and ways[a] == ways[b]:
            return False
        if isinstance(node, str) or node not in by_node:
            return True
        from_ways, to_ways = ways_at(a, node), ways_at(b, node)
        return not any(
            from_ways & froms and _forbidden(value, bool(to_ways & tos))
            for value, froms, tos in by_node[node]
        )

    def add(x, y, edge):
        key = tg.add_edge(
            x, y, travel_time=graph.edges[edge]["travel_time"], movement=edge
        )
        arcs_into[edge].append((x, y, key))

    for node in graph.nodes:
        incoming = list(graph.in_edges(node, keys=True))
        outgoing = list(graph.out_edges(node, keys=True))
        for a in incoming:
            from_ways = ways_at(a, node)
            for b in outgoing:
                if not node_allows(a, b, node):
                    continue
                entering = [
                    group
                    for way in ways[b] & chain_entry.keys()
                    if way not in ways[a]
                    for group in chain_entry[way]
                    if group[0] in from_ways
                ]
                for group in entering:
                    add(a, (b, (*group, 0)), b)
                if not entering:
                    add(a, b, b)
            # States for routes already on a via-way chain.
            for way in ways[a] & chain_positions.keys():
                for group, i in chain_positions[way]:
                    from_way, vias = group
                    clone = (a, (from_way, vias, i))
                    for b in outgoing:
                        if not node_allows(a, b, node):
                            continue
                        if vias[i] in ways[b]:
                            add(clone, (b, (from_way, vias, i)), b)
                        elif i + 1 < len(vias) and vias[i + 1] in ways[b]:
                            add(clone, (b, (from_way, vias, i + 1)), b)
                        elif i + 1 < len(vias):
                            add(clone, b, b)  # left the chain before its end
                        else:
                            to_ways = ways_at(b, node)
                            if not any(
                                _forbidden(value, bool(to_ways & tos))
                                for value, tos in groups[group]
                            ):
                                add(clone, b, b)
    for origin in start_nodes:
        for b in graph.out_edges(origin, keys=True):
            add(("start", origin), b, b)
    return tg, arcs_into


def hospital_states(tg, hospital_nodes):
    """Turn-graph states that arrive at any of ``hospital_nodes``, sorted."""
    states = []
    for state in tg.nodes:
        piece = piece_of(state)
        node = state[1] if piece is None else piece[1]
        if node in hospital_nodes:
            states.append(state)
    return sorted(states, key=str)


def path_pieces(tg, path_edges):
    """Graph edges entered along a turn-graph path, in order."""
    return [tg.edges[e]["movement"] for e in path_edges]


def index_restrictions(relations):
    """Index applicable restrictions for :func:`audit_route`."""
    by_node, by_way = {}, {}
    for relation in relations:
        kind = applicable(relation["tags"])
        if kind is None:
            continue
        value = restriction_value(relation["tags"])
        members = relation["members"]
        froms = [m["ref"] for m in members if m["role"] == "from" and m["type"] == "w"]
        tos = [m["ref"] for m in members if m["role"] == "to" and m["type"] == "w"]
        via_nodes = [
            m["ref"] for m in members if m["role"] == "via" and m["type"] == "n"
        ]
        via_ways = [
            m["ref"] for m in members if m["role"] == "via" and m["type"] == "w"
        ]
        if not froms or not tos or not value:
            continue
        entry = {
            "id": relation["id"],
            "value": value,
            "kind": kind,
            "froms": set(froms),
            "tos": set(tos),
            "via_ways": via_ways,
        }
        if len(via_nodes) == 1 and not via_ways:
            by_node.setdefault(via_nodes[0], []).append(entry)
        elif via_ways and not via_nodes:
            for from_way in froms:
                by_way.setdefault(from_way, []).append(entry)
    return by_node, by_way


def audit_route(movements, source_ways, way_nodes, by_node, by_way):
    """Check a route (a chain of edges) against restrictions independently.

    Returns findings: ``GRAPH_U_TURN``, ``RESTRICTION_VIOLATED`` or
    ``CONDITIONAL_RESTRICTION_TOUCHED``. Used to verify turn-graph routes.
    """
    findings = []
    way_sequence = []
    for movement in movements:
        for way in source_ways[movement]:
            if not way_sequence or way_sequence[-1] != way:
                way_sequence.append(way)
    for a, b in pairwise(movements):
        node = a[1]
        if b[0] != node:
            raise ValueError("route edges do not form a chain")
        from_ways = {w for w in source_ways[a] if node in way_nodes.get(w, ())}
        to_ways = {w for w in source_ways[b] if node in way_nodes.get(w, ())}
        if b[1] == a[0] and set(source_ways[a]) == set(source_ways[b]):
            findings.append({"kind": "GRAPH_U_TURN", "node": node})
        for entry in by_node.get(node, ()):
            if not from_ways & entry["froms"]:
                continue
            hit = bool(to_ways & entry["tos"])
            if entry["value"].startswith("no_"):
                violated = hit
            else:
                violated = (
                    entry["value"].startswith("only_") and not hit and bool(to_ways)
                )
            if violated:
                findings.append(_finding(entry, node=node))
    for i, way in enumerate(way_sequence):
        for entry in by_way.get(way, ()):
            via = entry["via_ways"]
            if way_sequence[i + 1 : i + 1 + len(via)] != via:
                continue
            after = i + 1 + len(via)
            if after >= len(way_sequence):
                continue
            hit = way_sequence[after] in entry["tos"]
            if entry["value"].startswith("no_"):
                violated = hit
            else:
                violated = entry["value"].startswith("only_") and not hit
            if violated:
                findings.append(_finding(entry, via_ways=via))
    return findings


def _finding(entry, **where):
    kind = (
        "RESTRICTION_VIOLATED"
        if entry["kind"] == "car"
        else "CONDITIONAL_RESTRICTION_TOUCHED"
    )
    return {"kind": kind, "relation": entry["id"], "value": entry["value"], **where}
