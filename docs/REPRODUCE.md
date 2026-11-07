# Reproducing the NYC study

The test suite runs on Linux and macOS with Python 3.11 and 3.13. The full
rebuild below was checked from a fresh clone on macOS with Python 3.13. It
needs about 4 GB of memory and 11 minutes:

| Step | Time | Peak memory |
| --- | ---: | ---: |
| `extract` | 5.3 min | 3.3 GB |
| `prepare` | 43 s | 2.7 GB |
| `run` | 4.9 min | 3.7 GB |
| `report` | 10 s | 1.6 GB |

## 1. Install

```sh
git clone <repository> onebridge && cd onebridge
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[prepare,report,test]'
python -m pytest
```

## 2. Downloads

```sh
python scripts/download.py
```

This fetches the two Census files and the NYC borough boundaries into
`data/downloads/` and keeps each only if it matches its pinned SHA-256. The
two OpenStreetMap files, if you have them, go in the same folder; otherwise
use the release extraction in step 3. [DATA.md](DATA.md#downloads) lists
every file. Every command checks them against the SHA-256 values in
`config/nyc.toml` and stops on a mismatch. The OpenStreetMap files must be the
2026-10-24 snapshot. Geofabrik's current extracts will not match.

## 3. Extract the road graph

With the two OpenStreetMap PBFs:

```sh
onebridge extract --config config/nyc.toml --downloads data/downloads --out data/nyc-extract
```

Without them, use the extraction attached to the v0.2.0 release
([DATA.md](DATA.md#extraction-of-the-2026-10-24-snapshot)):

```sh
shasum -a 256 onebridge-nyc-extract-2026-10-24.tar.gz   # 7b7e5b6c...35b11e6
tar xzf onebridge-nyc-extract-2026-10-24.tar.gz -C data
(cd data/nyc-extract && shasum -a 256 -c SHA256SUMS)
```

Either way `data/nyc-extract` is byte-identical, and steps 4 and 5 need only
the Census and NYC files from `data/downloads`.

This writes `roads/primary-extract.jsonl.gz` and `roads/wide-extract.jsonl.gz`
(175,693 and 476,400 directed edges), plus `osm/ways.json.gz`,
`osm/nodes.json.gz` and `osm/restrictions.jsonl.gz`.

## 4. Prepare the bundle

```sh
onebridge prepare --config config/nyc.toml --extract data/nyc-extract \
    --sources data/nyc-sources --downloads data/downloads --out data/nyc-bundle
```

The log should report:

- primary: 159,805 usable edges, 100,965 ordinary, 3,304 structure and 21
  crossing candidates;
- wide: 438,921 usable edges, 257,090 ordinary, 6,275 structure and 21
  crossing candidates.

`prepare` writes sorted keys and gzip headers without timestamps, so the same
inputs give a byte-identical bundle.

## 5. Run and report

```sh
onebridge run --bundle data/nyc-bundle --out out/runs
onebridge report --bundle data/nyc-bundle --run out/runs --out out/report
```

`out/report/evidence/` holds:

- `ranking.csv`: 2,653 scenarios. Rank 1 is 12th Avenue at
  8.880544772304221 minutes.
- `endpoint_convention.csv`: 129 scenarios.
- `route_outcomes.jsonl`, `evidence.json` and `map.png`.

`out/report/comparison.json` compares every sensitivity with the primary run.
With the same dependency versions, `ranking.csv`, `route_outcomes.jsonl`,
`endpoint_convention.csv` and `map.png` are byte-identical across rebuilds.
`evidence.json` differs only in its run ID, start time, and package and Python
versions.
A different matplotlib version may change `map.png` without changing the
data.
