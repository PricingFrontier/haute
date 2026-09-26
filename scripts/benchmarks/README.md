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
- `opt-p06-frontier-parallelism.*`: the library's serial against parallel
  frontier sweep (24 September 2026). The parallel sweep was faster but raised
  peak memory and changed results, so the frontier stays serial; the decision is
  recorded in `specs/optimiser/low-level.md`.
- `opt-v09a-setup-memory.*`: peak memory of solve setup's server, setup
  worker and thread-mode steps at 1M and 5M quotes, with and without the
  quote-analysis extraction (26 September 2026). Each case runs in a fresh
  process and reads its own `VmHWM`, not `ru_maxrss`. The numbers and the
  thresholds they set are in `specs/optimiser/low-level.md`.
- `opt-v09b-choice-query-memory.*`: peak memory of the optimiser's bounded
  choice queries (histogram, group-by, row index, top-k) at 1M and 5M quotes,
  with and without an analysis side table (26 September 2026). The numbers and
  the thresholds they set are in `specs/optimiser/low-level.md`.
- `price_contour_ratebook_frontier/`: a standalone reproduction of a slow
  ratebook efficient frontier, with its metadata.
