# Reference Pipeline — High-Level Specification

## Purpose

`examples/reference/` is the repository's runnable reference pipeline: a small
Haute project that reads synthetic quotes, derives two rating features and returns
priced rows through an explicit output mapping. The repository-root `haute.toml`
selects `examples/reference/main.py` through `[project].pipeline`, so commands that
resolve the project default (`haute run`, `haute serve`) load, run and preview it
from a fresh clone with no local files to supply.

It is example material for working on Haute itself, not the Haute package's
runtime implementation and not part of the installed package.

## Scope

In scope:

- The tracked files under `examples/reference/`: the generated pipeline, its
  Data Input and output sidecars, and the synthetic quote data.
- The root `haute.toml` selection of this pipeline as the repository default.

Out of scope:

- The generic graph/config parser, persistence, and code-generation behaviour,
  owned by [pipeline-config](../pipeline-config/high-level.md) and
  [codegen](../codegen/high-level.md).
- The project layout `haute init` scaffolds (`rating/main.py` in a user's project),
  owned by [cli](../cli/high-level.md).
- Production distribution and the quality policy for excluded non-product
  directories, owned by [build-and-distribution](../build-and-distribution/high-level.md)
  and [engineering-quality](../engineering-quality/high-level.md).
- Untracked files a run leaves locally, such as `.haute_cache/` at the project root.

## Behaviour

- The pipeline is named `reference` and has three nodes wired in a line: a CSV
  Data Input (`quotes`), a Polars transform (`features`), and an output (`priced`).
- `quotes` reads `examples/reference/data/quotes.csv`, six synthetic quotes with
  `quote_id`, `driver_age`, `vehicle_year`, `region` and `sum_insured`. Its
  sidecar path is project-root relative, like every Data Input path.
- `features` adds `vehicle_age` (2026 minus `vehicle_year`) and `driver_band`
  (the driver's age cut at 25, 40 and 65, as text).
- `priced` maps `quote_id`, `vehicle_age`, `driver_band` and `sum_insured` from the
  `features` port into one JSON object per quote.
- `main.py` is exactly what Haute's code generation writes for this graph, so
  saving it from the editor does not rewrite it.

## Design rationale

- A reference the repository selects as its default must run from a fresh
  clone; the earlier `rating/` snapshot named data and a sidecar it did not
  track, so it could only be inspected. Synthetic data keeps the example
  self-contained and free of anything confidential.
- The example is deliberately small: it exercises the parser, input
  preparation, a transform and output assembly end to end, and leaves every
  node type's detail to its own component tests.
- It lives under `examples/`, outside the package and outside the normal Ruff
  target, because it is generated pipeline code with the layout a user project
  has, not packaged source.

## Interactions

- [pipeline-config](../pipeline-config/high-level.md) supplies the decorators,
  sidecar resolution and the project resolver that reads `[project].pipeline`.
- [codegen](../codegen/high-level.md) writes `main.py`.
- [io-layer](../io-layer/high-level.md) prepares the Data Input's snapshot, and
  [json-shredding](../json-shredding/high-level.md) assembles the output mapping.
- [engineering-quality](../engineering-quality/high-level.md) excludes
  `examples/` from the normal Ruff target.

## Failure model

- A missing or unreadable data file fails the Data Input's preparation loudly;
  no placeholder frame is supplied.
- A sidecar that does not match its node fails through the shared configuration
  error contract, and a graph, port or mapping mismatch through Haute's generic
  parser and runtime validation; the example adds no error translation.
- A run that cannot prepare its input reports the failing node and exits
  non-zero (`haute run`).
