"""Separate endpoint closures from the main ranking.

Closing the road an origin departs on, or the road a hospital is reached
from, measures how the endpoints were attached rather than how the network
reroutes. Those scenarios move to a separate endpoint table; nothing is
dropped, and both tables use the same ranking rules. Main-table scenarios in
the top 10, with new disconnections, or next to an endpoint are flagged for
a turn and detour review.
"""

from __future__ import annotations

from ..ranking import rank_closures

REVIEW_TOP = 10


def endpoint_edges(bundle, buffer):
    """Return ``(departures by spacing, arrivals)`` as sets of original edges.

    Arrivals are the edges hospital points are split on plus every edge
    entering a hospital arrival node.
    """
    endpoints = bundle.endpoints(buffer)
    arrival_nodes = {n for nodes in endpoints["hospital_nodes"].values() for n in nodes}
    arrivals = {
        tuple(edge)
        for points in endpoints["hospital_splits"].values()
        for edge, _ in points
    }
    arrivals |= {
        tuple(row["edge"])
        for row in bundle.edges(buffer)
        if row["edge"][1] in arrival_nodes
    }
    departures = {
        int(spacing): {tuple(e) for _, e, _ in grid["departures"]}
        | {tuple(e) for _, e, _ in grid["reverse"]}
        for spacing, grid in endpoints["origins"].items()
    }
    return departures, arrivals


def adjacent_edges(edges, departures, arrivals):
    """Edges entering an arrival's start node or leaving a departure's end."""
    starts = {e[0] for e in arrivals}
    ends = {e[1] for e in departures}
    return {e for e in edges if e[1] in starts or e[0] in ends} - arrivals - departures


def split_ranking(ranking, members, departures, arrivals):
    """Return ``(main, endpoint)`` ranked tables."""
    all_edges = {e for edges in members.values() for e in edges}
    adjacent = adjacent_edges(all_edges, departures, arrivals)
    main_rows, endpoint_rows, seen = [], [], set()
    for row in ranking:
        cid = row["closure_id"]
        seen.add(cid)
        edges = {tuple(e) for e in members[cid]}
        base = {k: v for k, v in row.items() if k != "rank"}
        touched = [
            name
            for name, hit in (
                ("ORIGIN_DEPARTURE", edges & departures),
                ("ED_ARRIVAL", edges & arrivals),
            )
            if hit
        ]
        if touched:
            endpoint_rows.append(
                dict(
                    base,
                    endpoint_class="+".join(touched),
                    interpretation="ENDPOINT_CONVENTION_DEPENDENT_NOT_NETWORK_FINDING",
                )
            )
        else:
            main_rows.append(dict(base, adjacent_to_endpoint=bool(edges & adjacent)))
    if seen != set(members):
        raise ValueError("ranking and scenario members cover different closures")
    main = rank_closures(main_rows)
    for row in main:
        reasons = []
        if row["rank"] <= REVIEW_TOP:
            reasons.append("TOP_10")
        if row["newly_disconnected_origins"] > 0:
            reasons.append("NEW_DISCONNECTION")
        if row["adjacent_to_endpoint"]:
            reasons.append("ADJACENT_TO_ENDPOINT")
        row["turn_detour_audit_required"] = reasons
    return main, rank_closures(endpoint_rows)
