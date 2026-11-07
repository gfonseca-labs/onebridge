"""Study runs: the primary configuration and its one-at-a-time sensitivities.

Each run routes every origin to its nearest hospital on the turn-expanded
graph, then evaluates every closure scenario. Pass 1 computes all baselines;
pass 2 screens scenarios against the union of their routes and evaluates them,
so each run is screened against every run's routes, not only its own.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from shapely import prepare
from shapely.geometry import Point

from ..closures import evaluate_local
from ..graph import expand_closures
from ..ranking import rank_closures
from ..routing import original_movements, routes_with_paths
from ..turns import build_turn_graph, compile_restrictions, hospital_states, path_pieces
from .bundle import SPEEDS
from .network import attach_endpoints, road_graph
from .scenarios import scenario_members


@dataclass(frozen=True)
class Run:
    buffer: str = "primary"
    spacing: int = 500
    speeds: str = "baseline"
    time_limited_turns: bool = False
    u_turn_ban: bool = False

    @property
    def name(self):
        name = f"{self.buffer}-{self.spacing}-{self.speeds}"
        if self.time_limited_turns:
            name += "-conditional"
        if self.u_turn_ban:
            name += "-uturn-ban"
        return name


PRIMARY = Run()
RUNS = (
    PRIMARY,
    Run(buffer="wide"),
    Run(spacing=250),
    Run(speeds="outer35"),
    Run(speeds="special_slow"),
    Run(speeds="special_fast"),
    Run(time_limited_turns=True),
    Run(spacing=250, time_limited_turns=True),
    Run(u_turn_ban=True),
    Run(spacing=250, u_turn_ban=True),
)


def run_study(bundle, outdir, runs=RUNS, log=print):
    """Run every configuration and write routes, scenarios and rankings."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=False)
    used = defaultdict(set)
    results = {}
    for run, case in _cases(bundle, runs):
        baseline = case.routes
        with (out / f"{run.name}-baseline-routes.jsonl").open("x") as f:
            for oid, route in baseline.items():
                movements = case.movements(route)
                used[run.buffer].update(movements)
                record = {
                    "origin_id": oid,
                    "status": route["status"],
                    "hospital_id": route["hospital_id"],
                    "travel_time_seconds": route["travel_time_seconds"],
                    "original_movements": [list(m) for m in movements],
                }
                f.write(json.dumps(record, sort_keys=True) + "\n")
        reached = sorted(
            r["travel_time_seconds"] / 60
            for r in baseline.values()
            if r["status"] == "reachable"
        )
        results[run.name] = {
            "origins": len(baseline),
            "reachable": len(reached),
            "median_minutes": reached[len(reached) // 2],
            "unrepresented_restrictions": case.unrepresented,
            "u_turn_banned_nodes": case.banned,
        }
        log(run.name, json.dumps(results[run.name]))

    members = {}
    for buffer in sorted({run.buffer for run in runs}):
        members[buffer] = scenario_members(bundle, buffer, used[buffer])
        with (out / f"{buffer}-scenario-members.jsonl").open("x") as f:
            for cid, edges in sorted(members[buffer].items()):
                f.write(
                    json.dumps(
                        {"closure_id": cid, "movements": [list(e) for e in edges]}
                    )
                    + "\n"
                )

    for run, case in _cases(bundle, runs):
        ranking, affected = case.evaluate(members[run.buffer])
        _write_lines(out / f"{run.name}-ranking.jsonl", ranking)
        _write_lines(out / f"{run.name}-affected-outcomes.jsonl", affected)
        results[run.name]["scenarios"] = len(ranking)
        log(run.name, "scenarios", len(ranking))

    summary = {
        "bundle": str(bundle.path),
        "bundle_sha256": bundle_digest(bundle.path),
        "runs": results,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


class _Case:
    """One run's turn graph, endpoints and baseline routes."""

    def __init__(self, bundle, run, network):
        self.network = network
        split = network.split
        ways = {tuple(row["edge"]): row["ways"] for row in bundle.edges(run.buffer)}

        def edge_ways(piece):
            return ways[tuple(split.edges[piece].get("original_edge", piece))]

        by_node, groups, unrepresented = compile_restrictions(
            bundle.restrictions(), include_conditional=run.time_limited_turns
        )
        banned = ()
        if run.u_turn_ban:
            nyc = bundle.nyc_boundary()
            prepare(nyc)
            banned = {
                n
                for n, d in split.nodes(data=True)
                if not isinstance(n, str) and nyc.covers(Point(d["x"], d["y"]))
            }
        way_nodes = {w: set(ns) for w, ns in bundle.way_nodes().items()}
        starts = {n for nodes in network.origins.values() for n in nodes}
        self.tg, self.arcs = build_turn_graph(
            split, edge_ways, way_nodes, by_node, groups, starts, u_turn_banned=banned
        )
        self.hospitals = {
            h: tuple(hospital_states(self.tg, set(nodes)))
            for h, nodes in network.hospitals.items()
        }
        self.origins = {
            o: tuple(("start", n) for n in nodes)
            for o, nodes in network.origins.items()
        }
        self.unrepresented = len(unrepresented)
        self.banned = len(banned)
        self.routes = routes_with_paths(self.tg, self.hospitals, self.origins)

    def movements(self, route):
        pieces = path_pieces(self.tg, route["path_edges"])
        return original_movements(self.network.split, pieces)

    def evaluate(self, members):
        n = self.network
        pieces = expand_closures(n.graph, n.split, members, n.pieces)
        arcs = {
            c: [a for p in ps for a in self.arcs.get(p, [])] for c, ps in pieces.items()
        }
        result = evaluate_local(
            self.tg,
            self.hospitals,
            self.origins,
            {c: a for c, a in arcs.items() if a},
            self.routes,
        )
        rows = [{k: v for k, v in r.items() if k != "rank"} for r in result["ranking"]]
        base = rows[0]
        # A closure no turn can enter changes nothing; keep it in the ranking.
        for cid in sorted(c for c, a in arcs.items() if not a):
            rows.append(
                {
                    "closure_id": cid,
                    "total_finite_added_minutes": 0.0,
                    "mean_finite_added_minutes": 0.0,
                    "finite_comparable_origins": base["baseline_reachable_origins"],
                    "baseline_reachable_origins": base["baseline_reachable_origins"],
                    "baseline_unreachable_origins": base[
                        "baseline_unreachable_origins"
                    ],
                    "newly_disconnected_origins": 0,
                    "newly_disconnected_share": 0.0,
                    "retained_origins": base["retained_origins"],
                    "zero_effect_reason": "NO_TURN_STATE_ENTERS_MOVEMENT",
                }
            )
        return rank_closures(rows), result["affected_route_outcomes"]


def _cases(bundle, runs):
    """Yield ``(run, case)`` in a fixed order, reusing graphs where possible."""
    wanted = defaultdict(list)
    for run in runs:
        wanted[(run.buffer, run.speeds, run.spacing)].append(run)
    for buffer in ("primary", "wide"):
        for speeds in SPEEDS:
            spacings = [s for s in (500, 250) if (buffer, speeds, s) in wanted]
            if not spacings:
                continue
            graph = road_graph(bundle, buffer, speeds)
            for spacing in spacings:
                network = attach_endpoints(bundle, buffer, speeds, spacing, graph)
                for run in wanted[(buffer, speeds, spacing)]:
                    yield run, _Case(bundle, run, network)


def _write_lines(path, records):
    with Path(path).open("x") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def bundle_digest(path):
    """SHA-256 over the bundle's files, in path order."""
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob("*")):
        if p.is_file():
            h.update(p.relative_to(path).as_posix().encode() + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()
