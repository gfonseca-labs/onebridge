# Studying any place

The NYC study rests on reviewed tables: every road edge, grid point and
hospital approach was checked by hand. A **place study** runs the same
analysis anywhere OpenStreetMap covers, with documented default rules in
place of that review. Its results are a first screen that shows where
single closures matter and where a closer look is worth it. They are not a
reviewed study.

## Quick start

```sh
python -m pip install -e '.[prepare,report]'
onebridge place "Newport, Rhode Island" --out data/studies/newport
```

`place` runs every stage:

1. **init** geocodes the name with Nominatim and takes its bounding box as the
   study rectangle. It picks the UTM zone at its centre, then downloads the
   smallest set of [Geofabrik](https://download.geofabrik.de/) extracts that
   covers 30 km around the rectangle (for Newport: Rhode Island and
   Massachusetts). It pins their SHA-256, lists every hospital it finds and
   sizes the routing buffers (see below).
2. **extract**, **prepare**, **run** and **report** then run as in the NYC
   study, with the default rules below.

The evidence lands in `data/studies/newport/report/evidence/`
(`ranking.csv`, `disconnections.csv`, `map.png`, `evidence.json`,
`route_outcomes.jsonl`, `baseline_outcomes.jsonl`).

To check the inputs first, run the stages one at a time:

```sh
onebridge init "Newport, Rhode Island" --out data/studies/newport
# review data/studies/newport/hospitals.json and config.toml
S=data/studies/newport
onebridge extract --config $S/config.toml --downloads $S/downloads --out $S/extract
onebridge prepare --config $S/config.toml --extract $S/extract --downloads $S/downloads --out $S/bundle
onebridge run --bundle $S/bundle --out $S/runs
onebridge report --bundle $S/bundle --run $S/runs --out $S/report
```

Options for `init` and `place`:

| Option | Use |
| --- | --- |
| `--bbox WEST SOUTH EAST NORTH` | study rectangle in degrees, instead of geocoding the name |
| `--pbf FILE ...` | local `.osm.pbf` extracts, instead of downloading |
| `--force` | allow a study rectangle more than 60 km across |

The study rectangle is where origins are placed. Roads are taken a
routing buffer beyond it, so trips can reach hospitals outside it. The
primary buffer starts at 10 km and grows to 15 or 20 km until every part of
the rectangle has two included EDs within reach (a straight line of up to
buffer ÷ 1.4, allowing for roads that wind). The wide sensitivity is always
10 km more. In a rural study a 10 km buffer can hold a single ED, and
closing the road to it would look like lost access when a farther ED is
still open. `config.toml` records the chosen buffers; edit them there, and
`init` logs a warning when even 20 km does not reach two EDs everywhere.

## What the study folder holds

| Path | Contents |
| --- | --- |
| `config.toml` | name, study rectangle, UTM zone, buffers, default speeds, extracts and their SHA-256, OpenStreetMap snapshot time |
| `hospitals.json` | every hospital found, with `included` and the reason; edit it, then rerun `prepare` |
| `downloads/` | the OpenStreetMap extracts |
| `extract/`, `bundle/`, `runs/`, `report/` | stage outputs, as in the NYC study |

## Default rules

**Roads.** Every edge of the OSMnx `drive_service` graph is usable, unless
one of its source ways is tagged `access`, `vehicle`, `motor_vehicle` or
`motorcar` = `no` or `private`. Travel time uses the posted `maxspeed` (the
slowest, where an edge merges several ways). Without a posted limit, it uses
the default for the road class:

| Class | Baseline | Special roads slow | Special roads fast |
| --- | ---: | ---: | ---: |
| motorway | 55 mph | 45 mph | 65 mph |
| trunk | 45 mph | 35 mph | 55 mph |
| motorway, trunk, primary, secondary and tertiary links | 25 mph | 15 mph | 35 mph |
| residential, unclassified, tertiary, secondary, primary | 30 mph | 30 mph | 30 mph |
| service, living street | 10 mph | 10 mph | 10 mph |

These are US-style defaults; edit `[speeds.*]` in `config.toml` for other
countries. Edges whose class has no default (for example `busway`) are left
out and counted in the `prepare` log.

**Origins.** Origins are points of a 500 m grid (250 m in a sensitivity run)
in the study's UTM zone, inside the study rectangle. A point is kept when a
usable road that is not a motorway, trunk or link lies within 150 m. Every
kept origin has equal weight; there are no population weights.

**Hospitals.** Hospitals are features tagged `amenity=hospital` or
`healthcare=hospital` within 30 km of the study rectangle. Only those
tagged `emergency=yes` count as having an ED:

| Tags | Included |
| --- | --- |
| `emergency=yes` | yes |
| `emergency=no` | no |
| no `emergency` tag | no, listed as "set included to true if it has an ED" |

Outside the US the hospital tag also covers clinics, practices, laboratories
and care homes: in Basel an untagged urology practice, a pathology institute
and nursing homes all carry it. Counting such a place as an ED makes nearby
closures look harmless. `init` logs how many untagged hospitals it left out;
check them in `hospitals.json` and set `included` to `true` for any that has
an ED, then rerun from `prepare`. Only where no hospital in the area is
tagged `emergency=yes` are untagged general hospitals assumed to have an ED
(marked "assumed"), leaving out names or specialities that suggest none
(psychiatric, rehabilitation, long-term care and similar).

**Arrivals.** A trip may end at any public road (residential to primary
class, at grade) within 20 m of the hospital grounds. A hospital mapped only
as a point gets a 50 m circle as its grounds. Mapped
`emergency=emergency_ward_entrance` nodes are added, each joined to the
nearest public road within 60 m.

**Closures.** Ordinary road segments and bridge or tunnel structures are
candidates, as in the NYC study. Structures that share a name and a node
close as one. There are no curated river-crossing corridors.

**Disconnections.** As in the NYC study, the ranking orders closures by
added minutes, and an origin cut off from every ED adds no minutes. In a
place study such a closure is often the main finding, so the report also
writes `disconnections.csv`: every main-ranking closure that leaves at least
one origin with no route to any ED, with its name, the number of origins cut
off, the baseline reachable origins and the IDs of the origins cut off, most
origins first. The map draws these closures as a wide purple band under the
top-10 red lines, with its own legend entry. The ranking itself does not
change. Closures of an origin's own departure road or a hospital's arrival
road stay in `endpoint_convention.csv`, as in the NYC study.

**Evidence size.** A city study can have 15,000 closures and 4,500
origins. One outcome record per pair, as in the NYC evidence, would be
gigabytes. Place evidence lists in `route_outcomes.jsonl` only the
(closure, origin) pairs whose outcome changes; `baseline_outcomes.jsonl`
holds each origin's outcome with every road open, and a missing pair keeps
it. The ranking is recomputed from these two files and must match
`ranking.csv` exactly, as for NYC.

For Newport, closing the Newport (Pell) Bridge leads the delay ranking: the
6 Jamestown origins drive to South County Hospital over the Jamestown
Verrazzano Bridge instead, 7 to 10 minutes later. Fort Adams Drive cuts 3
origins off. Most other rows are short roads that are the only way out for
one origin. With a 10 km buffer, South County Hospital lay outside the
routing area and the bridge looked like it cut Jamestown off; that is why
the buffer is now sized to reach two EDs.

**Run time.** On a recent Mac (one process, no parallelism):

| Study | Origins (500 m) | Closures | Whole `place` run |
| --- | ---: | ---: | ---: |
| Newport, RI | 109 | 1,956 | 4 min |
| Bar Harbor, ME | 426 | 2,314 | 3 min |
| Basel, CH (Swiss, French and German extracts) | 179 | 3,261 | 7 min |
| Pittsburgh, PA | 1,126 | 19,302 | 10 min |

Downloads are extra: Pennsylvania is 350 MB, Switzerland 550 MB.

**Runs.** Eight of the NYC study's ten configurations apply. The
outer-urban 35 mph run and the NYC U-turn ban are NYC-specific and are not
run.

## Limits

Everything in [methods](METHODS.md) about modeled travel time applies. On
top of it:

- Inputs are automatic, not reviewed. OpenStreetMap errors in access,
  direction, turn restrictions, hospital tags or road classes pass straight
  into the results.
- Emergency departments are inferred from tags. The arrival points are campus
  frontage, not verified entrances.
- Origins are not weighted by population.
- A hospital beyond the routing buffer is not a destination. The buffer is
  sized to reach two EDs from the whole study, but where it cannot, compare
  the wide run before reading much into a disconnection near the edge.
- The default speeds suit the United States.

Treat a top-ranked closure as a question to check, for example against
local knowledge, street imagery or the state's hospital list, before
treating it as a finding.
