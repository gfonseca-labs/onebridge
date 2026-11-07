# Data

## Folders

| Folder | In git | Contents |
| --- | --- | --- |
| `data/downloads/` | no | source files downloaded as listed below, checked by SHA-256 |
| `data/nyc-sources/` | yes | reviewed tables: roads, origins, hospitals, river crossings |
| `data/nyc-extract/` | no | output of `onebridge extract` |
| bundle (any name) | no | output of `onebridge prepare` |

## Downloads

Put these files in `data/downloads/` under the names shown. `config/nyc.toml`
pins each SHA-256 (`[downloads]`), and the commands refuse a file that does not
match.

| File | Source | Terms |
| --- | --- | --- |
| `new-york.osm.pbf` | Geofabrik New York extract, OSM data as of 2026-10-24 20:20:51 UTC | ODbL 1.0 |
| `new-jersey.osm.pbf` | Geofabrik New Jersey extract, same replication state | ODbL 1.0 |
| `tl_2020_36_tabblock20.zip` | US Census Bureau, 2020 TIGER/Line blocks, New York | public domain |
| `tl_2020_34_tabblock20.zip` | US Census Bureau, 2020 TIGER/Line blocks, New Jersey | public domain |
| `nyc-borough-boundaries.geojson` | NYC Department of City Planning, Borough Boundaries (water areas included), NYC Open Data `wh2p-dxnf`, retrieved 2026-10-29 (version 26b) | NYC Open Data terms of use |

URLs:

- https://download.geofabrik.de/north-america/us/new-york.html
- https://download.geofabrik.de/north-america/us/new-jersey.html
- https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_36_tabblock20.zip
- https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_34_tabblock20.zip
- https://data.cityofnewyork.us/resource/wh2p-dxnf.geojson

Geofabrik's download pages offer the current extract, so a file downloaded
today has a newer snapshot and will not match the pinned hashes. To study
current data, update the hashes and expect to update the review tables too:
each covers exactly the edges, grid points and campus frontages of the
2026-10-24 snapshot, and `prepare` stops with an error when a table does not
match the extract.

### Extraction of the 2026-10-24 snapshot

Because those PBFs can no longer be downloaded, the v0.2.0 release carries
the output of `onebridge extract` for them, which is all `prepare` needs from
OpenStreetMap:

| File | SHA-256 |
| --- | --- |
| `onebridge-nyc-extract-2026-10-24.tar.gz` (49.9 MB) | `7b7e5b6cf70677e38d5475b285ec3ff70e0458aef2028e82f2565956535b11e6` |

The [v0.2.0 release](https://github.com/gfonseca-labs/onebridge/releases/tag/v0.2.0)
also carries the study's report,
`onebridge-nyc-report-0.2.0.tar.gz` (7.6 MB): the evidence directory
(`ranking.csv`, `route_outcomes.jsonl`, `endpoint_convention.csv`,
`evidence.json`, `map.png`), `comparison.json` and the ranking tables of all
10 configurations, under the same ODbL terms.

The extraction archive unpacks to `nyc-extract/`; the report archive unpacks
to `nyc-report/`. The extraction is packaged with a `SHA256SUMS` list and an ODbL notice.
`scripts/package_release.py` rebuilds the identical archive from an
extraction, when the input bytes and compression environment match. Extraction from
the pinned PBFs also depends on the software environment (see
[reproduction limits](REPRODUCE.md)).

The NYC endpoint serves the current version of the dataset. On 2026-11-07
it served version 26c, byte-identical to the pinned file. If the city
republishes different boundaries, the hash check stops `prepare` and the
pinned file is needed.

`python scripts/download.py` fetches the Census and NYC files and checks
them. They can also be checked by hand:

```sh
cd data/downloads
shasum -a 256 tl_2020_36_tabblock20.zip tl_2020_34_tabblock20.zip nyc-borough-boundaries.geojson
```

## Reviewed tables

These live in `data/nyc-sources/`.

All tables are gzipped JSON lines unless noted. Edges are `[u, v, key]` with
OSM node IDs.

`roads/<buffer>-review.jsonl.gz`: one row per directed edge of the buffer's
graph.

```json
{"edge": [42421728, 3602679383, 0], "reasons": [], "usable": true}
{"edge": [42421741, 42432756, 0], "reasons": ["NUMERICAL_COST_UNRESOLVED", "RAW_WAY_DIRECTIONAL_OR_CONDITIONAL_TAGS"], "usable": false}
{"edge": [42464938, 42478403, 0], "reasons": [], "speed": {"mph": 20.0, "rule": "signed"}, "usable": true}
{"edge": [42423514, 103879821, 0], "reasons": [], "speed": {"basis": "osm_source_segment_speeds", "rule": "segments", "seconds": {"baseline": 192.67, "outer35": 192.67, "special_slow": 192.67, "special_fast": 192.67}}, "usable": true}
```

The examples illustrate field shapes; the segment-speed values above are
illustrative, not authoritative source measurements. Use the committed rows
for actual study values.

A `signed` speed replaces the OSM limit with a reviewed signed limit; a
`segments` speed gives the edge's travel time per scenario directly, costed
from the posted limits of the source segments it merges (used for river
crossings).

`origins/<spacing>-review.jsonl.gz`: one row per candidate grid point.

```json
{"origin": "utm18n-500-1164-9023", "status": "building_context", "reasons": ["building_within_1m"]}
```

`status` is `clear`, `building_context` (kept after review) or `held`.

`hospitals/campuses.geojson`: one OSM campus outline per study ED, with the
OSM element it came from (`osm`).

`hospitals/arrivals.jsonl.gz`: reviewed entrance points (`kind: "entrance"`,
with `edge` and `fraction` or a `node`) and campus-frontage points removed by
review (`kind: "excluded"`, with `edge` and `reasons`).

`hospitals/registry.json`: name and point of each hospital in the NY State
Department of Health and New Jersey Department of Health facility lists that
was considered; `lon`/`lat` are `null` where the state record has no point.

`closures/<buffer>-crossings.jsonl.gz`: the river-crossing corridors.

```json
{"corridor": "hudson-holland-tunnel", "river": "Hudson", "edges": [], "ways": [], "opened_edges": []}
```

The empty lists above illustrate the schema only; real corridor rows contain
edge triples and way IDs.

`opened_edges` are corridor edges that the crossing review made usable. They
are closed only together with their corridor.

## Licences

- **OpenStreetMap:** © OpenStreetMap contributors, under the Open Database
  License 1.0. This covers `data/nyc-sources` and every extract, bundle and
  report derived from it. They are derived databases and are distributed under
  the same licence.
- **US Census TIGER/Line:** US federal public domain.
- **NY State Department of Health facility data (health.data.ny.gov):** reuse
  allowed under the OPEN-NY terms of use. Facility names and IDs appear in the
  registry and the report.
- **New Jersey Department of Health facility data (data.nj.gov):** licence-free
  under the New Jersey Open Data Initiative.
- **NYC borough boundaries:** read from `data/downloads` and copied into each
  bundle; not distributed in this repository.

Neither state agency nor the City of New York endorses this work.

