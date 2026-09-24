# Benchmarks and probes

Point-in-time benchmark, probe and reproduction programs, with the raw results
and metadata they produced. They are evidence for dated reports, not a current
test suite or a product-behaviour contract.

- `mod-f00-*`, `mod-f05-*`, `mod-f06-*`: modelling engine probes and release
  benchmarks for the model-family expansion. Their reports
  (`specs/roadmap/mod-f00-engine-probes.md`, `mod-f05-release-check.md` and
  `mod-f06-gpu-probes.md`) were retired with the delivered work; read them at
  `main` commit `7d0a7b6a`. The behaviour is specified in `specs/modelling`.
- `pr-227-*`: the PR #227 cache and pipeline review probes and benchmarks.
  Their reports (`specs/roadmap/pr-227-review.md`,
  `pr-227-implementation-progress.md` and the supporting evidence) were
  retired with the delivered plan; read them at `main` commit `7d0a7b6a`. The
  raw JSON results keep the commands and paths recorded when they ran.
- `benchmark_optimiser_*.py`: optimiser auto-range and pipeline timing
  benchmarks; each takes `--pipeline` for the project to measure.
- `price_contour_ratebook_frontier/`: a standalone reproduction of a slow
  ratebook efficient frontier, with its metadata.
