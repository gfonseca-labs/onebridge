"""Evidence records: validation and deterministic encoding.

The NYC outcome file holds one record per (closure, origin) pair; a place
study's lists only the pairs a closure changes, next to one baseline outcome
per origin. Either way the ranking is recomputed from those records, so a
ranking that does not follow from its outcomes cannot be written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import platform
from datetime import UTC, datetime
from importlib import metadata
from uuid import uuid4

from .ranking import rank_closures, require_id, require_number

SCHEMA_VERSION = "1.1.0"
CSV_COLUMNS = (
    "rank",
    "closure_id",
    "total_finite_added_minutes",
    "mean_finite_added_minutes",
    "finite_comparable_origins",
    "baseline_reachable_origins",
    "baseline_unreachable_origins",
    "newly_disconnected_origins",
    "newly_disconnected_share",
    "retained_origins",
)
RECORD_FIELDS = {
    "closure_id",
    "origin_id",
    "baseline",
    "scenario",
    "finite_added_minutes",
    "newly_disconnected",
}
OUTCOME_FIELDS = {"status", "hospital_id", "travel_time_seconds"}


def capture_run() -> dict:
    """Run ID, start time and software versions for the evidence file."""
    return {
        "run_id": "run-" + uuid4().hex,
        "started_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "onebridge_version": metadata.version("blackwood-onebridge"),
        "python_version": platform.python_version(),
    }


def validate_records(records, closure_ids, origin_ids, hospital_ids) -> list[dict]:
    """Check complete, ordered records and return the ranking they imply."""
    closure_ids = _unique_ids(closure_ids, "closure_id")
    origin_ids = _unique_ids(origin_ids, "origin_id")
    hospitals = set(_unique_ids(hospital_ids, "hospital_id"))
    expected = [(cid, oid) for cid in closure_ids for oid in origin_ids]
    if len(records) != len(expected):
        raise ValueError("wrong number of closure-origin records")
    baseline = {}
    deltas = {cid: [] for cid in closure_ids}
    disconnected = dict.fromkeys(closure_ids, 0)
    for record, pair in zip(records, expected, strict=True):
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
            raise ValueError("record has the wrong fields")
        if (record["closure_id"], record["origin_id"]) != pair:
            raise ValueError("records are missing, duplicated or out of order")
        cid, oid = pair
        before = _outcome(record["baseline"], hospitals)
        after = _outcome(record["scenario"], hospitals)
        if baseline.setdefault(oid, record["baseline"]) != record["baseline"]:
            raise ValueError(f"baseline of {oid} differs between closures")
        if (before is None and after is not None) or (
            before is not None and after is not None and after < before
        ):
            raise ValueError("a closure made a route faster")
        lost = before is not None and after is None
        if (
            type(record["newly_disconnected"]) is not bool
            or record["newly_disconnected"] != lost
        ):
            raise ValueError("newly_disconnected contradicts the outcomes")
        delta = record["finite_added_minutes"]
        if before is not None and after is not None:
            require_number(delta, "finite_added_minutes")
            if delta != (after - before) / 60:
                raise ValueError("finite_added_minutes does not match the times")
            deltas[cid].append(delta)
        elif delta is not None:
            raise ValueError("finite_added_minutes must be null without both times")
        disconnected[cid] += lost
    reachable = sum(o["status"] == "reachable" for o in baseline.values())
    if not reachable:
        raise ValueError("no origin reaches a hospital")
    rows = []
    for cid in closure_ids:
        values = deltas[cid]
        total = require_number(math.fsum(values), "total_finite_added_minutes")
        rows.append(
            {
                "closure_id": cid,
                "total_finite_added_minutes": total,
                "mean_finite_added_minutes": total / len(values) if values else None,
                "finite_comparable_origins": len(values),
                "baseline_reachable_origins": reachable,
                "baseline_unreachable_origins": len(origin_ids) - reachable,
                "newly_disconnected_origins": disconnected[cid],
                "newly_disconnected_share": disconnected[cid] / reachable,
                "retained_origins": len(origin_ids),
            }
        )
    return rank_closures(rows)


def validate_changed(
    records, baseline, closure_ids, origin_ids, hospital_ids
) -> list[dict]:
    """Check changed-only records and return the ranking they imply.

    Place studies are too large for one record per (closure, origin) pair, so
    ``records`` lists only the pairs whose outcome differs from ``baseline``
    (``{origin_id: outcome}``), in closure then origin order. A missing pair
    keeps its baseline outcome and adds zero minutes. ``math.fsum`` is exact,
    so the totals equal those of the complete record set.
    """
    closure_ids = _unique_ids(closure_ids, "closure_id")
    origin_ids = _unique_ids(origin_ids, "origin_id")
    hospitals = set(_unique_ids(hospital_ids, "hospital_id"))
    if set(baseline) != set(origin_ids):
        raise ValueError("baseline outcomes do not match the origins")
    times = {oid: _outcome(baseline[oid], hospitals) for oid in origin_ids}
    reachable = sum(t is not None for t in times.values())
    if not reachable:
        raise ValueError("no origin reaches a hospital")
    closure_at = {cid: i for i, cid in enumerate(closure_ids)}
    origin_at = {oid: i for i, oid in enumerate(origin_ids)}
    deltas = {cid: [] for cid in closure_ids}
    comparable = dict.fromkeys(closure_ids, reachable)
    disconnected = dict.fromkeys(closure_ids, 0)
    previous = None
    for record in records:
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
            raise ValueError("record has the wrong fields")
        cid, oid = record["closure_id"], record["origin_id"]
        if cid not in closure_at or oid not in origin_at:
            raise ValueError("record names an unknown closure or origin")
        position = (closure_at[cid], origin_at[oid])
        if previous is not None and position <= previous:
            raise ValueError("records are duplicated or out of order")
        previous = position
        if record["baseline"] != baseline[oid]:
            raise ValueError(f"baseline of {oid} differs in {cid}")
        if record["scenario"] == record["baseline"]:
            raise ValueError("an unchanged outcome is listed as changed")
        before = times[oid]
        after = _outcome(record["scenario"], hospitals)
        if (before is None and after is not None) or (
            before is not None and after is not None and after < before
        ):
            raise ValueError("a closure made a route faster")
        lost = before is not None and after is None
        if (
            type(record["newly_disconnected"]) is not bool
            or record["newly_disconnected"] != lost
        ):
            raise ValueError("newly_disconnected contradicts the outcomes")
        delta = record["finite_added_minutes"]
        if before is not None and after is not None:
            require_number(delta, "finite_added_minutes")
            if delta != (after - before) / 60:
                raise ValueError("finite_added_minutes does not match the times")
            deltas[cid].append(delta)
        elif delta is not None:
            raise ValueError("finite_added_minutes must be null without both times")
        comparable[cid] -= lost
        disconnected[cid] += lost
    rows = []
    for cid in closure_ids:
        total = require_number(math.fsum(deltas[cid]), "total_finite_added_minutes")
        count = comparable[cid]
        rows.append(
            {
                "closure_id": cid,
                "total_finite_added_minutes": total,
                "mean_finite_added_minutes": total / count if count else None,
                "finite_comparable_origins": count,
                "baseline_reachable_origins": reachable,
                "baseline_unreachable_origins": len(origin_ids) - reachable,
                "newly_disconnected_origins": disconnected[cid],
                "newly_disconnected_share": disconnected[cid] / reachable,
                "retained_origins": len(origin_ids),
            }
        )
    return rank_closures(rows)


def encode_changed(ranking, records, baseline, closure_ids, origin_ids, hospital_ids):
    """Validate changed-only records, then encode the ranking and both files."""
    if (
        validate_changed(records, baseline, closure_ids, origin_ids, hospital_ids)
        != ranking
    ):
        raise ValueError("ranking does not follow from the outcome records")
    outcomes = encode_jsonl(records)
    baselines = encode_jsonl(
        {"origin_id": oid, "baseline": baseline[oid]} for oid in sorted(baseline)
    )
    return {
        "ranking.csv": encode_csv(ranking),
        "route_outcomes.jsonl": outcomes,
        "baseline_outcomes.jsonl": baselines,
        "reference": {
            "path": "route_outcomes.jsonl",
            "record_count": len(records),
            "records": "changed outcomes only; a missing (closure, origin) pair "
            "keeps its outcome in baseline_outcomes.jsonl",
            "sha256": hashlib.sha256(outcomes).hexdigest(),
            "baseline_sha256": hashlib.sha256(baselines).hexdigest(),
        },
    }


def encode_jsonl(records) -> bytes:
    return "".join(
        json.dumps(
            r, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )
        + "\n"
        for r in records
    ).encode("utf-8")


def encode_manifest(manifest) -> bytes:
    text = json.dumps(
        manifest, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
    )
    return (text + "\n").encode("utf-8")


def encode_csv(rows, columns=CSV_COLUMNS) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=columns, lineterminator="\n", extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def encode_results(ranking, records, closure_ids, origin_ids, hospital_ids) -> dict:
    """Validate the records, then encode the ranking CSV and outcome file."""
    if validate_records(records, closure_ids, origin_ids, hospital_ids) != ranking:
        raise ValueError("ranking does not follow from the outcome records")
    outcomes = encode_jsonl(records)
    return {
        "ranking.csv": encode_csv(ranking),
        "route_outcomes.jsonl": outcomes,
        "reference": {
            "path": "route_outcomes.jsonl",
            "record_count": len(records),
            "sha256": hashlib.sha256(outcomes).hexdigest(),
        },
    }


def strict_json(content):
    """Parse JSON, rejecting duplicate keys and NaN or infinite values."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON value {value}")

    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


def _outcome(value, hospital_ids):
    if not isinstance(value, dict) or set(value) != OUTCOME_FIELDS:
        raise ValueError("outcome has the wrong fields")
    if value["status"] == "unreachable":
        if value["hospital_id"] is not None or value["travel_time_seconds"] is not None:
            raise ValueError("unreachable outcome must have no hospital or time")
        return None
    if value["status"] != "reachable":
        raise ValueError(f"unknown route status {value['status']!r}")
    if require_id(value["hospital_id"], "hospital_id") not in hospital_ids:
        raise ValueError(f"unknown hospital {value['hospital_id']}")
    return require_number(value["travel_time_seconds"], "travel_time_seconds")


def _unique_ids(ids, label):
    values = list(ids)
    for value in values:
        require_id(value, label)
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{label} list is empty or has duplicates")
    return sorted(values)
