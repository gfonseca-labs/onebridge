"""Compare each sensitivity run with the primary run.

For every one-at-a-time sensitivity this reports hospital choices, baseline
times, the top-10 scenarios, the leading tie group and disconnections, and
flags a trigger when a baseline time or top-10 total moves by more than 5%.
Triggers call for an explanation; they do not accept or reject a result.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

PRIMARY = "primary-500-baseline"
SENSITIVITIES = {
    "wide-500-baseline": "20 km buffer",
    "primary-250-baseline": "250 m origin grid",
    "primary-500-outer35": "outer-urban 35 mph",
    "primary-500-special_slow": "special roads slow",
    "primary-500-special_fast": "special roads fast",
    "primary-500-baseline-conditional": "time-limited turn rules in force (500 m)",
    "primary-250-baseline-conditional": "time-limited turn rules in force (250 m)",
    "primary-500-baseline-uturn-ban": "U-turns banned at NYC nodes (500 m)",
    "primary-250-baseline-uturn-ban": "U-turns banned at NYC nodes (250 m)",
}
TRIGGER = 0.05


def compare_runs(run_dir, tables_dir, population=None):
    """Return the comparison report as a dict.

    ``tables_dir`` holds the ``*-main-ranking.jsonl`` tables from the endpoint
    split. ``population`` maps 500 m origin IDs to residents; when given, the
    primary main table is also ranked by population-weighted delay.
    """
    run_dir, tables_dir = Path(run_dir), Path(tables_dir)
    primary = _routes(run_dir, PRIMARY)
    primary_main = _lines(tables_dir / f"{PRIMARY}-main-ranking.jsonl")
    primary_reached = [r for r in primary.values() if r["status"] == "reachable"]
    report = {}
    for name, label in SENSITIVITIES.items():
        routes = _routes(run_dir, name)
        main = _lines(tables_dir / f"{name}-main-ranking.jsonl")
        same_origins = set(routes) == set(primary)
        reached = [r for r in routes.values() if r["status"] == "reachable"]
        triggers = []
        result = {
            "label": label,
            "same_origin_population": same_origins,
            "baseline_reachable": [len(primary_reached), len(reached)],
        }
        if same_origins:
            changed = [
                o
                for o in primary
                if primary[o]["hospital_id"] != routes[o]["hospital_id"]
            ]
            time_change = max(
                (
                    _relative(
                        routes[o]["travel_time_seconds"],
                        primary[o]["travel_time_seconds"],
                    )
                    for o in primary
                    if primary[o]["status"] == routes[o]["status"] == "reachable"
                    and primary[o]["travel_time_seconds"] > 0
                ),
                default=0.0,
            )
            result["hospital_choice_changes"] = len(changed)
            result["max_relative_baseline_time_change"] = time_change
            if changed:
                triggers.append(f"{len(changed)} origins choose a different ED")
            if time_change > TRIGGER:
                triggers.append(f"baseline time changes up to {time_change:.1%}")
        else:
            result["mean_baseline_minutes"] = [
                _mean_seconds(primary_reached) / 60,
                _mean_seconds(reached) / 60,
            ]
        top_primary = [r["closure_id"] for r in primary_main[:10]]
        top = [r["closure_id"] for r in main[:10]]
        result["top10_overlap"] = len(set(top_primary) & set(top))
        result["top10_same_order"] = top_primary == top
        result["leading_group_same"] = _leading(primary_main) == _leading(main)
        if not result["leading_group_same"]:
            triggers.append("leading scenario or tie group changed")
        by_id_primary = {r["closure_id"]: r for r in primary_main}
        by_id = {r["closure_id"]: r for r in main}
        reclassified = sorted(
            c
            for c in by_id_primary
            if c in by_id
            and (by_id_primary[c]["newly_disconnected_origins"] > 0)
            != (by_id[c]["newly_disconnected_origins"] > 0)
        )
        result["disconnection_classification_changes"] = len(reclassified)
        if reclassified:
            triggers.append(
                f"{len(reclassified)} scenarios change disconnection classification"
            )
        if same_origins:
            worst = max(
                (
                    _relative(
                        by_id[c]["total_finite_added_minutes"],
                        by_id_primary[c]["total_finite_added_minutes"],
                    )
                    for c in top_primary
                    if c in by_id
                ),
                default=0.0,
            )
            result["max_relative_top10_total_change"] = worst
            if worst > TRIGGER:
                triggers.append(f"top-10 total changes up to {worst:.1%}")
        else:
            result["top10_mean_finite_minutes"] = [
                [r["mean_finite_added_minutes"] for r in primary_main[:10]],
                [r["mean_finite_added_minutes"] for r in main[:10]],
            ]
        result["triggers"] = triggers
        report[name] = result
    if population is not None:
        report["census-weighted"] = population_weighted(
            run_dir, primary_main, population
        )
    return report


def population_weighted(run_dir, primary_main, population):
    """Rank primary main-table scenarios by person-minutes of added delay."""
    ids = {r["closure_id"] for r in primary_main}
    minutes = dict.fromkeys(ids, 0.0)
    lost = dict.fromkeys(ids, 0)
    for r in _lines(Path(run_dir) / f"{PRIMARY}-affected-outcomes.jsonl"):
        if r["closure_id"] not in ids:
            continue
        people = population[r["origin_id"]]
        if r["newly_disconnected"]:
            lost[r["closure_id"]] += people
        elif r["finite_added_minutes"]:
            minutes[r["closure_id"]] += people * r["finite_added_minutes"]
    # As in the main table, disconnections never break ties.
    order = sorted(ids, key=lambda c: (-minutes[c], c))
    equal = [r["closure_id"] for r in primary_main[:10]]
    return {
        "label": "Census 2020 population weights (500 m)",
        "top10": [
            {
                "closure_id": c,
                "people_newly_disconnected": lost[c],
                "person_minutes_added": minutes[c],
            }
            for c in order[:10]
        ],
        "top10_overlap_with_equal_weights": len(set(order[:10]) & set(equal)),
        "leading_same_as_equal_weights": order[0] == equal[0],
    }


def _lines(path):
    return [json.loads(line) for line in Path(path).open()]


def _routes(run_dir, name):
    return {
        r["origin_id"]: r for r in _lines(run_dir / f"{name}-baseline-routes.jsonl")
    }


def _leading(main):
    top = main[0]["total_finite_added_minutes"]
    return {r["closure_id"] for r in main if r["total_finite_added_minutes"] == top}


def _relative(a, b):
    if a == b:
        return 0.0
    return math.inf if b == 0 else abs(a - b) / abs(b)


def _mean_seconds(routes):
    return sum(r["travel_time_seconds"] for r in routes) / len(routes)
