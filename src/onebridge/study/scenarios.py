"""Closure scenarios.

Three kinds of closure are evaluated:

* ordinary road segments (one OSM way between junctions, both directions);
* local structures: bridge and tunnel ways, merged when they share a name and
  a node, so a structure split into several ways closes as one;
* river crossing corridors, curated by name and river.

Only scenarios that touch a baseline route can change any origin's result
(see :mod:`onebridge.closures`), so ordinary segments and structures are
screened against the union of all baseline routes. Crossing corridors are
always evaluated.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict


def scenario_members(bundle, buffer, used):
    """Return ``{closure_id: [edge, ...]}`` for one buffer.

    ``used`` is the set of edges on at least one baseline route.
    """
    members = {}
    for row in bundle.ordinary(buffer):
        edges = [tuple(e) for e in row["edges"]]
        if set(edges) & used:
            members[row["closure_id"]] = edges
    members |= local_structures(bundle.structures(buffer), used, bundle.way_nodes())
    for corridor in bundle.crossings(buffer):
        if corridor["edges"]:
            members["crossing-" + corridor["corridor_id"]] = [
                tuple(e) for e in corridor["edges"]
            ]
    uncovered = used - {e for edges in members.values() for e in edges}
    if uncovered:
        raise ValueError(f"{len(uncovered)} route edges are in no scenario")
    return members


def local_structures(features, used, way_nodes):
    """Group structure ways on a route by shared name and shared node."""
    hits = {f["way"]: f for f in features if {tuple(e) for e in f["edges"]} & used}
    parent = {w: w for w in hits}

    def find(w):
        while parent[w] != w:
            w = parent[w]
        return w

    for a in hits:
        for b in hits:
            name = hits[a]["name"]
            if (
                a < b
                and name
                and name == hits[b]["name"]
                and set(way_nodes[a]) & set(way_nodes[b])
            ):
                parent[find(b)] = find(a)
    groups = defaultdict(list)
    for w in sorted(hits):
        groups[find(w)].append(w)
    structures = {}
    for ways in groups.values():
        digest = hashlib.sha256(json.dumps(sorted(ways)).encode()).hexdigest()[:16]
        structures["structure-corridor-" + digest] = sorted(
            {tuple(e) for w in ways for e in hits[w]["edges"]}
        )
    return structures
