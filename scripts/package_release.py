"""Package an extraction or a report as a release asset.

    python scripts/package_release.py extract data/nyc-extract onebridge-nyc-extract-2026-10-24.tar.gz
    python scripts/package_release.py report out/report onebridge-nyc-report-0.2.0.tar.gz

The archive unpacks to ``nyc-extract/`` or ``nyc-report/`` with the files, a
``SHA256SUMS`` list and an ODbL notice. Entries are sorted and carry no owner
or timestamp, and the gzip header has none either, so the same input always
gives the same archive.
"""

import argparse
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

LICENCE = """\
Contains information from OpenStreetMap, (c) OpenStreetMap contributors,
which is made available under the Open Database License (ODbL) 1.0:
https://opendatacommons.org/licenses/odbl/1-0/
https://www.openstreetmap.org/copyright
"""

NOTICES = {
    "extract": """\
OneBridge NYC study: road extraction of the OpenStreetMap snapshot of
2026-10-24 20:20:51 UTC (Geofabrik New York and New Jersey extracts), as
written by `onebridge extract` with config/nyc.toml.

{licence}
This extraction is a derived database and is made available under the same
licence. Unpack it into data/ and continue with `onebridge prepare`; see
docs/REPRODUCE.md. SHA256SUMS lists every file.
""",
    "report": """\
OneBridge NYC study: output of `onebridge report` for the 2026-10-24
OpenStreetMap snapshot and the reviewed tables in data/nyc-sources.

  evidence/ranking.csv              main ranking, primary configuration
  evidence/endpoint_convention.csv  closures of origin or ED endpoint roads
  evidence/route_outcomes.jsonl     every affected origin in every scenario
  evidence/evidence.json            closures, inputs, methodology, run
  evidence/map.png                  study map with the top-10 closures
  comparison.json                   each sensitivity against the primary run
  tables/                           ranking tables for all 10 configurations

Research model of road travel time to a public road approach at each
emergency department; not ambulance response, live traffic or clinical
advice. Methods and caveats: docs/METHODS.md and docs/RESULTS.md.

{licence}
These results are a derived database and are made available under the same
licence. Hospital names come from NY State and New Jersey Department of
Health facility lists; neither agency endorses this work. SHA256SUMS lists
every file.
""",
}


def entry(top, name, data):
    info = tarfile.TarInfo(f"{top}/{name}")
    info.size, info.mtime, info.mode = len(data), 0, 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info, io.BytesIO(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=sorted(NOTICES))
    parser.add_argument("folder", help="extraction or report directory")
    parser.add_argument("archive", help="output .tar.gz (must not exist)")
    args = parser.parse_args()
    folder, archive = Path(args.folder), Path(args.archive)
    files = sorted(
        p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()
    )
    contents = {name: (folder / name).read_bytes() for name in files}
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in contents.items()
    )
    contents["README.txt"] = NOTICES[args.kind].format(licence=LICENCE).encode()
    contents["SHA256SUMS"] = sums.encode()
    top = f"nyc-{args.kind}"
    with (
        open(archive, "xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT) as tar,
    ):
        for name in sorted(contents):
            tar.addfile(*entry(top, name, contents[name]))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"{digest}  {archive.name}")


if __name__ == "__main__":
    main()
