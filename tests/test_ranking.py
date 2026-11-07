from copy import deepcopy
from itertools import permutations

import pytest

from onebridge.ranking import rank_closures


def rows_for(totals):
    return [
        {"closure_id": f"c{i}", "total_finite_added_minutes": total}
        for i, total in enumerate(totals)
    ]


@pytest.mark.parametrize(
    "totals, ranks",
    [
        ([24, 24, 18, 12], [1, 1, 3, 4]),
        ([10, 10, 10, 5], [1, 1, 1, 4]),
        ([0, 0, 0], [1, 1, 1]),
        ([], []),
    ],
)
def test_competition_ranks(totals, ranks):
    assert [r["rank"] for r in rank_closures(rows_for(totals))] == ranks


def test_disconnections_never_break_ties_and_input_is_not_mutated():
    rows = [
        {
            "closure_id": "b",
            "total_finite_added_minutes": 3,
            "newly_disconnected_origins": 9,
        },
        {
            "closure_id": "a",
            "total_finite_added_minutes": 3,
            "newly_disconnected_origins": 0,
        },
        {
            "closure_id": "c",
            "total_finite_added_minutes": 0,
            "newly_disconnected_origins": 100,
        },
    ]
    before = deepcopy(rows)
    expected = rank_closures(rows)
    assert [(r["closure_id"], r["rank"]) for r in expected] == [
        ("a", 1),
        ("b", 1),
        ("c", 3),
    ]
    for order in permutations(rows):
        assert rank_closures(order) == expected
    assert rows == before


def test_totals_are_not_rounded():
    ranked = rank_closures(
        [
            {"closure_id": "a", "total_finite_added_minutes": 1.0000001},
            {"closure_id": "b", "total_finite_added_minutes": 1.0000002},
        ]
    )
    assert [(r["closure_id"], r["rank"]) for r in ranked] == [("b", 1), ("a", 2)]


@pytest.mark.parametrize("value", [None, True, "1", -1, float("nan"), float("inf")])
def test_invalid_totals_are_rejected(value):
    with pytest.raises(ValueError):
        rank_closures([{"closure_id": "a", "total_finite_added_minutes": value}])


@pytest.mark.parametrize("cid", [None, "", "A", " a", "é", "a/b"])
def test_noncanonical_ids_are_rejected(cid):
    with pytest.raises(ValueError):
        rank_closures([{"closure_id": cid, "total_finite_added_minutes": 0}])


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        rank_closures([{"closure_id": "a", "total_finite_added_minutes": 1}] * 2)
