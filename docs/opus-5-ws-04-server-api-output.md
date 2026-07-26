# WS-04 — Server API, routing & JSON output assembly

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-04 · Status: complete; cross-stream deferrals recorded.

**Branch:** `ws-04`

## Mission

The FastAPI surface — the pipeline load/save/preview/trace-hosting routes, the file watcher,
the request-correlation and admission plumbing — plus the JSON shredding / OUTPUT assembly
subsystem that those routes drive. Carries the Wave-1 blank-canvas data-loss bug and the
Wave-2 supersession admission race, and the biggest single-component drift block
(server-api: 13 medium).

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| server-api | 0 | 1 | 13 | 1 |
| json-shredding | 0 | 2 | 7 | 5 |
| cross-cutting (assigned) | 0 | 1 | 0 | 0 |
| **Total** | **0** | **4** | **20** | **6** |

## Priorities

**P1 — data loss (review Wave 1):**

- `failure-model-2` (H, cross-cutting): the primary pipeline-load route swallows every parse
  error and returns an empty `PipelineGraph()` — a structural defect presents as a blank
  canvas, and saving from it overwrites the file. Carry the parse failure (error field or
  422) so the canvas renders a load-failure state. The frontend-shared spec correction is
  noted to WS-09.

**P1 — races & wrong status codes (review Wave 2):**

- `server-api-1` (H): superseded-during-timeout preview/trace release the execution context
  while the worker thread still runs, letting a concurrent input-cache clear delete the
  generation being scanned. Coordinate the `BlockingWorkTimeoutError`/`background_task`
  semantics with WS-03.
- `over-complication-2` / `server-api-10` / `server-api-11`: the file watcher's unlocked
  `_ensure_module_deps` twin, unconditional index drop on every flush, and infinite
  self-reschedule with no cap.
- `server-api-2` / `failure-model-5`: OUTPUT dry-run admission refusal returns 500 (503
  branch unreachable); status/body inconsistency vs other routes.

**P1 — OUTPUT correctness:**

- `json-shredding-1` (H): OUTPUT null-nesting-key guard conflates "column absent" with "null
  value", 422-ing valid multi-frame mappings — restrict the guard to participating rows.
  This is a regression against the pre-remediation assembler; pin the multi-frame case.
- `json-shredding-3` (H): four present-tense statements say
  `assemble_output_from_mapping` does not run `validate_v2_output_mapping` — it does, and a
  test already pins that it runs before frame collection. Correct all four.
- `json-shredding-2` (M): ancestor `$value` column drops every row of an object table.
- `json-shredding-4` (M): incomplete-mapping rows handled inconsistently (`_node_apply.py`
  and `projection.py` use raw `enabled`). The `projection.py:2847` hunk is in WS-03's file —
  coordinate.
- `json-shredding-6` (M): O(n²) re-parsing validator now on every runtime assembly.

**P2 — spec truth:**

- server-api: fold both shipped contracts (`contracts-a-7`, `contracts-a-8`,
  `server-api-5`, `server-api-6`), rewrite endpoint tables and module maps for shipped
  routes (`server-api-4`, `server-api-9`, `readme-coherence-1`'s io_capabilities half),
  fix the closed-error-set list (`server-api-8`), the pipeline-index "two writers"
  invariant (`server-api-7`), the multi-file save-guarantee overclaim
  (`failure-model-4` — submodels/codegen halves noted to WS-05/WS-14).
- json-shredding: fold shipped OUTPUT guards (`contracts-d-4`, `json-shredding-5`,
  `contracts-d-10`), fix the validator-runs claim (`json-shredding-3`), out-of-scope
  `projection.py` doc (`json-shredding-8`), coverage/testing (`json-shredding-7`,
  `json-shredding-11`), step-order and CWD notes (`json-shredding-10`, `json-shredding-9`,
  `json-shredding-12` mojibake).
- `src/haute/server.py:383` API version: coordinate the single version line with WS-01's
  version-stamp decision.

## Finding inventory

High (4): `failure-model-2`, `server-api-1`, `json-shredding-1`, `json-shredding-3`.
Medium (20): `contracts-a-7`, `contracts-a-8`, `failure-model-4`, `over-complication-2`,
`server-api-2`, `server-api-4`, `server-api-5`, `server-api-6`, `server-api-7`,
`server-api-8`, `server-api-9`, `server-api-10`, `server-api-11`, `contracts-d-4`,
`json-shredding-2`, `json-shredding-4`, `json-shredding-5`, `json-shredding-6`,
`json-shredding-7`, `json-shredding-8`.
Low (6): `failure-model-5`, `contracts-d-10`, `json-shredding-9`, `json-shredding-10`,
`json-shredding-11`, `json-shredding-12`.

## File ownership (exclusive)

- `src/haute/server.py` (whole file; WS-01 coordinates only the version line),
  `src/haute/routes/pipeline.py`, `routes/_helpers.py`, `routes/_supersession.py`,
  `routes/output_assemble.py`, `routes/files.py`, `routes/io_capabilities.py`,
  `routes/output_destination` handlers
- `src/haute/_output_assembler.py`, `_json_shred.py`, `_json_flatten.py`, `_node_apply.py`,
  `_builders.py` OUTPUT paths (read-mostly; structural ownership of `_builders.py` is WS-06)
- `docs/specs/server-api/**`, `docs/specs/json-shredding/**`
- Their tests (`tests/test_output_assembler.py`, `test_output_nest_example_contract.py`,
  `test_pipeline_index_cache.py`, server/route suites, json-shred suites)

## Cross-stream touchpoints

- WS-03 owns admission/timeout internals and `projection.py` — align `server-api-1`,
  `failure-model-5`, and the `json-shredding-4` `projection.py` hunk.
- WS-05 owns `_save_pipeline.py` and the dissolve/save routes; `server-api-7` (index
  writers) and `failure-model-4` (save guarantee) reference `_save_pipeline.py` — WS-05 makes
  code edits there, WS-04 owns the server-api spec text.
- `failure-model-2` frontend correction (`frontend-shared/high-level.md:270-273`) → WS-09.
- `contracts-a-6` rename sweep touches `server-api` spec lines — WS-03 drives the rename;
  apply the two server-api lines here.

## Implementation outcome

The WS-04-owned server, route, OUTPUT/shred, canonical-spec, and regression-test changes are
implemented on this branch. The following adjacent changes are deliberately deferred to
their exclusive owners rather than being duplicated here:

- `projection.py` incomplete-mapping parity remains with WS-03, which owns the execution
  planner and its tests.
- The canvas load-failure presentation and frontend-shared wording remain with WS-09; this
  workstream supplies the backend 422 contract.
- Save/submodel/codegen guarantee wording outside the server-api-owned text remains with
  WS-05/WS-14, whose transaction boundaries determine those guarantees.
- The FastAPI version stamp remains with WS-01's single coordinated version decision.

## Definition of done

- Blank-canvas load failure surfaces to the client with a regression test; watcher no longer
  self-reschedules unbounded or drops the index unconditionally; supersession race closed and
  tested with WS-03's aligned timeout semantics.
- OUTPUT multi-frame mappings no longer false-422; validator cost addressed.
- server-api and json-shredding contracts folded/deleted; endpoint tables and module maps
  match the live route table.
- Baseline entries deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_output_assembler.py tests/test_pipeline_index_cache.py -q`
- Route/server suites for pipeline load/save/preview/output.
- `uv run pytest tests/test_docs_accuracy.py -q`; quick preflight near completion.
