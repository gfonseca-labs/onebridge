import copy

import pytest

from onebridge.evidence import encode_results, strict_json, validate_records
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
