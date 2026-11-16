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
   covers the 20 km routing buffer (for Newport: Rhode Island and
   Massachusetts). It pins their SHA-256 and lists every hospital it finds.
2. **extract**, **prepare**, **run** and **report** then run as in the NYC
   study, with the default rules below.

The evidence lands in `data/studies/newport/report/evidence/`
(`ranking.csv`, `map.png`, `evidence.json`).

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

The study rectangle is where origins are placed. Roads are taken 10 km
(primary) and 20 km (wide sensitivity) beyond it, so trips can reach
hospitals outside it.

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
`healthcare=hospital` within the wide routing buffer. Inclusion follows the
`emergency` tag:

| Tags | Included |
| --- | --- |
| `emergency=yes` | yes |
| `emergency=no` | no |
| no `emergency` tag, name or speciality suggests no emergency department (psychiatric, rehabilitation, long-term care, and similar) | no |
| no `emergency` tag, anything else | yes, marked "assumed to have an ED" |

Check the assumed ones in `hospitals.json`: OpenStreetMap often lacks the
tag, and a hospital without an ED makes nearby closures look less harmful
than they are.

**Arrivals.** A trip may end at any public road (residential to primary
class, at grade) within 20 m of the hospital grounds. A hospital mapped only
as a point gets a 50 m circle as its grounds. Mapped
`emergency=emergency_ward_entrance` nodes are added, each joined to the
nearest public road within 60 m.

**Closures.** Ordinary road segments and bridge or tunnel structures are
candidates, as in the NYC study. Structures that share a name and a node
close as one. There are no curated river-crossing corridors.

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
- A hospital beyond the 10 km routing buffer is not a destination in the
  primary run. Compare the wide (20 km) run before reading much into a
  closure near the buffer's edge.
- The default speeds suit the United States.

Treat a top-ranked closure as a question to check, for example against
local knowledge, street imagery or the state's hospital list, before
treating it as a finding.
