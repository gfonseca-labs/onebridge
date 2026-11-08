"""Turn study runs into the published evidence directory.

``build_report`` writes, under its output directory:

* ``tables/``: the main and endpoint tables for every run;
* ``comparison.json``: each sensitivity compared with the primary run;
* ``evidence/``: ``ranking.csv``, ``route_outcomes.jsonl`` (every closure and
  origin pair), ``endpoint_convention.csv``, ``map.png`` and ``evidence.json``,
  published atomically after validation.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

from shapely import from_wkt

from ..evidence import (
    CSV_COLUMNS,
    SCHEMA_VERSION,
    capture_run,
    encode_csv,
    encode_manifest,
    encode_results,
    strict_json,
    validate_records,
)
from ..publish import publish_bytes
from .bundle import to_utm
from .compare import PRIMARY, compare_runs
from .endpoints import endpoint_edges, split_ranking

ENDPOINT_COLUMNS = (
    "rank",
    "closure_id",
    "endpoint_class",
    "total_finite_added_minutes",
    "finite_comparable_origins",
    "newly_disconnected_origins",
    "baseline_reachable_origins",
)
SECTIONS = {"schema_version", "run", "inputs", "methodology", "closures", "validation"}
NYC_CONFIGURATION = {
    "buffer_m": 10000,
    "grid_m": 500,
    "speeds": "baseline",
    "routing": "turn_aware",
    "endpoint_options": "campus_frontage_and_reverse_departures",
    "closure_scope": "route_screened",
}
NYC_LIMITATIONS = [
    "Modeled road travel to a public road approach at each emergency department; "
    "not door-to-treatment, ambulance response or clinical arrival time.",
    "Static research speeds; no live traffic, signals or emergency-vehicle privileges.",
    "OpenStreetMap access and turn restrictions are modeling assumptions, not legal or "
    "field verification. Unmapped rules, such as NYC business-district U-turn bans, "
    "appear only in a sensitivity run.",
    "Origins are an equal-weight 500 m grid, not population; Census weighting is a "
    "sensitivity only.",
    "Candidates without a verified current ED or public approach (Metropolitan, Heights, "
    "Palisades, Hoboken, Cobble Hill) are excluded and can bias nearby results.",
    "Where no ED entrance is published, the approach is the hospital street address "
    "(Bellevue, Woodhull, Wyckoff Heights, Elmhurst).",
    "Reversible Lincoln Tunnel lanes and a few interstate or access-restricted crossing "
    "movements are excluded from the graph.",
    "Consecutive segments with identical effects (for example 12th Avenue) are one "
    "corridor effect, not independent findings.",
    "Meadowlands Parkway depends on the excluded Hoboken hospital and on New Jersey "
    "speed assumptions.",
]


def report_context(bundle):
    """Keep invented demonstrations separate from the NYC research model."""
    study = bundle.study()
    if study["kind"] == "synthetic":
        return {
            "dataset": "synthetic-demo",
            "configuration": {
                "network": "invented_3_by_2_street_grid",
                "origins": "two_fixed_origins_reused_in_each_configuration",
                "travel_times": "30_seconds_per_street_in_each_direction",
                "routing": "turn_aware",
                "closure_scope": "route_screened",
            },
            "limitations": [
                "Invented roads, origins and hospital; no real access or NYC finding.",
                "The configuration names exercise the study pipeline; the demo does "
                "not construct 500 m or 250 m origin grids or geographic buffers.",
                "All speed configurations reuse the same synthetic travel times.",
                "The population-weighted comparison uses invented weights.",
            ],
        }
    configuration = dict(NYC_CONFIGURATION)
    configuration["buffer_m"] = study["buffers_m"]["primary"]
    return {
        "dataset": "nyc",
        "configuration": configuration,
        "limitations": NYC_LIMITATIONS,
    }


def build_report(bundle, run_dir, out, log=print):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    run_dir = Path(run_dir)
    tables = out / "tables"
    tables.mkdir()
    members = {}
    for buffer in ("primary", "wide"):
        path = run_dir / f"{buffer}-scenario-members.jsonl"
        if not path.exists():
            continue
        members[buffer] = {
            r["closure_id"]: [tuple(e) for e in r["movements"]] for r in _lines(path)
        }
        departures, arrivals = endpoint_edges(bundle, buffer)
        for spacing, departing in departures.items():
            for ranking_path in sorted(
                run_dir.glob(f"{buffer}-{spacing}-*-ranking.jsonl")
            ):
                name = ranking_path.name.removesuffix("-ranking.jsonl")
                main, endpoint = split_ranking(
                    _lines(ranking_path), members[buffer], departing, arrivals
                )
                _write_lines(tables / f"{name}-main-ranking.jsonl", main)
                _write_lines(tables / f"{name}-endpoint-convention.jsonl", endpoint)
                log(name, "main", len(main), "endpoint", len(endpoint))

    population = bundle.origins()["population_2020"]["500"]
    comparison = compare_runs(run_dir, tables, population)
    if bundle.study()["kind"] == "synthetic":
        for name, result in comparison.items():
            result["label"] = (
                "Invented population weights"
                if name == "census-weighted"
                else f"Synthetic configuration: {name}"
            )
    (out / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    published = build_evidence(
        bundle, run_dir, tables, members["primary"], comparison, out / "evidence"
    )
    log("published", published)
    return published


def build_evidence(bundle, run_dir, tables, members, comparison, destination):
    """Assemble, validate and publish the primary run's evidence directory."""
    baseline = {
        r["origin_id"]: r for r in _lines(run_dir / f"{PRIMARY}-baseline-routes.jsonl")
    }
    main = _lines(tables / f"{PRIMARY}-main-ranking.jsonl")
    endpoint = _lines(tables / f"{PRIMARY}-endpoint-convention.jsonl")
    closure_ids = [r["closure_id"] for r in main]
    in_main = set(closure_ids)
    affected = [
        r
        for r in _lines(run_dir / f"{PRIMARY}-affected-outcomes.jsonl")
        if r["closure_id"] in in_main
    ]
    records = complete_records(closure_ids, list(baseline), baseline, affected)
    hospital_ids = sorted(
        {r["hospital_id"] for r in baseline.values() if r["hospital_id"]}
        | {
            r["scenario"]["hospital_id"]
            for r in affected
            if r["scenario"]["hospital_id"]
        }
    )
    ranking = validate_records(records, closure_ids, list(baseline), hospital_ids)
    split_rows = {r["closure_id"]: {k: r[k] for k in CSV_COLUMNS} for r in main}
    if any(
        split_rows[r["closure_id"]] != {k: r[k] for k in CSV_COLUMNS} for r in ranking
    ):
        raise ValueError(
            "ranking from the outcome records disagrees with the main table"
        )

    closures, geometry = describe_closures(bundle, members, main)
    hospitals = bundle.hospitals()
    origin_xy = bundle.origins()["coordinates"]["500"]
    context = report_context(bundle)
    inputs = {
        "dataset": context["dataset"],
        "origins": {
            o: {"latitude": origin_xy[o][1], "longitude": origin_xy[o][0]}
            for o in sorted(baseline)
        },
        "hospitals": {h: hospitals[h]["name"] for h in hospital_ids},
        "configuration": context["configuration"],
    }
    project = to_utm()
    with tempfile.TemporaryDirectory(prefix="onebridge-map-") as tmp:
        image = Path(tmp) / "map.png"
        draw_map(
            image,
            bundle,
            [geometry[r["closure_id"]] for r in ranking[:10]],
            {
                h: (
                    *project(hospitals[h]["lon"], hospitals[h]["lat"]),
                    hospitals[h]["name"],
                )
                for h in hospital_ids
            },
            [
                project(v["longitude"], v["latitude"])
                for v in inputs["origins"].values()
            ],
        )
        map_bytes = image.read_bytes()

    encoded = encode_results(
        ranking, records, closure_ids, list(baseline), hospital_ids
    )
    endpoint_bytes = encode_csv(endpoint, ENDPOINT_COLUMNS)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run": capture_run(),
        "inputs": inputs,
        "methodology": {
            "ranking": "unrounded sum of finite added minutes",
            "tie_ranks": "competition",
            "origin_weighting": "equal per retained origin",
            "disconnections": "counted separately; never break ties",
            "endpoint_convention_table": "closures of an origin departure or ED arrival "
            "road are listed separately in endpoint_convention.csv",
            "scope": "single closures of route-screened road segments, local bridge and "
            "tunnel structures, and river crossing corridors",
            "sensitivity_triggers": {
                name: r["triggers"] for name, r in comparison.items() if "triggers" in r
            },
            "limitations": context["limitations"],
        },
        "closures": closures,
        "validation": {
            "route_outcomes": encoded["reference"],
            "map.png": hashlib.sha256(map_bytes).hexdigest(),
            "endpoint_convention.csv": hashlib.sha256(endpoint_bytes).hexdigest(),
        },
    }
    files = {
        "ranking.csv": encoded["ranking.csv"],
        "route_outcomes.jsonl": encoded["route_outcomes.jsonl"],
        "endpoint_convention.csv": endpoint_bytes,
        "evidence.json": encode_manifest(manifest),
        "map.png": map_bytes,
    }
    return publish_bytes(destination, files, validator(records, closures, inputs))


def describe_closures(bundle, members, main):
    """Closure metadata for evidence.json, plus each closure's map geometry."""
    ordinary = {
        r["closure_id"]: r
        for r in bundle.ordinary("primary")
        if r["closure_id"] in members
    }
    crossings = {"crossing-" + c["corridor_id"]: c for c in bundle.crossings("primary")}
    edge_ways = {tuple(r["edge"]): r["ways"] for r in bundle.edges("primary")}
    way_names = bundle.way_names()
    shapes = {tuple(r["edge"]): r["geometry"] for r in bundle.basemap("primary")}
    flags = {r["closure_id"]: r for r in main}
    project = to_utm()
    closures, geometry = {}, {}
    for cid in sorted(flags):
        edges = members[cid]
        if cid in ordinary:
            info = {
                "type": "ordinary_source_segment",
                "names": ordinary[cid]["names"],
                "source_osm_way_ids": ordinary[cid]["ways"],
            }
        elif cid in crossings:
            c = crossings[cid]
            info = {
                "type": "crossing_corridor",
                "names": [c["corridor_id"]],
                "source_osm_way_ids": c["ways"],
            }
        else:
            ways = sorted({w for e in edges for w in edge_ways[e]})
            names = sorted({way_names.get(w, "") for w in ways} - {""})
            info = {
                "type": "structure_corridor",
                "names": [" / ".join(names) or cid],
                "source_osm_way_ids": ways,
            }
        closures[cid] = dict(
            info,
            original_movements=[list(e) for e in edges],
            adjacent_to_endpoint=flags[cid]["adjacent_to_endpoint"],
            turn_detour_audit_required=flags[cid]["turn_detour_audit_required"],
            field_verified=False,
        )
        geometry[cid] = [
            [project(*xy) for xy in from_wkt(shapes[e]).coords]
            for e in edges
            if e in shapes
        ]
    return closures, geometry


def complete_records(closure_ids, origin_ids, baseline, affected):
    """One record per (closure, origin); unaffected origins keep their baseline."""
    by_pair = {(r["closure_id"], r["origin_id"]): r for r in affected}
    if len(by_pair) != len(affected):
        raise ValueError("duplicate affected closure-origin records")
    allowed_closures, allowed_origins = set(closure_ids), set(origin_ids)
    if any(c not in allowed_closures or o not in allowed_origins for c, o in by_pair):
        raise ValueError("affected record names an unknown closure or origin")
    fields = (
        "closure_id",
        "origin_id",
        "baseline",
        "scenario",
        "finite_added_minutes",
        "newly_disconnected",
    )
    records = []
    for cid in sorted(closure_ids):
        for oid in sorted(origin_ids):
            before = {
                k: baseline[oid][k]
                for k in ("status", "hospital_id", "travel_time_seconds")
            }
            record = by_pair.get((cid, oid))
            if record is not None:
                if record["baseline"] != before:
                    raise ValueError(f"baseline of {oid} differs in {cid}")
                records.append({k: record[k] for k in fields})
                continue
            records.append(
                {
                    "closure_id": cid,
                    "origin_id": oid,
                    "baseline": before,
                    "scenario": dict(before),
                    "finite_added_minutes": 0.0
                    if before["status"] == "reachable"
                    else None,
                    "newly_disconnected": False,
                }
            )
    return records


def validator(expected_records, expected_closures, expected_inputs):
    """Re-check the staged directory before it is published."""

    def validate(stage):
        manifest_bytes = (stage / "evidence.json").read_bytes()
        manifest = strict_json(manifest_bytes)
        if set(manifest) != SECTIONS or manifest["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unexpected evidence sections or schema version")
        if encode_manifest(manifest) != manifest_bytes:
            raise ValueError("evidence.json is not canonically encoded")
        if not re.fullmatch(r"run-[0-9a-f]{32}", manifest["run"]["run_id"]):
            raise ValueError("invalid run ID")
        datetime.strptime(manifest["run"]["started_at_utc"], "%Y-%m-%dT%H:%M:%SZ")
        outcomes = (stage / "route_outcomes.jsonl").read_bytes()
        records = [strict_json(line) for line in outcomes.splitlines()]
        closure_ids = list(manifest["closures"])
        origin_ids = list(manifest["inputs"]["origins"])
        hospital_ids = list(manifest["inputs"]["hospitals"])
        ranking = validate_records(records, closure_ids, origin_ids, hospital_ids)
        encoded = encode_results(
            ranking, records, closure_ids, origin_ids, hospital_ids
        )
        if (
            encoded["route_outcomes.jsonl"] != outcomes
            or encoded["ranking.csv"] != (stage / "ranking.csv").read_bytes()
            or encoded["reference"] != manifest["validation"]["route_outcomes"]
        ):
            raise ValueError("ranking and outcome files do not reconcile")
        for name in ("map.png", "endpoint_convention.csv"):
            digest = hashlib.sha256((stage / name).read_bytes()).hexdigest()
            if digest != manifest["validation"][name]:
                raise ValueError(f"{name} does not match its recorded hash")
        if not (stage / "map.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("map.png is not a PNG")
        if (
            records != expected_records
            or manifest["closures"] != expected_closures
            or manifest["inputs"] != expected_inputs
        ):
            raise ValueError("evidence differs from the computed results")

    return validate


MAP_LABELS = {
    "title": "OneBridge NYC study: top-10 main-ranking single closures (red)\n"
    "turn-aware, 10 km buffer, 500 m origins, baseline speeds - research model, "
    "not field-verified",
    "credit": "Map data: OpenStreetMap contributors (ODbL), 2026-10-24 snapshot.",
    "margin_m": 1500,
    "origin_label": "Retained origins (500 m)",
    "hospital_heading": "Selected emergency departments",
}


def draw_map(path, bundle, top_geometry, hospitals_xy, origins_xy):
    """Top-10 closures in red over the road network, with origins and EDs.

    Title, credit and margin come from :data:`MAP_LABELS`, overridden by the
    bundle's optional ``map.json``.
    """
    labels = MAP_LABELS | bundle.map_labels()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.font_manager import FontProperties
    from matplotlib.transforms import Bbox

    project = to_utm()
    points = [xy for lines in top_geometry for coords in lines for xy in coords]
    points += [(x, y) for x, y, _ in hospitals_xy.values()] + list(origins_xy)
    xs, ys = zip(*points, strict=True)
    m = labels["margin_m"]
    x0, x1, y0, y1 = min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m
    roads = []
    for row in bundle.basemap("primary"):
        coords = [project(*c) for c in from_wkt(row["geometry"]).coords]
        if any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in coords):
            roads.append(coords)
    fig = plt.figure(figsize=(12, 8), dpi=150, layout="constrained")
    grid = fig.add_gridspec(1, 2, width_ratios=(3, 1.25))
    ax = fig.add_subplot(grid[0, 0])
    key = fig.add_subplot(grid[0, 1])
    key.set_axis_off()
    ax.add_collection(LineCollection(roads, colors="#c8c8c8", linewidths=0.3))
    top = [coords for lines in top_geometry for coords in lines]
    ax.add_collection(LineCollection(top, colors="#c0392b", linewidths=2.6))
    ox, oy = zip(*origins_xy, strict=True)
    ax.scatter(ox, oy, s=6, c="#2c7fb8", label=labels["origin_label"])
    names = []
    for number, hospital in enumerate(sorted(hospitals_xy), 1):
        x, y, name = hospitals_xy[hospital]
        names.append(
            textwrap.fill(f"{number}. {name}", width=35, subsequent_indent="   ")
        )
        ax.scatter([x], [y], marker="P", s=60, c="black")
    key.set_title(labels["hospital_heading"], loc="left", fontsize=9)
    key.text(
        0, 0.98, "\n\n".join(names), va="top", fontsize=7.5, transform=key.transAxes
    )
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    fig.suptitle(labels["title"], fontsize=10)
    ax.legend(loc="lower right", fontsize=6)
    fig.supxlabel(labels["credit"], fontsize=7)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    occupied = []
    offsets = [(4, 4), (4, -12), (-15, 4), (-15, -12)]
    offsets += [
        (dx, dy)
        for radius in (20, 35, 50)
        for dx, dy in (
            (radius, radius),
            (-radius, radius),
            (radius, -radius),
            (-radius, -radius),
        )
    ]
    pixel_per_point = fig.dpi / 72
    for number, hospital in enumerate(sorted(hospitals_xy), 1):
        x, y, _ = hospitals_xy[hospital]
        px, py = ax.transData.transform((x, y))
        width, height, _ = renderer.get_text_width_height_descent(
            str(number), FontProperties(size=7), False
        )
        for dx, dy in offsets:
            left, bottom = px + dx * pixel_per_point, py + dy * pixel_per_point
            box = Bbox.from_bounds(left - 2, bottom - 2, width + 4, height + 4)
            if (
                ax.bbox.contains(*box.p0)
                and ax.bbox.contains(*box.p1)
                and not any(box.overlaps(other) for other in occupied)
            ):
                break
        else:
            raise ValueError("cannot place hospital map labels without overlap")
        occupied.append(box)
        label = ax.annotate(
            str(number),
            (x, y),
            fontsize=7,
            xytext=(dx, dy),
            textcoords="offset points",
            ha="left",
            va="bottom",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 0.4},
        )
        label.set_in_layout(False)
    fig.savefig(path, bbox_inches="tight", metadata={"Software": "OneBridge"})
    plt.close(fig)


def _lines(path):
    return [json.loads(line) for line in Path(path).open()]


def _write_lines(path, records):
    with Path(path).open("x") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")
