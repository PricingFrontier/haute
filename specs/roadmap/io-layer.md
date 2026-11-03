# IO layer roadmap

## Scope

Data Input providers and their freshness proofs, the input snapshot store and
its generations, the config path anchoring the registry and the engine share,
and the input preparation builds. Current behaviour is specified in
[the IO-layer specification](../io-layer/high-level.md) and
[caching](../caching/high-level.md). `IO-R07` comes from the
[31 October 2026 codebase review](codebase-review-2026-10-31.md); the numbering
continues from the earlier reviews' packages.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IO-R07 | Planned | P2 | Explicit `/api/input-cache/build` jobs for `"bounded"` Data Inputs run under memory admission and isolated worker supervision like automatic preparation builds, `input_cache` and `json_cache` resolve paths from the project root rather than `Path.cwd()`, and `_infer_jsonl_in_parallel` avoids re-walking the prefix range already scanned by `_learn_jsonl_prefix`. |

## Planned improvements

### IO-R07 — Memory admission and isolated worker execution for explicit Data Input cache builds, project-root path anchoring, and non-overlapping parallel JSONL schema inference
**Why:** Three IO and JSON-shredding defects affect explicit Data Input cache builds,
route path resolution and parallel JSONL schema inference:
1. When automatic input preparation builds a `"bounded"` Data Input snapshot
   (`src/haute/_input_preparation.py::_run_build`), it dispatches to
   `run_supervised_data_input_build` in an isolated child process with an
   `ExecutionContext` and memory limit. When the user clicks **Cache** on the same
   `"bounded"` Data Input node (`POST /api/input-cache/build`),
   `src/haute/routes/input_cache.py::_run_build` runs `provider.ensure_snapshot`
   directly on a daemon thread inside the FastAPI server process with
   `execution_context=None`, bypassing server memory admission, chunked-write RSS
   monitoring and process isolation.
2. `input_cache._project_root` and `json_cache._resolve_data_path` hardcode
   `Path.cwd()` instead of resolving from the configured project root.
3. In `_infer_jsonl_in_parallel(data_path, ranges)` (`_json_shred/_inference.py`),
   `_learn_jsonl_prefix(data_path, ranges[-1][1])` walks up to `_INITIAL_SAMPLE_RECORDS`
   (`10,000`) records from byte `0` into `merged`, and then `tasks` dispatches **all**
   byte `ranges` (including the prefix bytes starting at `0`) to the worker pool.
   Furthermore, when a JSONL file is `>= _PARALLEL_MIN_BYTES` (64 MiB) with
   `len(ranges) > 1` but has fewer than `10,000` records total, `_learn_jsonl_prefix`
   consumes the entire file without `known_structure._remaining` reaching `0`, so
   `known_structure.snapshot()` returns `None` and the worker pool re-parses and
   re-walks 100% of the file a second time without a filter seed.

**Plan:** Route `"bounded"` Data Input builds in `routes/input_cache.py::_run_build`
through the same memory admission and `run_supervised_data_input_build` isolated
worker path as `_input_preparation.py::_run_build` (sharing the build runner
between both entry points); resolve project paths in `input_cache` and
`json_cache` from `_get_project_root()`; and have `_learn_jsonl_prefix` return the
byte offset where prefix sampling stopped so `_infer_jsonl_in_parallel` skips the
already-walked prefix bytes (and returns `merged` immediately when the prefix pass
reached `ranges[-1][1]`).

**Acceptance:** Clicking **Cache** (`POST /api/input-cache/build`) on a `"bounded"`
Data Input runs under `run_supervised_data_input_build` with a bounded
`ExecutionContext` rather than in-process with `execution_context=None`; both
`/api/input-cache/*` and `/api/json-cache/*` resolve relative paths against the
configured project root when `Path.cwd()` differs; and `_infer_jsonl_in_parallel`
starts worker ranges at the byte offset after `_learn_jsonl_prefix` and dispatches
no worker tasks when the prefix pass already reached EOF.

**Dependencies:** Coordinates with `PCFG-R04` (one project context).

**Evidence:** `src/haute/routes/input_cache.py::_run_build`;
`src/haute/_input_preparation.py::_run_build`;
`src/haute/routes/input_cache.py::_project_root`;
`src/haute/routes/json_cache.py::_resolve_data_path`;
`src/haute/_json_shred/_inference.py::_learn_jsonl_prefix`;
`src/haute/_json_shred/_inference.py::_infer_jsonl_in_parallel`;
`src/haute/_json_shred/_inference_filter.py::InferenceFilter.snapshot`.
