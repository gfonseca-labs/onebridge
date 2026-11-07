# Methods

## Question and measure

For each origin, the baseline is the modeled road travel time to the nearest
of the study's emergency departments. A scenario closes one candidate (a road
segment, a bridge or tunnel structure, or a river-crossing corridor) and
recomputes every affected origin's nearest-ED time. A scenario's score is the
unrounded sum, over origins that stay reachable, of the added minutes.
Origins that lose all access are counted separately as newly disconnected and
never enter the sum or break ties. Equal scores share a rank (competition
ranking: 1, 2, 2, 4).

## Study area

- **Origins** lie in the WGS84 rectangle
  `[-74.035, 40.690, -73.930, 40.770]`: Lower and Midtown Manhattan, northern
  Brooklyn, western Queens, and the Hudson waterfront of Jersey City, Hoboken
  and Weehawken.
- **Roads** come from that rectangle buffered by 10 km (primary) or 20 km
  (sensitivity) in UTM zone 18N, so detours and EDs outside the rectangle are
  reachable.

## Road network

The graph is OSMnx's `drive_service` network built from the Geofabrik New
York and New Jersey extracts of 2026-10-24 (OSMnx 2.1.1, simplified,
truncated by edge). `onebridge extract` feeds OSMnx from the local files
rather than Overpass, so the graph is the one OSMnx would build for the same
data.

**Road review.** Every directed edge has a row in
`data/nyc-sources/roads/<buffer>-review.jsonl.gz` that says whether it is
usable and, if not, why. The review covers:

- access tags;
- barriers;
- one-way direction;
- bridges, tunnels and other structures;
- jurisdiction and source conflicts.

It is curated data, not something the code re-derives. Of 175,693 primary
edges, 159,805 are usable (438,921 of 476,400 in the wide buffer).

**Speeds.** Each usable edge takes the first rule that applies:

1. a reviewed exception. These are signed limits from NYC DOT, and river
   crossings costed segment by segment at their own posted limits;
2. a single numeric `maxspeed` in OSM;
3. the configured class speed in `config/nyc.toml`:

   | Setting | Baseline | Sensitivities |
   | --- | ---: | --- |
   | motorway | 50 mph | 40 (slow) / 60 (fast) |
   | trunk | 35 mph | 25 / 50 |
   | ramps (`*_link`) | 25 mph | 15 / 35 |
   | residential to primary, in NYC | 25 mph | |
   | residential to primary, outside NYC | 25 mph | 35 (outer35) |
   | service, living street | 10 mph | |

A road counts as in NYC when its line touches the borough boundaries (water
areas included). Travel time is `3.6 * length / kph`. There are no signals,
congestion, turn penalties or emergency-vehicle privileges.

## Turn restrictions

Routing is on a turn-expanded graph that enforces OSM car turn restrictions:
`no_*` and `only_*` relations with a via node, a via way or a chain of via
ways. Time-limited (`:conditional`) rules are off in the primary runs and on
in a sensitivity. Another sensitivity bans U-turns at NYC nodes, standing in
for the city's business-district U-turn ban, which OSM does not map.
Relations that cannot be represented (missing members or unsupported shapes)
are counted in the run output.

## Origins

Candidate origins are UTM 18N grid points at 500 m (primary) and 250 m
(sensitivity) spacing inside the rectangle: 317 and 1,256 points. A review
table (`data/nyc-sources/origins/`) marks each one:

- **clear:** nothing in the surrounding OSM data raises a concern;
- **building context:** held only for a nearby building, then kept after
  review;
- **held:** with the reasons, such as open water, private or managed land,
  barriers, grade separation, or a long or water-crossing connector.

96 origins are kept at 500 m and 353 at 250 m.

A kept origin departs from the nearest usable edge, split at the closest
point. Exact distance ties, as between the two directions of a two-way street,
go to the edge that comes first in routing order. Where the opposite direction
is also usable, the origin may depart that way too. Origins are equally
weighted. A sensitivity weights each one by the 2020 Census population of its
grid cell.

## Emergency departments

The study includes 18 ED destinations identified using the NY and NJ state
facility lists and a review of public road approaches:

| ID | Hospital | ID | Hospital |
| --- | --- | --- | --- |
| nj-nj10904 | Jersey City Medical Center | ny-1450 | Lenox Hill Hospital |
| nj-nj10906 | Secaucus University Hospital | ny-1456 | Mount Sinai Hospital |
| ny-1288 | Brooklyn Hospital Center | ny-1458 | NewYork-Presbyterian/Weill Cornell |
| ny-1306 | NewYork-Presbyterian Brooklyn Methodist | ny-1463 | NYU Langone Tisch |
| ny-1309 | Interfaith Medical Center | ny-1466 | Mount Sinai West |
| ny-1318 | Wyckoff Heights Medical Center | ny-1469 | Mount Sinai Morningside |
| ny-1437 | NewYork-Presbyterian/Lower Manhattan | ny-1626 | Elmhurst Hospital Center |
| ny-1438 | Bellevue Hospital Center | ny-1639 | Mount Sinai Queens |
| ny-1692 | Woodhull Medical Center | ny-9700 | Northwell Greenwich Village |

Some candidates are excluded: Metropolitan, Heights, Palisades, Hoboken
University and Cobble Hill. Each lacked a verified current ED or a public
approach when the review was done.

**Arrival points.** A trip may end at any of an ED's arrival points; the
fastest one counts. Each ED has an OSM campus outline (`data/nyc-sources/hospitals/campuses.geojson`) and
two kinds of arrival point:

- **Entrances:** reviewed points on usable roads at the ED's published or
  mapped entrance. Where no entrance is published, the point is at the
  hospital's street address.
- **Campus frontage:** usable `residential`-to-`primary` roads selected by
  a search whose edge start node lies strictly inside the campus's 60 m
  buffered bounding box, and whose line lies within 20 m of the campus.
  Roads fully within the campus, bridges, tunnels and non-zero layers are excluded.
  Long edges passing near a campus can be missed when their start node lies
  outside that search box.
  - The arrival point is the road point closest to the campus.
  - Within 1 cm of an edge end, that end node is used instead of a split.
  - The context review removed 19 frontage points, for a mapped access
    restriction or a grade separation at the point.

The door and the final walk are not modeled.

## Closure candidates

- **Ordinary segments.** Usable at-grade edges that share their source ways,
  their pair of end nodes and their geometry. This is usually both directions
  of one street segment between intersections. The ID is a hash of that key.
- **Structures.** Every bridge, tunnel, ford or non-zero-layer way with its
  usable edges. Structures on a route that share a name and a node are closed
  together.
- **River crossings.** 21 reviewed corridors across the Hudson, East and
  Harlem rivers, each closed as a whole.

Ordinary segments and local structures are screened against the union of
baseline routes from every run in the same buffer. Nonempty reviewed
river-crossing corridors are always evaluated, including those off every
baseline route. A closure off every baseline route cannot change nearest-ED
time; such crossing scenarios have zero effect.
Closures of an origin's departure edge or an ED's arrival edge measure the endpoint
convention rather than network rerouting. They are reported separately in
`endpoint_convention.csv`.

## Study matrix

| Run | Buffer | Grid | Speeds | Turn rules |
| --- | --- | --- | --- | --- |
| primary-500-baseline (primary) | 10 km | 500 m | baseline | unconditional |
| wide-500-baseline | 20 km | 500 m | baseline | unconditional |
| primary-250-baseline | 10 km | 250 m | baseline | unconditional |
| primary-500-outer35, -special_slow, -special_fast | 10 km | 500 m | as named | unconditional |
| primary-{500,250}-baseline-conditional | 10 km | both | baseline | with time-limited rules |
| primary-{500,250}-baseline-uturn-ban | 10 km | both | baseline | U-turns banned at NYC nodes |

The report compares each sensitivity with the primary run. It flags a changed
leader, changed disconnections, ED choice changes and large relative changes
in baseline time or top-10 totals.

## Verification

The test suite checks the engine against independent implementations:

- the router against networkx Dijkstra on random graphs;
- the local closure evaluator against a full re-route of every origin;
- the turn-expanded graph against the plain graph when there are no
  restrictions;
- routes against an independent turn-restriction audit.

Release preparation reported agreement with an earlier study build for
road extraction, usable edges and times, origins, departures, Census weights,
hospital arrivals and closure candidates. That comparison is historical
evidence, not a public independent reference implementation. Ordinary-segment
names were normalized to a stable order (see the changelog).

Public verification uses the tests, committed source tables, release extraction
and report, and the [rebuild procedure](REPRODUCE.md). CI does not run the full
NYC rebuild. Agreement between implementations verifies algorithm behavior on
their inputs; it does not independently validate curated source decisions or
real-world travel times.

