# P02 — `/save` artifact contract: non-atomic overwrite, no finiteness gate, no schema version

**Severity:** HIGH (deploy-critical) · **Effort:** M · **Silent-wrongness:** partially (FO-03/FO-05/FO-06)

The JSON written by `/save` is the artifact the deploy scorer loads on every quote
(`deploy/_scorer.py:572` → `load_optimiser_artifact(path)`) and the OPTIMISER_APPLY node
consumes in-pipeline (`_builders.py:1026-1068`). Defects here reach production pricing.

## FO-02 — [HIGH] Non-atomic in-place overwrite can destroy a good deployed artifact
`routes/optimiser.py:1628` (`save_result`)

### Evidence
```python
out.parent.mkdir(parents=True, exist_ok=True)
payload = _build_artifact_payload(...)
out.write_text(json.dumps(payload, indent=2, default=str))
```

### Impact
`write_text` truncates then writes. A worker kill, power loss, or disk-full mid-write leaves a
truncated file **and the previously-good artifact is gone**. The deploy scorer's next quote
fails on `json.load` (loud, but the outage persists until someone re-saves). The server's own
parquet artifacts already get the write-to-fresh-dir treatment
(`_persist_apply_result_artifact`, `_optimiser_service.py:1059-1067`); the user-facing save
path does not.

### Fix design
Write to a sibling temp file (`f"{out}.tmp-{os.getpid()}"`), `flush()` + `os.fsync()`, then
`os.replace(tmp, out)` — atomic on the same volume on both POSIX and Windows. Unlink the temp
file on any exception. Consider a tiny shared helper (e.g. `haute._io.atomic_write_text`) since
the same pattern is useful elsewhere; check first whether one already exists.

## FO-03 — [MEDIUM] Non-finite lambdas/objective round-trip silently into the deploy scorer
`routes/optimiser.py:1539` + `:1628`; consumed at `_builders.py:1481`

### Evidence
`"lambdas": solve_result.lambdas` is serialised with `json.dumps(..., default=str)`. Python's
`json` does **not** route floats through `default` — NaN/Infinity are emitted as bare `NaN` /
`Infinity` tokens (allowed by default), and `json.loads` parses them straight back.
`ApplyOptimiser.__init__` (`price_contour/apply.py:56-94`) validates lambda *keys* but never
value finiteness, so a NaN multiplier flows into the Rust argmax → undefined selection / NaN
`optimal_scenario_value` at deploy time. (Trigger — the solver emitting a non-finite lambda —
was not reproduced; the propagation mechanism is verified. Treat as a boundary-validation gap.)

### Fix design
In `_build_artifact_payload`/`save_result`, assert every value in `lambdas`,
`total_constraints`, and `total_objective`/`baseline_objective` is finite; raise
`HTTPException(422)` naming the offending key. Belt-and-braces: serialise with
`json.dumps(..., allow_nan=False)` so any slip raises instead of writing invalid JSON.

## FO-04 — [MEDIUM] Loaded artifacts are never schema/version-gated
`_optimiser_io.py:46-48` / `:107-108`

### Evidence
`json.load(f)` → returned as-is. The payload's `version` field is a human label
(`optimiser_20260101_120000`, `optimiser.py:1536`), not a schema version, and nothing reads it
on load. Contrast upstream `ApplyOptimiser.load`, which gates `version > 1`
(`price_contour/apply.py:451-456`). Missing `mode` silently defaults to `"online"`
(`_builders.py:1455`).

### Fix design
Add integer `schema_version: 1` to the payload. Add `_validate_artifact_schema(artifact)` in
`_optimiser_io.py`, called from `load_optimiser_artifact` and `load_mlflow_optimiser_artifact`:
raise on unknown/newer `schema_version` and on missing per-mode required keys (`lambdas` for
online; `factor_tables` + `factor_columns` for ratebook). Keep the label `version` separate.
Artifacts without `schema_version` (pre-existing saves) should be accepted as version 1 —
document that explicitly.

## FO-05 — [LOW] `lambdas` strictness diverges between apply and trace
`_builders.py:1481` (`artifact["lambdas"]`, KeyError if absent) vs
`_optimiser_apply_explainability.py:161` (`artifact.get("lambdas") or {}`, silently empty).

### Fix design
One `_required_artifact_lambdas(artifact)` helper raising a named error; use it in both. Falls
out naturally from FO-04's schema validation (make `lambdas` a required online key).

## FO-06 — [LOW] `mode: ""` applies as online but errors in trace
`_builders.py:1455` (`artifact.get("mode", "online")` → `""` ≠ `"ratebook"` → online) vs
`_optimiser_apply_explainability.py:45-52` (explicit empty-mode → `OptimiserApplyTraceError`).

### Fix design
`_dispatch_apply` should reject an explicitly empty `mode` (absent → online; blank → raise),
matching the trace path. Also folds into FO-04's schema validation.

## TDD plan
1. `tests/test_optimiser_io.py::test_save_is_atomic_on_write_failure` — pre-existing good
   artifact at `out`; monkeypatch `os.replace` (or the tmp-file write) to raise mid-save; call
   `save_result`; assert the original bytes are untouched and `load_optimiser_artifact(out)`
   still returns the old payload. Fails today (file already truncated by `write_text`).
2. `tests/test_optimiser_apply_artifacts.py::test_save_rejects_nonfinite_lambda` — job whose
   `solve_result.lambdas = {"loss_ratio": float("nan")}` → `/save` returns 422 naming
   `loss_ratio`; no file written.
3. `tests/test_optimiser_apply_artifacts.py::test_apply_rejects_nonfinite_artifact_lambda` —
   hand-written artifact JSON with `NaN` lambda → `_dispatch_apply` raises naming the key.
4. `tests/test_optimiser_io.py::test_load_rejects_unknown_schema_version` —
   `{"schema_version": 999}` → raises naming the max supported version.
5. `tests/test_optimiser_io.py::test_load_accepts_legacy_artifact_without_schema_version` —
   pre-existing artifact shape loads fine (backward compat).
6. `tests/test_optimiser_apply.py::test_apply_and_trace_agree_on_missing_lambdas` and
   `::test_blank_mode_rejected_by_apply` — both paths raise, same key named.

## Acceptance
- Kill-mid-save can never destroy the previous artifact (atomic replace).
- Non-finite lambdas/objective cannot be persisted or applied; errors name the key.
- Schema-versioned artifacts; unknown versions rejected loudly; legacy artifacts still load.
- Apply and trace enforce identical artifact strictness.
- Full `test_optimiser_io.py`, `test_optimiser_apply*.py`, deploy scorer tests pass.
