"""Bundle-wide tables: node and way lookups, names, basemap and hospitals.

* ``nodes.json.gz``: lon/lat of every node a usable edge or hospital
  arrival uses, in either buffer;
* ``ways.json.gz``: the node list of every way behind a usable edge or a
  structure candidate (structures are grouped by shared nodes);
* ``way-names.json.gz``: the names of those ways, for labelling closures;
* ``primary/basemap.jsonl.gz``: every primary-buffer edge that has a curved
  geometry, for drawing the map;
* ``hospitals.json``: name and point of each hospital in the state
  registries; a hospital without a state point takes the centroid of its
  campus, or is left out when it has none.
"""

from __future__ import annotations


def bundle_nodes(edges, hospital_nodes, nodes):
    """``{node_id: [lon, lat]}`` (string keys, numeric order)."""
    used = set()
    for rows in edges.values():
        for row in rows:
            used.update(row["edge"][:2])
    for by_hospital in hospital_nodes.values():
        for ids in by_hospital.values():
            used.update(ids)
    return {str(n): [nodes[n]["lon"], nodes[n]["lat"]] for n in sorted(used)}


def used_ways(edges, structures):
    """IDs of the ways behind usable edges and structure candidates."""
    used = {w for rows in edges.values() for row in rows for w in row["ways"]}
    used.update(s["way"] for rows in structures.values() for s in rows)
    return sorted(used)


def bundle_ways(used, ways):
    """``{way_id: [node_id, ...]}`` (string keys)."""
    return {str(w): ways[w]["nodes"] for w in used}


def way_names(used, ways):
    """``{way_id: name}`` for the named ways among ``used``."""
    return {
        str(w): ways[w]["tags"]["name"] for w in used if ways[w]["tags"].get("name")
    }


def basemap(extract):
    """``[{"edge", "geometry"}, ...]`` for edges with a stored geometry."""
    return [
        {"edge": row["edge"], "geometry": row["geometry"]}
        for row in extract
        if row["geometry"]
    ]


def hospital_locations(registry, campuses):
    """``{hospital: {"name", "lon", "lat"}}`` sorted by hospital.

    ``campuses`` maps hospitals to campus outlines in lon/lat.
    """
    out = {}
    for hospital, entry in registry.items():
        if entry["lon"] is not None:
            out[hospital] = entry
        elif hospital in campuses:
            centre = campuses[hospital].centroid
            out[hospital] = {"name": entry["name"], "lon": centre.x, "lat": centre.y}
    return dict(sorted(out.items()))
