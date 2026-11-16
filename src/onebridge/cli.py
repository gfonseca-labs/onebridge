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
    prepare.add_argument(
        "--sources",
        help="review tables and geography (NYC); a place study reads its own folder",
    )
    prepare.add_argument("--downloads", required=True, help="downloaded source files")
    prepare.add_argument("--out", required=True, help="new bundle directory")

    init = commands.add_parser(
        "init", help="start a study of any place from OpenStreetMap"
    )
    _place_arguments(init)

    place = commands.add_parser(
        "place", help="init, extract, prepare, run and report a place in one go"
    )
    _place_arguments(place)

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
        from .prepare.place import is_place_config, prepare_place

        if is_place_config(args.config):
            from pathlib import Path

            study = args.sources or Path(args.config).parent
            prepare_place(
                args.config, args.extract, study, args.downloads, args.out, log=_log
            )
            return 0
        if args.sources is None:
            parser.error("prepare: --sources is required for the NYC study")
        prepare_bundle(
            args.config, args.extract, args.sources, args.downloads, args.out, log=_log
        )
        return 0
    if args.command in ("init", "place"):
        return _place(args, full=args.command == "place")
    from .study.bundle import Bundle

    if args.command == "run":
        from .study.runs import run_study

        run_study(Bundle(args.bundle), args.out, log=_log)
    elif args.command == "report":
        from .study.report import build_report

        build_report(Bundle(args.bundle), args.run, args.out, log=_log)
    return 0


def _place_arguments(command):
    command.add_argument("name", help='place to study, e.g. "Newport, Rhode Island"')
    command.add_argument("--out", required=True, help="new study folder")
    command.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="study rectangle in degrees, instead of geocoding the name",
    )
    command.add_argument(
        "--pbf",
        nargs="+",
        help="local .osm.pbf extract(s), instead of downloading from Geofabrik",
    )
    command.add_argument(
        "--force", action="store_true", help="allow a study rectangle over 60 km"
    )


def _place(args, full):
    """``init`` (and for ``place``, every later stage) for one place."""
    import tomllib

    from .prepare.extract import extract_roads
    from .prepare.place import init_place, prepare_place
    from .study.bundle import Bundle
    from .study.report import build_report
    from .study.runs import run_study

    config = init_place(
        args.name, args.out, bbox=args.bbox, pbf=args.pbf, force=args.force, log=_log
    )
    study = config.parent
    if not full:
        _log(
            "next: review",
            study / "hospitals.json",
            "then run `onebridge extract` and `prepare` with",
            config,
        )
        return 0
    extract_roads(
        tomllib.loads(config.read_text()),
        study / "downloads",
        study / "extract",
        log=_log,
    )
    prepare_place(
        config,
        study / "extract",
        study,
        study / "downloads",
        study / "bundle",
        log=_log,
    )
    bundle = Bundle(study / "bundle")
    run_study(bundle, study / "runs", log=_log)
    evidence = build_report(bundle, study / "runs", study / "report", log=_log)
    _log("done; open", evidence / "ranking.csv", "and", evidence / "map.png")
    return 0


def _log(*parts):
    print(*parts, file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
