# WS-05 — Codegen & submodels

Part of the Opus 5 review split (`opus-5-workstreams.md`). Evidence and fix guidance:
`opus-5-review.md`. Owner: WS-05 · Status: delivered in PR #139.

**Branch:** `opus5/ws-05-codegen-submodels`

## Mission

Round-trip integrity between the canvas and the on-disk pipeline files: code generation,
preserve-block/preamble handling, and the submodel flatten/dissolve/drill-down machinery.
This stream owns the densest cluster of Wave-1 data-loss bugs in the review — every one of
them silently destroys hand-authored user code — so it is code-fix-first.

## Scope

| Component | C | H | M | L |
|---|---:|---:|---:|---:|
| codegen | 0 | 4 | 4 | 8 |
| submodels | 0 | 1 | 5 | 5 |
| **Total** | **0** | **5** | **9** | **13** |

## Priorities

**P1 — data loss (review Wave 1):**

- `codegen-1` (H): preserve blocks above `Pipeline(...)` re-emitted once per save — unbounded
  file growth + repeated side-effecting execution. Make the two extractors disjoint; add the
  `graph_to_code(parse(graph_to_code(g))) == graph_to_code(g)` round-trip test.
- `codegen-2` (H): an indented preserve block is relocated to column 0 and permanently blocks
  every future save with a misattributed `IndentationError`.
- `codegen-3` (H): submodel files regenerated without preamble/preserved blocks — silently
  discards hand-authored module code.
- `failure-model-3` (H): `flatten_graph` silently drops edges its own comment calls
  impossible, and the dissolve route persists that result — permanent edge loss.
- `submodels-2` (M): dissolve deletes the authoritative `modules/<name>.py` while trusting
  the client's (possibly stale) mirror — re-parse on disk before flattening or reject on
  divergence.
- `submodels-1` (M): drill-down reconstructs `modules/<name>.py` instead of the recorded
  path — a submodel authored at `lib/pricing.py` 404s.

**P2 — bugs:** `submodels-3` (duplicate edge ids on one-sided flatten), `submodels-5`
(Windows backslash in `{name}` → uncaught `ValueError`/500), `codegen-4` (duplicate labels
emit self-referential connect), `codegen-10` (boundary-edge port forwarding), `codegen-6`
(silent decorator default), `codegen-12` (stray backslash / ignored arg).

**P3 — spec truth:** fold shipped codegen contracts and dead-symbol references
(`contracts-b-4`, `contracts-b-5`, `contracts-b-13`, `codegen-7`, `codegen-8`,
`codegen-9`, `codegen-11`, `seam-io-10`, `codegen-13`); submodels ownership/scope and
reserved-name and testing fixes (`submodels-8`, `submodels-9`, `submodels-10`,
`submodels-11`, `submodels-4`, `submodels-6`). `failure-model-4`'s submodels-spec half
("no partial multi-file write") is edited here; the server-api half is WS-04's.

## Finding inventory

High: `codegen-1`, `codegen-2`, `codegen-3`, `contracts-b-4`, `failure-model-3`.
Medium: `codegen-10`, `codegen-4`, `contracts-b-13`, `contracts-b-5`, `submodels-1`,
`submodels-3`, `submodels-5`, `submodels-2`, `submodels-8`.
Low: `codegen-11`, `codegen-12`, `codegen-13`, `codegen-6`, `codegen-7`, `codegen-8`,
`codegen-9`, `seam-io-10`, `submodels-10`, `submodels-11`, `submodels-4`, `submodels-6`,
`submodels-9`.

## File ownership (exclusive)

- `src/haute/codegen.py`, `parser.py`, `_ast_helpers.py`, `_codegen_builders.py`,
  `_flatten.py`, `_parser_submodels.py`, `_submodel_paths.py`, `graph_utils.py`,
  `_config_io.py` (reserved-name list)
- `src/haute/routes/submodel.py`, `routes/_submodel_ops.py`, and the dissolve/drill-down
  handlers; `routes/_save_pipeline.py` **for the dissolve/flatten persistence path only**
  (coordinate with WS-04, which owns the server-api spec and the index-writer invariant)
- `docs/specs/codegen/**`, `docs/specs/submodels/**`
- Their tests (`tests/test_codegen*.py`, `test_parser*` codegen round-trip parts,
  `test_submodel.py`, `test_submodel_routes.py`, `test_flattening_dedup.py`)

## Cross-stream touchpoints

- `parser.py` / `_submodel_paths.py` also raise bare `ValueError` findings owned by WS-06
  (`expression-parsing-9`) — WS-06 wraps parser-call-site errors; keep the submodel-path
  fix here coordinated so both land one typed error.
- `_save_pipeline.py` is shared with WS-04 (`server-api-7`, `failure-model-4`) — WS-05 edits
  only the dissolve/flatten persistence; WS-04 owns the index-writer and spec text.
- `useWebSocketSync.ts` stale-mirror vector behind `submodels-2` is fixed in WS-09
  (`frontend-graph-canvas-1`); note the dependency but do not edit frontend files here.

## Definition of done

- Every Wave-1 item fixed with a regression test that proves user code/edges survive a
  save/dissolve round-trip; the codegen idempotence test is green.
- Submodel drill-down uses recorded paths; dissolve validates on-disk content before delete.
- Codegen and submodels contracts folded/deleted; dead `_SINK_*`/`_gen_data_sink` references
  removed from specs.
- Baseline entries deleted; findings fixed or deferred with reasons.

## Verification

- `uv run pytest tests/test_codegen.py tests/test_submodel.py tests/test_submodel_routes.py tests/test_flattening_dedup.py -q`
- Parser round-trip suites; `uv run pytest tests/test_docs_accuracy.py -q`; quick preflight.
