import copy
import random

import pytest

from onebridge.evidence import (
    encode_changed,
    encode_results,
    strict_json,
    validate_changed,
    validate_records,
)
from onebridge.publish import publish_bytes
from onebridge.study.endpoints import adjacent_edges, split_ranking


def reachable(hid, seconds):
    return {"status": "reachable", "hospital_id": hid, "travel_time_seconds": seconds}


UNREACHABLE = {
    "status": "unreachable",
    "hospital_id": None,
    "travel_time_seconds": None,
}


def records():
    base = {"o-1": reachable("h-1", 60.0), "o-2": reachable("h-1", 120.0)}
    after = {
        "c-1": {"o-1": reachable("h-1", 120.0), "o-2": reachable("h-1", 120.0)},
        "c-2": {"o-1": reachable("h-1", 60.0), "o-2": UNREACHABLE},
    }
    out = []
    for cid in ("c-1", "c-2"):
        for oid in ("o-1", "o-2"):
            b, s = base[oid], after[cid][oid]
            both = (
                b["travel_time_seconds"] is not None
                and s["travel_time_seconds"] is not None
            )
            out.append(
                {
                    "closure_id": cid,
                    "origin_id": oid,
                    "baseline": b,
                    "scenario": s,
                    "finite_added_minutes": (
                        (s["travel_time_seconds"] - b["travel_time_seconds"]) / 60
                        if both
                        else None
                    ),
                    "newly_disconnected": b["status"] == "reachable"
                    and s["status"] == "unreachable",
                }
            )
    return out


def test_ranking_is_derived_from_records():
    ranking = validate_records(records(), ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"])
    assert [(r["closure_id"], r["rank"]) for r in ranking] == [("c-1", 1), ("c-2", 2)]
    assert ranking[0]["total_finite_added_minutes"] == 1.0
    assert ranking[1]["newly_disconnected_origins"] == 1


@pytest.mark.parametrize(
    "tamper",
    [
        lambda r: r[0].update(finite_added_minutes=2.0),
        lambda r: r[3].update(newly_disconnected=False),
        lambda r: r.pop(),
        lambda r: r.reverse(),
        lambda r: r[0]["scenario"].update(travel_time_seconds=30.0),
        lambda r: r[2]["baseline"].update(travel_time_seconds=61.0),
        lambda r: r[0]["scenario"].update(hospital_id="h-9"),
    ],
)
def test_inconsistent_records_are_rejected(tamper):
    bad = copy.deepcopy(records())
    tamper(bad)
    with pytest.raises(ValueError):
        validate_records(bad, ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"])


def test_encoding_refuses_a_ranking_that_does_not_follow():
    ranking = validate_records(records(), ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"])
    ranking[0]["total_finite_added_minutes"] = 5.0
    with pytest.raises(ValueError, match="does not follow"):
        encode_results(ranking, records(), ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"])


def test_strict_json_rejects_duplicates_and_nan():
    with pytest.raises(ValueError):
        strict_json('{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        strict_json('{"a": NaN}')


def test_publish_is_atomic_and_never_replaces(tmp_path):
    def reject(stage):
        raise ValueError("invalid")

    with pytest.raises(ValueError):
        publish_bytes(tmp_path / "out", {"a.txt": b"x"}, reject)
    assert list(tmp_path.iterdir()) == []
    published = publish_bytes(tmp_path / "out", {"a.txt": b"x"}, lambda stage: None)
    assert (published / "a.txt").read_bytes() == b"x"
    with pytest.raises(FileExistsError):
        publish_bytes(tmp_path / "out", {"a.txt": b"y"}, lambda stage: None)
    assert (published / "a.txt").read_bytes() == b"x"


def test_endpoint_closures_move_to_their_own_table():
    members = {
        "c-depart": [(1, 2, 0)],
        "c-arrive": [(8, 9, 0)],
        "c-next": [(2, 3, 0)],
        "c-far": [(5, 6, 0)],
    }
    ranking = [
        {
            "closure_id": c,
            "total_finite_added_minutes": t,
            "newly_disconnected_origins": 0,
        }
        for c, t in [
            ("c-depart", 9.0),
            ("c-arrive", 8.0),
            ("c-next", 3.0),
            ("c-far", 1.0),
        ]
    ]
    main, endpoint = split_ranking(ranking, members, {(1, 2, 0)}, {(8, 9, 0)})
    assert [r["closure_id"] for r in endpoint] == ["c-depart", "c-arrive"]
    assert [r["endpoint_class"] for r in endpoint] == ["ORIGIN_DEPARTURE", "ED_ARRIVAL"]
    assert [(r["closure_id"], r["rank"]) for r in main] == [("c-next", 1), ("c-far", 2)]
    assert main[0]["adjacent_to_endpoint"] and not main[1]["adjacent_to_endpoint"]
    assert main[0]["turn_detour_audit_required"] == ["TOP_10", "ADJACENT_TO_ENDPOINT"]
    assert adjacent_edges({(2, 3, 0), (7, 8, 0)}, {(1, 2, 0)}, {(8, 9, 0)}) == {
        (2, 3, 0),
        (7, 8, 0),
    }


def _changed(complete):
    return [r for r in complete if r["scenario"] != r["baseline"]]


def _baseline(complete):
    return {r["origin_id"]: r["baseline"] for r in complete}


def test_changed_only_records_give_the_same_ranking():
    full = records()
    expected = validate_records(full, ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"])
    changed = _changed(full)
    assert len(changed) == 2
    ranking = validate_changed(
        changed, _baseline(full), ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"]
    )
    assert ranking == expected
    encoded = encode_changed(
        ranking, changed, _baseline(full), ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"]
    )
    assert encoded["route_outcomes.jsonl"].count(b"\n") == 2
    assert encoded["baseline_outcomes.jsonl"].count(b"\n") == 2


@pytest.mark.parametrize("seed", range(25))
def test_changed_only_matches_complete_records_on_random_outcomes(seed):
    """Totals and means must be bit-identical, not just close."""
    rng = random.Random(seed)
    origins = [f"o-{i:02d}" for i in range(rng.choice([3, 8, 20]))]
    closures = [f"c-{i:02d}" for i in range(rng.choice([2, 6, 15]))]
    base = {
        o: UNREACHABLE if rng.random() < 0.2 else reachable("h-1", rng.uniform(1, 900))
        for o in origins
    }
    if all(b is UNREACHABLE for b in base.values()):
        base[origins[0]] = reachable("h-1", 10.0)
    full = []
    for cid in closures:
        for oid in origins:
            b = base[oid]
            s = b
            if b is not UNREACHABLE and rng.random() < 0.4:
                s = (
                    UNREACHABLE
                    if rng.random() < 0.2
                    else reachable(
                        rng.choice(["h-1", "h-2"]),
                        b["travel_time_seconds"]
                        + rng.choice([0.0, rng.uniform(0, 300)]),
                    )
                )
            both = b is not UNREACHABLE and s is not UNREACHABLE
            full.append(
                {
                    "closure_id": cid,
                    "origin_id": oid,
                    "baseline": b,
                    "scenario": s,
                    "finite_added_minutes": (
                        (s["travel_time_seconds"] - b["travel_time_seconds"]) / 60
                        if both
                        else None
                    ),
                    "newly_disconnected": b is not UNREACHABLE and s is UNREACHABLE,
                }
            )
    hospitals = ["h-1", "h-2"]
    expected = validate_records(full, closures, origins, hospitals)
    assert (
        validate_changed(_changed(full), base, closures, origins, hospitals) == expected
    )


@pytest.mark.parametrize(
    "tamper",
    [
        lambda r: r[0].update(finite_added_minutes=2.0),
        lambda r: r[1].update(newly_disconnected=False),
        lambda r: r.reverse(),
        lambda r: r.append(dict(r[0])),
        lambda r: r[0].update(scenario=r[0]["baseline"]),
        lambda r: r[0]["baseline"].update(travel_time_seconds=61.0),
        lambda r: r[0].update(origin_id="o-9"),
    ],
)
def test_inconsistent_changed_records_are_rejected(tamper):
    full = copy.deepcopy(records())
    bad = _changed(full)
    tamper(bad)
    with pytest.raises(ValueError):
        validate_changed(
            bad, _baseline(records()), ["c-1", "c-2"], ["o-1", "o-2"], ["h-1"]
        )
