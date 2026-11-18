# Changelog

## 0.3.0 — 2026-11-18

- Study any place: `onebridge place "<name>" --out <folder>` geocodes the
  name and downloads the covering Geofabrik extracts. It then builds a bundle
  with documented default rules for roads, origins, hospitals and arrivals,
  and runs the study and report. `onebridge init` does the first step alone,
  so `hospitals.json` and `config.toml` can be checked first. See
  [docs/PLACES.md](docs/PLACES.md).
- `prepare` recognises a place configuration (`[study] kind = "place"`);
  `--sources` is required only for the NYC study.
- The projection is a property of the bundle (`study.json` `epsg`, default
  UTM 18N), so routing, attachment and the map work in any UTM zone. NYC
  bundles, origin IDs and results are unchanged.
- Place studies run the eight configurations that apply outside NYC (no
  outer-urban 35 mph run and no NYC U-turn ban), and their report carries
  place-specific limitations.
- Place reports add `disconnections.csv`, listing every main-ranking closure
  that cuts origins off from every ED, and the map draws those closures in
  purple with a legend entry. The ranking is unchanged, and NYC and demo
  reports are byte-identical apart from the run ID.
- Closure re-solves use A* guided by each node's open-road time to the
  nearest hospital, a lower bound no closure can beat. Results are
  bit-identical; a Pittsburgh study's 19,302 closures run in minutes
  instead of hours, and the NYC `run` stage takes 2 minutes instead of 6.
- Place evidence lists only the outcomes a closure changes
  (`route_outcomes.jsonl`) plus each origin's baseline
  (`baseline_outcomes.jsonl`), so a city's evidence is megabytes, not
  gigabytes. The ranking is still recomputed from the records. NYC evidence
  keeps one record per pair.
- Place studies include only hospitals tagged `emergency=yes`; untagged ones
  are listed for review, and assumed only where none in the area is tagged.
- Place studies size the primary routing buffer (10, 15 or 20 km) to reach
  two EDs from the whole study rectangle; the wide run adds 10 km.
- Place studies close a road that only touches a ford or other
  grade-separated point as an ordinary segment, and `prepare` checks that
  every usable edge belongs to a closure.
- NYC results are unchanged. A rebuild of `prepare`, `run` and `report` from
  the frozen v0.2.0 extraction matches the v0.2.1 report byte for byte,
  apart from the evidence run metadata, so v0.2.1 keeps the NYC report asset.

## 0.2.1 — 2026-11-08

- Correct even-count medians in run summaries and reject duplicate road-review
  decisions and duplicate or unknown affected outcome records.
- Clarify reproduction, output failure behavior, ranking semantics, map coverage
  and the boundaries of historical validation in public documentation.
- Label synthetic evidence with its actual invented network, origins, travel
  times and limitations; remove local bundle paths from run summaries.
- Show complete hospital names in a numbered map key and remove excess margin.
- Add publication-content checks and normalized source-archive builds to CI.

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
