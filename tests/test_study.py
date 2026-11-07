"""End-to-end study run on a small synthetic bundle."""

import json
from itertools import pairwise
from statistics import median

import pytest

from onebridge.cli import main
from onebridge.study.demo import STREETS, write_bundle
from onebridge.study.report import build_report, complete_records
from onebridge.study.runs import Run, run_study


@pytest.fixture
def bundle(tmp_path):
    return write_bundle(tmp_path / "bundle")


def lines(path):
    return [json.loads(x) for x in path.open()]


def test_run_writes_routes_scenarios_and_rankings(bundle, tmp_path):
    out = tmp_path / "out"
    runs = (Run(), Run(time_limited_turns=True), Run(u_turn_ban=True))
    summary = run_study(bundle, out, runs=runs, log=lambda *a: None)
    assert set(summary["runs"]) == {r.name for r in runs}
    routes = {
        r["origin_id"]: r
        for r in lines(out / "primary-500-baseline-baseline-routes.jsonl")
    }
    assert all(r["status"] == "reachable" for r in routes.values())
    assert all(r["hospital_id"] == "h-east" for r in routes.values())
    assert summary["runs"][Run().name]["median_minutes"] == median(
        r["travel_time_seconds"] / 60 for r in routes.values()
    )
    # o-south starts on 4-5 and drives straight to the hospital node.
    assert routes["o-south"]["original_movements"][0] == [4, 5, 0]

    ranking = lines(out / "primary-500-baseline-ranking.jsonl")
    totals = [r["total_finite_added_minutes"] for r in ranking]
    assert totals == sorted(totals, reverse=True)
    assert ranking[0]["rank"] == 1 and ranking[0]["total_finite_added_minutes"] > 0
    members = {m["closure_id"] for m in lines(out / "primary-scenario-members.jsonl")}
    assert members <= {f"segment-{w}" for w in STREETS}
    assert summary["runs"]["primary-500-baseline-uturn-ban"]["u_turn_banned_nodes"] > 0


def test_turn_restriction_changes_the_route(bundle, tmp_path):
    run_study(bundle, tmp_path / "out", runs=(Run(),), log=lambda *a: None)
    route = {
        r["origin_id"]: r
        for r in lines(tmp_path / "out" / "primary-500-baseline-baseline-routes.jsonl")
    }["o-west"]
    # Without the ban o-west would turn right onto 2-5 (15 + 30 + 15 = 60 s);
    # with it the fastest way is straight on via node 3 (15 + 30 + 30 = 75 s).
    movements = [tuple(m) for m in route["original_movements"]]
    assert ((1, 2, 0), (2, 5, 0)) not in list(pairwise(movements))
    assert route["travel_time_seconds"] == pytest.approx(75.0)


def test_existing_output_directory_is_refused(bundle, tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(FileExistsError):
        run_study(bundle, tmp_path / "out", runs=(Run(),), log=lambda *a: None)


def test_report_publishes_validated_evidence(bundle, tmp_path):
    run_study(bundle, tmp_path / "runs", log=lambda *a: None)
    evidence = build_report(
        bundle, tmp_path / "runs", tmp_path / "report", log=lambda *a: None
    )
    files = sorted(p.name for p in evidence.iterdir())
    assert files == [
        "endpoint_convention.csv",
        "evidence.json",
        "map.png",
        "ranking.csv",
        "route_outcomes.jsonl",
    ]
    manifest = json.loads((evidence / "evidence.json").read_text())
    assert set(manifest["run"]) == {
        "run_id",
        "started_at_utc",
        "onebridge_version",
        "python_version",
    }
    outcomes = lines(evidence / "route_outcomes.jsonl")
    assert len(outcomes) == len(manifest["closures"]) * 2
    comparison = json.loads((tmp_path / "report" / "comparison.json").read_text())
    assert comparison["wide-500-baseline"]["triggers"] == []
    with pytest.raises(FileExistsError):
        build_report(
            bundle, tmp_path / "runs", tmp_path / "report", log=lambda *a: None
        )


def test_demo_command_runs_the_whole_study(tmp_path):
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    ranking = (tmp_path / "demo/report/evidence/ranking.csv").read_text().splitlines()
    assert ranking[0].startswith("rank,closure_id,")
    assert ranking[1].split(",")[1].startswith("segment-")


@pytest.mark.parametrize("case", ["duplicate", "unknown-origin", "unknown-closure"])
def test_report_rejects_ambiguous_or_unknown_affected_records(case):
    before = {"status": "reachable", "hospital_id": "h", "travel_time_seconds": 1.0}
    baseline = {"o": before}
    record = {
        "closure_id": "c",
        "origin_id": "o",
        "baseline": before,
        "scenario": before,
        "finite_added_minutes": 0.0,
        "newly_disconnected": False,
    }
    records = [record]
    if case == "duplicate":
        records.append(dict(record))
    elif case == "unknown-origin":
        record["origin_id"] = "other"
    else:
        record["closure_id"] = "other"
    with pytest.raises(ValueError, match="duplicate|unknown"):
        complete_records(["c"], ["o"], baseline, records)
