# WS-02 — Data I/O & caching backend

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-02 · Status: delivered in PR #134.

**Branch:** `opus5/ws-02-data-io-caching`

## Mission

Own the whole data-input seam: the io-layer registry and sink surface, the shared source
snapshot cache, the Databricks provider, and the caching component. This stream carries both
review criticals (the fictional `databricks-io` spec) and the largest spec-drift cluster,
plus real concurrency and security bugs in the snapshot store. These three components share
`_source_cache.py` / `_input_providers.py` and each other's spec text, so they must move as
one worktree.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| io-layer | 0 | 9 | 10 | 4 |
| databricks-io | 2 | 7 | 6 | 5 |
| caching | 0 | 1 | 10 | 6 |
| cross-cutting (assigned) | 0 | 1 | 1 | 1 |
| **Total** | **2** | **18** | **27** | **16** |

## Priorities

**P1 — security & concurrency (review Wave 2):**

- `io-layer-1` (M) + `seam-io-7` (L): query-string credentials pass the userinfo-only URI
  check into sidecars, cache identity and `meta.json` — reject query-string secrets in one
  shared validator (three call sites today).
- `io-layer-9` (H): `SourceCacheStore.__init__` startup sweep rmtree's other processes'
  staging builds and leased generations — cross-process advisory lock or non-destructive
  sweep; state the concurrency model in the spec.
- `io-layer-13` (M): snapshot lease release tied to GC of the returned LazyFrame — leases
  drop mid-scan.
- `databricks-io-2` (M): `cursor.execute` before the first cancellation checkpoint — a hung
  connect leaks an input-cache build slot forever.
- `io-layer-7` (H): `DatabaseSnapshotBuilder` per-batch schema inference — all-Null
  snapshots on empty results, mid-build aborts on mixed batches; derive one schema up front.
- `io-layer-8` (M): SQLite snapshot builder opens read-write and creates missing DBs.

**P2 — bug-category code fixes:** `databricks-io-3` (provider error messages discarded),
`io-layer-10` (SHA-256 of the artifact on every lease), `io-layer-14` (transient OS error →
"corrupt"), `caching-6` (evict-before-raise on oversized artifact), `caching-8` (unbounded
StatGatedCache), `over-complication-1` (json-cache cancel stub behind a live UI button —
implement or remove; UI button hand-off to WS-10), `over-complication-6` (bounded-memory
profile policy duplicated three times, untested agreement), `databricks-io-6` (batch_size in
cache identity).

**P3 — spec truth (the bulk of this stream):**

- Rewrite **both `databricks-io` documents from the code as it stands** (`databricks-io-1`,
  `testing-credibility-1` — the two criticals; plus `contracts-b-1/-2/-3`,
  `databricks-io-5`, `failure-model-1`, `seam-io-1`, `testing-credibility-2`,
  `databricks-io-7/-9`). Review Wave 0 item 4 lands here.
- io-layer: purge `safe_sink`/`best_effort_sink` and dead `*_from_config` entry points
  (`contracts-a-1/-2`, `io-layer-2/-3/-5`, `seam-io-2` — deletion of the two dead functions
  included), fold both shipped contracts (`io-layer-6`), fix the self-misdescribing
  "sections above" note (`contracts-a-4`), `contracts-a-11` overwrite/409 rule into the
  Failure model (server-api half noted to WS-04).
- caching: fold the shipped lineage-key factory into present-tense sections
  (`seam-exec-1`, `caching-3`), fix scan_stored_entry/LRU claims (`caching-1/-2`,
  `seam-exec-5/-6`), resolve `_source_cache.py` ownership one way (`contracts-a-3`,
  `caching-10`, `seam-exec-8`, `io-layer-11`, `seam-io-5`) and append the `ownership.toml`
  entry, document the artifact root owner (`seam-exec-9`), StatGatedCache consumers
  (`caching-7`), stat-gate parallel implementation (`caching-4` — doc side here; if
  unification into `StatGatedCache` is chosen, the `execution.py` edit belongs to WS-03).
- Testing sections rewritten from real files (`testing-credibility-6`, `io-layer-12`,
  `databricks-io-11` — add the missing end-to-end builder-through-store test), then delete
  this stream's docs-accuracy baseline entries.

## Finding inventory

Critical: `databricks-io-1`, `testing-credibility-1`.
High: `contracts-a-1`, `contracts-a-2`, `contracts-a-3`, `contracts-b-1`, `contracts-b-2`,
`contracts-b-3`, `databricks-io-5`, `failure-model-1`, `io-layer-2`, `io-layer-3`,
`io-layer-5`, `io-layer-6`, `io-layer-7`, `io-layer-9`, `seam-exec-1`, `seam-io-1`,
`seam-io-2`, `testing-credibility-2`.
Medium: `contracts-a-4`, `io-layer-1`, `io-layer-10`, `io-layer-12`, `io-layer-13`,
`io-layer-4`, `io-layer-8`, `over-complication-6`, `seam-io-6`, `testing-credibility-6`,
`databricks-io-11`, `databricks-io-2`, `databricks-io-3`, `databricks-io-4`,
`databricks-io-7`, `databricks-io-9`, `caching-1`, `caching-10`, `caching-2`, `caching-3`,
`caching-4`, `caching-7`, `over-complication-1`, `seam-exec-5`, `seam-exec-6`,
`seam-exec-9`, `contracts-a-11`.
Low: `contracts-a-12`, `io-layer-11`, `io-layer-14`, `seam-io-7`, `contracts-b-12`,
`databricks-io-10`, `databricks-io-12`, `databricks-io-6`, `databricks-io-8`, `caching-12`,
`caching-5`, `caching-6`, `caching-8`, `over-complication-9`, `seam-exec-8`, `seam-io-5`.

## File ownership (exclusive)

- `src/haute/_io.py`, `_polars_utils.py`, `_polars_io_registry.py`, `_source_cache.py`,
  `_database_io.py`, `_databricks_io.py`, `_input_providers.py`, `_cache.py`,
  `_dataframe_execution_cache.py`, `_lru_cache.py`, `_stat_gated_cache.py`
- `src/haute/routes/databricks.py`, `routes/input_cache.py`, `routes/json_cache.py`
- `docs/specs/io-layer/**`, `docs/specs/databricks-io/**`, `docs/specs/caching/**`
- Their test files (`tests/test_source_cache.py`, `test_databricks_io.py`,
  `test_databricks_endpoints.py`, `test_input_cache_route.py`, `test_input_providers.py`,
  `test_polars_io_registry.py`, `test_bounded_sink_contract.py`, `test_io*.py`,
  `test_lru_cache*.py`, cache suites)

## Cross-stream touchpoints

- `src/haute/execution.py` (WS-03): `caching-4`/`seam-exec-9` code moves, and the
  execution-engine/tracing halves of `seam-exec-1` spec pointers.
- Deploy consumes the lease API for `deploy-1` (WS-14) — expose/keep a public
  lease-across-lifecycle pattern and note it in the io-layer spec.
- Explore cancellation (`explore-eda-1`, WS-08) may want a checkpoint hook near
  `streaming collect` — coordinate if it lands in `_polars_utils.py`.
- `over-complication-1`: backend here; `ApiInputEditor.tsx` Cancel button is WS-10's file.
- `ownership.toml`: append-only entry for `_source_cache.py` (structure owned by WS-01).

## Definition of done

- All P1 items fixed with regression tests (concurrency tests for `io-layer-9`, credential
  tests for `io-layer-1`); the 0.7.0 contract's named lease/atomic-refresh tests exist.
- Both `databricks-io` documents describe only the shipped system; all shipped contract
  sections in the three components folded and deleted per `TEMPLATE.md:76-79`.
- Dead symbols removed (`read_polars_input_from_config`, `write_polars_output_from_config`).
- `_source_cache.py` has exactly one documented owner and an `ownership.toml` entry.
- Baseline entries for these three components deleted; findings fixed or deferred with
  written reasons.

## Verification

- `uv run pytest tests/test_source_cache.py tests/test_databricks_io.py tests/test_input_cache_route.py tests/test_input_providers.py tests/test_polars_io_registry.py -q`
- `uv run pytest tests/test_docs_accuracy.py -q`
- Affected typing/lint per `AGENTS.md`; quick preflight near completion.
