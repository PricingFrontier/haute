# P10 — Elegance: duplication, dead code, and layering (batchable cleanup)

**Severity:** LOW · **Effort:** S–M (FO-26 is M and optional) · **Risk:** low

## FO-22 — Duplicate target-chunk-bytes constants and functions
`routes/_optimiser_service.py:128-133` (constants), `:437-446` vs `:516-525` (functions)

`_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_{MIN,MAX}_BYTES`/`_BUDGET_DIVISOR` and their
`_AUTO_RANGE_` twins hold identical literals (16 MiB / 512 MiB / 16);
`_optimiser_setup_target_chunk_bytes` and `_auto_range_target_chunk_bytes` differ only in the
`ExecutionProfile`. **Fix:** one `_target_chunk_bytes(profile)` + one shared constant triple.

## FO-23 — Dead streaming-chain helpers and their import
`routes/_optimiser_service.py:3951-3978` (`_build_streaming_auto_range_chain_functions`),
`:3981-3992` (`_streaming_scenario_steps`), import at `:75`
(`build_linear_execution_chain_functions`)

Repo-wide grep finds only the definitions — no caller in `src/` or `tests/` (verified). The
streaming job builds its chain inline (`:3746-3769`). Already catalogued in
`review/MASTER/all-verified.json:3437`. **Fix:** delete both methods and the now-unused import;
note the audit cross-reference in the commit message.

## FO-24 — Unreachable branches
- `routes/_optimiser_service.py:382-385` — `_coerce_stopped_terminal_reason` returns early when
  `reason in TERMINAL_REASON_TO_STATUS`, which contains `"cancelled"`
  (`_job_lifecycle.py:34-42`, verified), so
  `return "cancelled" if reason == "cancelled" else "superseded"` can never take the
  `"cancelled"` arm. **Fix:** `return "superseded"`.
- `routes/_optimiser_service.py:4465-4467` — the missing-columns guard at `:4418-4429` already
  requires every constraint ∈ `available_cols`, so
  `[c for c in constraint_cols if c in available_cols]` is always the full list. **Fix:** drop
  the filter. (Both already tracked in `review/REMEDIATION-PROGRAM.md:624` — close the audit
  items in the commit message.)

## FO-25 — `_validate_and_project` / `_validate_and_project_auto_range` duplication
`routes/_optimiser_service.py:4390-4480` vs `:4537-4605`

Both: `collect_schema` → `_missing_columns_detail` → `_invalid_quote_id_dtype_detail` →
`_validate_input_value_contracts` → `select(...).with_columns(cast_exprs)` with the same
String→Categorical qid cast. The auto-range variant is a strict subset plus the
"objective only if present" nuance. Two copies of one validation contract will drift — the
exact hazard P03 is about. **Fix:** extract a shared
`_validate_columns_and_cast(source_lf, *, required_cols, qid_col, finite_columns,
cast_columns, job_id, execution_context, ...)`; do this **after** P03 lands so the null checks
are written once. Existing `test_optimiser_service_validation.py` is the safety net; add one
test asserting both paths emit byte-identical missing-column messages.

## FO-26 — ~750 lines of frontier-point logic live in the HTTP layer on untyped dicts (optional, M)
`routes/optimiser.py:344-1104`

`_frontier_point_result_dict`, `_materialise_ratebook_frontier_point`,
`_materialise_frontier_point_apply`, `_frontier_point_constraints_override`, … manipulate
stringly-keyed `dict[str, Any]` results (`total_objective`, `lambda_*`, `threshold_*`) with
hand-rolled finiteness re-validation (`_as_finite_float`, `:380-392`). This layering drift is
how P01's two divergent cleanup paths happened. **Fix (do last, after P01/P04 settle the
behaviour):** move materialisation into `_optimiser_service.py` behind a typed result
(dataclass or `SolveResultLike`), leaving routes to serialise. Pure refactor; the full route
test suite is the net.

## FO-27 — Exact-type error categorisation in the solve worker
`routes/_optimiser_service.py:4936-4943`

`error_categories.get(type(exc))` matches `ValueError`/`RuntimeError` exactly; subclasses
(most Polars errors) fall to "Unexpected error". Harmless mislabelling; if kept, use
`isinstance` order or document that only library-raised bare types are classified. One-line
change; include in this batch.

## TDD plan
Pure-refactor items (FO-22/23/24/26/27) ride the existing suites — run the full optimiser test
set before/after each. FO-25 adds the message-parity test named above. No behavioural fixture
changes expected anywhere in this package.

### Acceptance
- No dead helpers/imports; no unreachable branches; one chunk-bytes helper; one validation
  scaffold; audit ledger items closed with cross-references.
- `ruff check` + `mypy` clean; full optimiser suites green with zero fixture churn.
