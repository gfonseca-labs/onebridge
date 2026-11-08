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
    assert "bundle" not in summary
    assert str(bundle.path) not in (out / "summary.json").read_text()
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
    assert manifest["inputs"]["dataset"] == "synthetic-demo"
    config = manifest["inputs"]["configuration"]
    assert "grid_m" not in config and "buffer_m" not in config
    assert "two_fixed_origins" in config["origins"]
    assert all(
        "Metropolitan" not in text for text in manifest["methodology"]["limitations"]
    )
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
    assert comparison["census-weighted"]["label"] == "Invented population weights"
    assert all(
        row["label"].startswith("Synthetic configuration:")
        for name, row in comparison.items()
        if name != "census-weighted"
    )
    with pytest.raises(FileExistsError):
        build_report(
            bundle, tmp_path / "runs", tmp_path / "report", log=lambda *a: None
        )


def test_demo_command_runs_the_whole_study(tmp_path):
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    ranking = (tmp_path / "demo/report/evidence/ranking.csv").read_text().splitlines()
    assert ranking[0].startswith("rank,closure_id,")
    assert ranking[1].split(",")[1].startswith("segment-")


def test_report_context_preserves_legacy_nyc_bundles(bundle):
    from onebridge.study.report import report_context

    (bundle.path / "study.json").unlink()
    context = report_context(bundle)
    assert context["dataset"] == "nyc"
    assert context["configuration"]["buffer_m"] == 10000
    assert context["configuration"]["grid_m"] == 500


def test_report_rejects_unknown_study_kind(bundle):
    from onebridge.study.report import report_context

    (bundle.path / "study.json").write_text('{"kind": "unknown"}')
    with pytest.raises(ValueError, match="study kind"):
        report_context(bundle)


def test_map_keeps_complete_hospital_names(bundle, tmp_path, monkeypatch):
    from matplotlib.axes import Axes

    from onebridge.study.bundle import to_utm
    from onebridge.study.report import draw_map

    seen = []
    original = Axes.text

    def capture(self, x, y, text, *args, **kwargs):
        seen.append(text)
        return original(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Axes, "text", capture)
    project = to_utm()
    name = "Hospital With A Long Name That Must Remain Complete"
    path = tmp_path / "map.png"
    draw_map(
        path,
        bundle,
        [],
        {"h": (*project(-73.986, 40.750), name)},
        [project(-73.989, 40.752)],
    )
    assert any(name in " ".join(text.split()) for text in seen)
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_map_separates_labels_at_the_same_coordinate(bundle, tmp_path, monkeypatch):
    from matplotlib.axes import Axes

    from onebridge.study.bundle import to_utm
    from onebridge.study.report import draw_map

    labels = []
    original = Axes.annotate

    def capture(self, *args, **kwargs):
        label = original(self, *args, **kwargs)
        labels.append(label)
        return label

    monkeypatch.setattr(Axes, "annotate", capture)
    project = to_utm()
    point = project(-73.986, 40.750)
    draw_map(
        tmp_path / "map.png",
        bundle,
        [],
        {"h-one": (*point, "First hospital"), "h-two": (*point, "Second hospital")},
        [project(-73.989, 40.752)],
    )
    assert len(labels) == 2
    assert not labels[0].get_window_extent().overlaps(labels[1].get_window_extent())


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
