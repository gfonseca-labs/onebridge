# Results: NYC study

All numbers come from `onebridge run` and `onebridge report` on the bundle
prepared from `data/nyc-sources` and the 2026-10-24 OpenStreetMap snapshot.
Methods are in [METHODS.md](METHODS.md).

## Baseline

Primary configuration: 500 m grid, 10 km buffer, baseline speeds, turn-aware.

- All 96 origins reach an ED, with a median trip of 2.52 minutes.
- 12 of the 18 EDs are the nearest ED for at least one origin.
- On the 250 m grid, 350 of 353 origins reach an ED.

The published map shows the 13 EDs selected by at least one primary baseline
or main-ranking closure route. All 18 EDs are considered by routing; the map
does not display the five that are never selected in those outcomes.
The 2.52-minute figure is the conventional median of the released baseline
outcomes. The v0.2.0 run summary instead selects the upper middle value for
even counts, yielding 2.53 minutes; see [implementation limits](ARCHITECTURE.md).

## Leading closures

The main ranking (`ranking.csv`) has 2,653 scenarios. Its leaders, by total
added minutes over the 96 origins:

| Rank | Closure | Total minutes | Notes |
| ---: | --- | ---: | --- |
| 1 | 12th Avenue, Midtown West | 8.88 | |
| 2 (6 tied) | 12th Avenue, the next six segments | 8.84 | one corridor with rank 1: no parallel road nearby |
| 8 (2 tied) | Meadowlands Parkway, Secaucus | 6.89 | access to the Secaucus hospital; see caveats |
| 10 (3 tied) | Queens-Midtown Tunnel and its two Long Island Expressway approaches | 6.59 | one Long Island City origin falls back to Elmhurst |
| 13 | unnamed motorway ramp | 6.17 | |
| 14 (2 tied) | Ashland Place, Brooklyn | 4.15 | near Brooklyn Hospital Center |
| 16 | Navy Street, Brooklyn | 3.07 | |

**Magnitudes are small.** The leading closure adds 5.6 seconds per origin on
average. In this dense area, with many EDs, a single closure mostly causes a
short detour to the same ED.

36 scenarios each cut one origin off from every ED. These are reported in the
`newly_disconnected_origins` column and do not enter the totals or break
ties.

Closures of an origin's departure road or an ED's arrival road measure the
endpoint convention rather than rerouting. The 129 such scenarios are listed
in `endpoint_convention.csv`.

## Sensitivities

| Run | Effect |
| --- | --- |
| 20 km buffer | identical to the primary run |
| 250 m grid | Queens-Midtown Tunnel group leads (20.17 min over 350 origins), ahead of Ashland Place (18.22) |
| outer-urban 35 mph | NJ baseline times up to 26% lower; same leader; 8 of the top 10 shared |
| special roads slow | two Long Island City origins switch from NYU Langone Tisch to Mount Sinai Queens and Elmhurst; Queens-Midtown Tunnel group 4.55 min; same leader |
| special roads fast | Queens-Midtown Tunnel group 7.77 min; same leader; 8 of the top 10 shared |
| time-limited turn rules (500 m) | one Brooklyn origin's baseline changes; top 10 unchanged |
| U-turns banned at NYC nodes (500 m) | top 10 unchanged |
| Census 2020 population weights | 12th Avenue still leads; 8 of the top 10 shared |

The 250 m grid is a different origin set, not a refinement of the 500 m one.
Its leader changes, and 75 scenarios change whether they disconnect any
origin.

## Caveats

- **Travel time to a road approach.** This is modeled road travel to a
  public road point at each ED. It is not ambulance response, live traffic,
  door-to-treatment time or clinical advice. Speeds are static, with no
  signals or congestion.
- **Mapped rules only.** OpenStreetMap access and turn restrictions are
  modeling assumptions. Unmapped rules appear only in sensitivities.
- **Excluded hospitals.** Metropolitan, Heights, Palisades, Hoboken University
  and Cobble Hill were excluded, which can bias nearby results.
  - Meadowlands Parkway depends on the Hoboken exclusion and on New Jersey
    speed assumptions.
- **Street-address approaches.** Where no ED entrance is published (Bellevue,
  Woodhull, Wyckoff Heights, Elmhurst), the approach is the hospital's street
  address.
- **Excluded crossing movements.** The reversible Lincoln Tunnel lanes and a
  few interstate or access-restricted crossing movements are not in the graph.
- **Origins are a grid, not population.** The Census weighting is a
  sensitivity. The kept 500 m cells hold 48% of the population of all
  candidate cells.
