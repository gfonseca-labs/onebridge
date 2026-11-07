# Architecture and failure behavior

OneBridge is a local research CLI. It reads source data and reviewed tables,
constructs a road model, evaluates single closures and publishes evidence.
It has no server, account system or live routing service.

## Pipeline

| Stage | Inputs | Outputs |
| --- | --- | --- |
| `extract` | Pinned OSM PBFs, study config | Directed road edges, source nodes/ways, turn relations |
| `prepare` | Extraction, curated tables, pinned Census and NYC files | Bundle of costed roads, endpoints, origins and candidates |
| `run` | Bundle | Baseline routes, scenario members, affected outcomes, rankings and summary |
| `report` | Bundle and run output | Main/endpoint tables, sensitivity comparison and validated evidence |
| `demo` | Included synthetic street grid | Bundle, runs and report without data downloads |

## Components

- `prepare/`: extraction, checksums, review-table application and endpoint preparation.
- `graph.py`: point attachment, edge splitting and closure expansion.
- `turns.py`: turn-expanded graph and a separate route-audit helper.
- `routing.py`: nearest-ED paths with deterministic hospital-ID tie-breaking.
- `closures.py`: full-rerouting reference and affected-origin optimization.
- `study/`: fixed ten-configuration matrix, endpoint separation and comparison.
- `evidence.py`, `publish.py`: record validation, ranking reconciliation and
  staged publication of the evidence directory.

## Trust and interpretation

Pinned checksums establish agreement with selected source bytes. They do not
prove those sources are complete, legally authoritative or correct. Road and
origin review tables are curated inputs, not independently verified facts.
Use trusted input directories and inspect third-party archives before unpacking.

The default study is configured for NYC and the Hudson waterfront. The
synthetic demo exercises the pipeline; some evidence configuration labels and
limitations are inherited from the NYC report code. It is not evidence about
NYC or a general-purpose arbitrary-city product.

Only finite added delay enters the main ranking. Disconnections and endpoint
closures require separate interpretation. See [methods](METHODS.md) and
[results](RESULTS.md). Rank positions can repeat the same corridor dependency.

## Failure behavior

Checks reject mismatched pinned downloads, missing review decisions, unknown
attachments and inconsistent final evidence. Output handling is not a
transaction over the whole pipeline. `run`, `prepare`, `extract`, `report` and
`demo` can leave partial directories after an error; retry with a fresh path.
The extraction stage checks creation of its child directories, so an existing
empty parent alone is not refused. The final evidence directory is staged and
validated separately before publication.

The CLI returns 0 on success and argparse returns 2 for invalid arguments.
Other exceptions produce a nonzero exit and may print a traceback. There is no
stable machine-readable error schema. The downloader uses network timeouts;
the analytical pipeline has no general memory or runtime quota.

## Verification boundaries

Tests compare routing with NetworkX, optimized closures with full rerouting,
and unrestricted turn graphs with ordinary graphs. Synthetic integration
tests cover the pipeline and report. This does not provide external model
validation, field verification or a full NYC regression in CI.

The v0.2.0 run summary selects the upper middle reachable time for an even
origin count, rather than averaging the two middle values. For the released
96-origin baseline, the summary calculation gives 2.5307202464 minutes;
the conventional median of its released route outcomes is 2.5179093751
minutes, correctly rounded to 2.52 in the README. The summary's
`median_minutes` field therefore differs from the documented statistic.
The primary turn configuration handles generic unconditional restrictions;
the sensitivity also includes `restriction:motorcar` and conditional tags.
It does not evaluate a departure clock or fully support every vehicle-specific
tag. Those are model limitations, not live turn-rule enforcement.
