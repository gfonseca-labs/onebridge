# Changelog

## Unreleased

- Correct even-count medians in run summaries and reject duplicate road-review
  decisions and duplicate or unknown affected outcome records.
- Clarify reproduction, output failure behavior, ranking semantics, map coverage
  and the boundaries of historical validation in public documentation.

## [0.2.0](https://github.com/gfonseca-labs/onebridge/releases/tag/v0.2.0) — 2026-11-07

First public version of the NYC hospital-access closure study.

- `onebridge extract`, `prepare`, `run` and `report` rebuild the study from
  pinned downloads and the reviewed tables in `data/nyc-sources`.
- Turn-aware routing on OSM car turn restrictions, including via-way chains;
  time-limited rules and a NYC U-turn ban as sensitivities.
- Closure candidates: ordinary road segments, local bridge and tunnel
  structures, and 21 river-crossing corridors, screened against every baseline
  route.
- Sensitivity matrix: 20 km buffer, 250 m grid, outer-urban and special-road
  speeds, turn-rule variants and Census 2020 population weights.
- `extract` and `prepare` write byte-reproducible files. The release carries
  the extraction of the 2026-10-24 OpenStreetMap snapshot
  (`onebridge-nyc-extract-2026-10-24.tar.gz`), since that snapshot can no
  longer be downloaded.
- `onebridge demo` runs the whole study on a tiny synthetic bundle in
  seconds, with no downloads.
- `scripts/download.py` fetches and checks the Census and NYC downloads.
- The release also carries the full NYC report
  (`onebridge-nyc-report-0.2.0.tar.gz`); the README shows its map.
- Evidence schema 1.1.0. Run metadata is limited to run ID, start time, and
  package and Python versions.

Release preparation reported agreement with an earlier study build, apart
from the format of ordinary-segment names. Public reproduction uses the
release extraction and report; the earlier build is not a required input. Names are now the sorted distinct `name`
tags of the segment's source ways, or an empty list when unnamed. Before,
they were OSMnx's merged edge attribute, whose list order depended on Python's
string hashing. Routes, rankings and scores are unchanged; in `evidence.json`
the labels of 46 closures change format, and the map is titled "NYC study".
