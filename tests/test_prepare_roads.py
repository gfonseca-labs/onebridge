from pathlib import Path

import pytest
from shapely.geometry import box

from onebridge.prepare.roads import (
    MPH,
    fallback_mph,
    load_speeds,
    posted_kph,
    usable_edges,
)
from onebridge.study.bundle import to_utm

CONFIG = Path(__file__).parents[1] / "config" / "nyc.toml"
SPEEDS = load_speeds(CONFIG)
NODES = {1: [-73.99, 40.75], 2: [-73.98, 40.75], 3: [-74.10, 40.75], 4: [-74.09, 40.75]}


def nyc():
    # A box around nodes 1 and 2 only, in UTM metres.
    (x0, y0), (x1, y1) = (to_utm()(-73.995, 40.74), to_utm()(-73.975, 40.76))
    return box(x0, y0, x1, y1)


def road(u, v, highway, maxspeed=None, length=100.0):
    return {
        "edge": [u, v, 0],
        "highway": highway,
        "maxspeed": maxspeed,
        "length": length,
        "geometry": None,
        "ways": [u * 10 + v],
    }


def seconds(rows, edge):
    return next(r["seconds"] for r in rows if tuple(r["edge"]) == edge)


def test_scenarios_inherit_the_baseline():
    assert SPEEDS["outer35"]["outer_ordinary"] == 35
    assert SPEEDS["outer35"]["motorway"] == 50
    assert SPEEDS["special_slow"]["link"] == 15
    assert SPEEDS["special_fast"]["nyc_ordinary"] == 25


@pytest.mark.parametrize(
    "raw, kph",
    [
        ("25 mph", 25 * MPH),
        ("40", 40.0),
        ("50 km/h", 50.0),
        ("25 mph;35 mph", None),
        ("signals", None),
        ("0", None),
        (None, None),
    ],
)
def test_posted_limits(raw, kph):
    assert posted_kph(raw) == kph


def test_fallback_classes():
    base = SPEEDS["baseline"]
    assert fallback_mph("residential", True, base) == 25
    assert fallback_mph("service", False, base) == 10
    assert fallback_mph("trunk_link", False, base) == 25
    assert fallback_mph("['residential', 'service']", True, base) is None


def test_speed_order_and_area():
    extract = [
        road(1, 2, "residential"),  # NYC fallback
        road(3, 4, "residential"),  # outer fallback
        road(2, 1, "residential", "15 mph"),  # posted wins
        road(4, 3, "motorway"),  # special
        road(1, 4, "residential"),  # crosses into NYC: NYC rule
        road(4, 1, "residential"),  # held
    ]
    review = [{"edge": r["edge"], "usable": True, "reasons": []} for r in extract]
    review[-1] = {
        "edge": [4, 1, 0],
        "usable": False,
        "reasons": ["RAW_NODE_BARRIER_REVIEW"],
    }
    rows = usable_edges(extract, review, NODES, nyc(), SPEEDS)
    assert len(rows) == 5
    t = 3.6 * 100.0
    assert seconds(rows, (1, 2, 0))["outer35"] == t / (25 * MPH)
    assert seconds(rows, (3, 4, 0))["baseline"] == t / (25 * MPH)
    assert seconds(rows, (3, 4, 0))["outer35"] == t / (35 * MPH)
    assert set(seconds(rows, (2, 1, 0)).values()) == {t / (15 * MPH)}
    assert seconds(rows, (4, 3, 0))["special_slow"] == t / (40 * MPH)
    assert seconds(rows, (1, 4, 0))["outer35"] == t / (25 * MPH)
    assert [r["edge"] for r in rows] == sorted(
        (r["edge"] for r in rows), key=lambda e: (str(e[0]), str(e[1]), e[2])
    )


def test_reviewed_exceptions():
    extract = [road(1, 2, "residential", "30 mph"), road(2, 1, "residential")]
    fixed = {"baseline": 9.0, "outer35": 9.0, "special_slow": 8.0, "special_fast": 7.0}
    review = [
        {
            "edge": [1, 2, 0],
            "usable": True,
            "reasons": [],
            "speed": {"rule": "signed", "mph": 20.0},
        },
        {
            "edge": [2, 1, 0],
            "usable": True,
            "reasons": [],
            "speed": {"rule": "segments", "seconds": fixed},
        },
    ]
    rows = usable_edges(extract, review, NODES, nyc(), SPEEDS)
    assert seconds(rows, (1, 2, 0))["baseline"] == 3.6 * 100.0 / (20.0 * MPH)
    assert seconds(rows, (2, 1, 0)) == fixed


def test_every_edge_needs_exactly_one_decision():
    extract = [road(1, 2, "residential")]
    with pytest.raises(ValueError, match="no review decision"):
        usable_edges(extract, [], NODES, nyc(), SPEEDS)
    extra = [
        {"edge": [1, 2, 0], "usable": True, "reasons": []},
        {"edge": [9, 9, 0], "usable": False, "reasons": []},
    ]
    with pytest.raises(ValueError, match="unknown edges"):
        usable_edges(extract, extra, NODES, nyc(), SPEEDS)


def test_usable_edge_without_a_speed_rule_fails():
    extract = [road(1, 2, "busway")]
    review = [{"edge": [1, 2, 0], "usable": True, "reasons": []}]
    with pytest.raises(ValueError, match="no speed rule"):
        usable_edges(extract, review, NODES, nyc(), SPEEDS)
