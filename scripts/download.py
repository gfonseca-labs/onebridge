"""Fetch the study's downloads into data/downloads and check their SHA-256.

    python scripts/download.py

Downloads the Census 2020 block files and the NYC borough boundaries; files
already present and matching are skipped. A file is kept only if its digest
matches [downloads] in config/nyc.toml. The 2026-10-24 OpenStreetMap
extracts can no longer be downloaded: use the extraction attached to the
v0.2.0 release instead (docs/REPRODUCE.md, step 3).
"""

import argparse
import hashlib
import ssl
import sys
import tomllib
import urllib.request
from pathlib import Path

URLS = {
    "tl_2020_36_tabblock20.zip": "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_36_tabblock20.zip",
    "tl_2020_34_tabblock20.zip": "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_34_tabblock20.zip",
    "nyc-borough-boundaries.geojson": "https://data.cityofnewyork.us/resource/wh2p-dxnf.geojson",
}


def tls_context():
    """System certificates, or certifi's bundle when installed.

    The python.org macOS builds ship without system certificates until their
    "Install Certificates" step runs; certifi comes with the prepare extra.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "onebridge"})
    with (
        urllib.request.urlopen(request, timeout=120, context=tls_context()) as r,
        path.open("wb") as f,
    ):
        while chunk := r.read(1 << 20):
            f.write(chunk)


def sha256(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config/nyc.toml")
    parser.add_argument("--out", default="data/downloads")
    args = parser.parse_args()
    pinned = tomllib.loads(Path(args.config).read_text())["downloads"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for name, url in URLS.items():
        target = out / name
        if target.exists() and sha256(target) == pinned[name]:
            print(f"ok       {name} (already present)")
            continue
        print(f"fetching {name} ...", flush=True)
        partial = out / (name + ".part")
        try:
            fetch(url, partial)
        except OSError as error:
            partial.unlink(missing_ok=True)
            failed.append(name)
            print(f"FAILED   {name}: {error}")
            continue
        if sha256(partial) == pinned[name]:
            partial.replace(target)
            print(f"ok       {name}")
        else:
            partial.unlink()
            failed.append(name)
            print(f"MISMATCH {name}: the source no longer serves the pinned file")
    missing = [n for n in pinned if n.endswith(".osm.pbf") and not (out / n).exists()]
    if missing:
        print(
            "\nThe OpenStreetMap extracts are not downloadable any more. Unpack\n"
            "onebridge-nyc-extract-2026-10-24.tar.gz from the v0.2.0 release into\n"
            "data/ and skip `onebridge extract` (docs/REPRODUCE.md, step 3)."
        )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
