"""Build distributions and normalize source-archive ownership and timestamps.

Run with the build package installed. Pass a new output directory; existing
outputs are never replaced. SHA256SUMS.txt covers the final wheel and source.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import subprocess
import sys
import tarfile
from pathlib import Path


def normalize_source(source, destination):
    with tarfile.open(source) as archive:
        members = []
        for entry in archive:
            if not (entry.isfile() or entry.isdir()):
                raise ValueError("source archive contains a link or special entry")
            data = archive.extractfile(entry).read() if entry.isfile() else b""
            members.append((entry, data))
    with (
        destination.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as out,
    ):
        for entry, data in sorted(members, key=lambda item: item[0].name):
            entry.uid = entry.gid = entry.mtime = 0
            entry.uname = entry.gname = ""
            entry.pax_headers = {}
            out.addfile(entry, io.BytesIO(data) if entry.isfile() else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    staging = args.out / "build"
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(staging)], check=True
    )
    for path in sorted(staging.iterdir()):
        target = args.out / path.name
        if path.name.endswith(".tar.gz"):
            normalize_source(path, target)
        elif path.suffix == ".whl":
            target.write_bytes(path.read_bytes())
    files = sorted(p for p in args.out.iterdir() if p.is_file())
    (args.out / "SHA256SUMS.txt").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files
        )
    )


if __name__ == "__main__":
    main()
