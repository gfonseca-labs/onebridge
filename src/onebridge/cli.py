"""Command-line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="onebridge", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the study on a prepared bundle")
    run.add_argument("--bundle", required=True, help="prepared input directory")
    run.add_argument("--out", required=True, help="new output directory")

    report = commands.add_parser("report", help="tables, comparison and evidence")
    report.add_argument("--bundle", required=True, help="prepared input directory")
    report.add_argument("--run", required=True, help="output directory of `run`")
    report.add_argument("--out", required=True, help="new output directory")

    extract = commands.add_parser("extract", help="road graphs from OSM extracts")
    extract.add_argument("--config", required=True, help="study configuration (TOML)")
    extract.add_argument("--downloads", required=True, help="downloaded source files")
    extract.add_argument("--out", required=True, help="new output directory")

    prepare = commands.add_parser("prepare", help="build a study bundle")
    prepare.add_argument("--config", required=True, help="study configuration (TOML)")
    prepare.add_argument("--extract", required=True, help="output of `extract`")
    prepare.add_argument("--sources", required=True, help="review tables and geography")
    prepare.add_argument("--downloads", required=True, help="downloaded source files")
    prepare.add_argument("--out", required=True, help="new bundle directory")

    demo = commands.add_parser(
        "demo", help="run the whole study on a tiny synthetic bundle (seconds)"
    )
    demo.add_argument("--out", required=True, help="new output directory")

    args = parser.parse_args(argv)
    if args.command == "demo":
        from .study.demo import run_demo

        evidence = run_demo(args.out, log=_log)
        _log(
            "demo finished; open", evidence / "ranking.csv", "and", evidence / "map.png"
        )
        return 0
    if args.command == "extract":
        import tomllib
        from pathlib import Path

        from .prepare.extract import extract_roads

        config = tomllib.loads(Path(args.config).read_text())
        extract_roads(config, args.downloads, args.out, log=_log)
        return 0
    if args.command == "prepare":
        from .prepare.pipeline import prepare_bundle

        prepare_bundle(
            args.config, args.extract, args.sources, args.downloads, args.out, log=_log
        )
        return 0
    from .study.bundle import Bundle

    if args.command == "run":
        from .study.runs import run_study

        run_study(Bundle(args.bundle), args.out, log=_log)
    elif args.command == "report":
        from .study.report import build_report

        build_report(Bundle(args.bundle), args.run, args.out, log=_log)
    return 0


def _log(*parts):
    print(*parts, file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
