# Roadmap stock-take — 2026-07-27

A 14-agent verification pass checked every package in `specs/roadmap/` against
`HEAD` (branch `roadmap-stocktake`, cut from `1fef4b03`): does the stated
problem still exist in the code, and are the acceptance criteria met by tests?
The root session spot-checked the load-bearing verdicts directly in the code.
Verification was static (code and test inspection, no suite execution); CI is
treated as the authority that existing tests pass.

Of 72 packages across 16 component docs (caching and git-integration were
already empty): **37 retired as delivered or superseded**, **31 with real
remaining work**, **3 open product decisions**, **1 needing re-scoping**
(OPT-P10). The component docs in `specs/roadmap/` were updated in this pass and
remain the source of truth for package definitions; this file is the dated
execution queue derived from that stock-take. Delete it once the queue is
consumed or re-planned.

## 1. Implement next (recommended order)

Silent-wrongness and user-facing correctness first, per `AGENTS.md`. Effort:
S ≤ half a day, M = 1–3 days, L > 3 days.

| # | Package | Change | Effort |
|---|---|---|---|
| 1 | [MOD-M07](specs/roadmap/modelling.md#mod-m07--workflow-ux) | Truthful GPU-VRAM copy, cancel wiring, 507 handling | S |
| 2 | [AUD-C10](specs/roadmap/optimiser.md#aud-c10--numerical-and-silent-failure-residuals) | Origin-aware solver error classification | S–M |
| 3 | [OPT-D01](specs/roadmap/optimiser.md#opt-d01--generic-setup-error-detail-policy) | Error-detail decision record, then sanitize/classify | S |
| 4 | [AUD-DEPLOY-01](specs/roadmap/deploy-platform.md#aud-deploy-01--deployment-path-and-scaffold-integrity) | Close remaining deploy-gate integrity holes | M |
| 5 | [CANVAS-STATE-01](specs/roadmap/frontend-canvas.md#canvas-state-01--deliberate-graph-seeding-lifecycle) | Atomic graph-load action with history reset | M |
| 6 | [RATE-01](specs/roadmap/rating.md#rate-01--consistent-malformed-config-rejection) | Consistent malformed-config rejection | S |
| 7 | [EXEC-01](specs/roadmap/execution-engine.md#exec-01--symmetric-eager-mismatch-propagation) | Symmetric mismatch propagation in preview | S |
| 8 | [AUD-C14](specs/roadmap/modelling.md#aud-c14--trainscore-and-mlflow-residuals) | MLflow Date/Datetime/Decimal signature mapping | S |
| 9 | [IO-JSON-01](specs/roadmap/io-layer.md#io-json-01--closed-v2-json-input-schema) | Validate `emit`/`selected`/`status` types | S |
| 10 | [ASSIST-03](specs/roadmap/assistant.md#assist-03--closed-assistant-configuration) | Closed `[assistant]` config with URL validation | S |

Verified details per item:

1. **MOD-M07** — `frontend/src/panels/modelling/TrainingActionsAndResults.tsx:180`
   says "Training will fall back to CPU automatically", but the backend refuses
   the job with HTTP 507 `gpu_vram_limit`
   (`src/haute/routes/_train_service.py:161-177`) and never retries on CPU.
   Rewrite the warning to request a user-selected CPU retry, add a Cancel
   control posting to the existing `train/cancel/{job_id}` route
   (`src/haute/routes/modelling.py:123`), and handle the 507 response with the
   same actionable message. No frontend code currently references
   `train/cancel` or `gpu_vram_limit`.
2. **AUD-C10** — `src/haute/routes/_optimiser_service.py:5040-5060` still maps
   `type(exc)` → category (`ValueError` → data/contract error), so an internal
   solver defect raising `ValueError` is reported as a user data error. Add
   typed boundary errors (or origin tagging at the solver call sites) so the
   terminal category reflects origin; keep the delivered
   `PUBLIC_CONTRACT_ERROR_TYPES` layer in front.
3. **OPT-D01** — decision record first: grid construction sends raw exception
   text to the client (`_optimiser_service.py:4881`,
   `HTTPException(400, detail=f"Grid construction failed: {exc}")`) while
   pipeline setup hides it; artifact loaders return 500 for both missing
   (possibly TTL-evicted, user-shaped) and corrupt artifacts
   (`_optimiser_service.py:1321-1395`). Decide the vocabulary, then align both.
4. **AUD-DEPLOY-01** — remaining gaps: `src/haute/deploy/_bundler.py:266-286`
   `_resolve_path` lacks project-root/traversal enforcement (mirror
   `routes/pipeline.py:155-194`); `src/haute/deploy/_validators.py:339-341`
   silently skips quote validation when the configured directory is missing or
   empty (must raise `DeployError`); `config.output_fields` is never checked
   against `resolved.output_schema` pre-deploy; `src/haute/cli/_deploy.py:233`
   catches all exceptions in one branch (distinguish user errors from
   implementation defects); the single-source deploy-input fallback in
   `src/haute/deploy/_config.py:560-573` accepts any node type.
5. **CANVAS-STATE-01** — `frontend/src/stores/useGraphStore.ts:644`
   `setNodesRaw`/`setEdgesRaw` never touch `undoStack`/`redoStack`, and
   `App.tsx:216` mounts with empty arrays, so undo after a pipeline load/switch
   can restore the previous graph. Add one graph-load action that installs
   nodes/edges and resets history/fingerprints atomically; route initial mount
   and every load/switch through it, with component tests for load-after-empty
   mount, switch, remount, undo, redo.
6. **RATE-01** — `src/haute/_banding_config.py:92-100` returns `[]` for
   non-list `factors` while the sidecar path raises `ValueError` for the same
   input, and `tests/test_rating.py:101` pins the silent behaviour as correct;
   `src/haute/_rating.py:819-851` silently no-ops a populated table with empty
   factors while `_rating_step_config.py:141` raises. Unify on typed rejection,
   invert the pinning test, add generated-vs-executor parity tests.
7. **EXEC-01** — base `SchemaMismatchError` (join-key dtype mismatch,
   `src/haute/_execute_lazy.py:523`) has no `error_code` and is not in
   `PUBLIC_CONTRACT_ERROR_TYPES` (`src/haute/routes/_contract_errors.py:30-42`),
   so preview swallow-mode downgrades it to a generic per-node failure while
   `ContractMismatchError` propagates. Re-raise both identically at the eager
   boundary and add a `swallow_errors=True` test.
8. **AUD-C14 (remainder)** — `src/haute/modelling/_signature.py:14-27`
   `_POLARS_TO_MLFLOW` maps only Int64/Float64/String/Boolean and raises for
   Date/Datetime/Decimal, so pipelines with date or decimal features cannot be
   MLflow-logged at all. Add the mappings plus signature/log tests. The
   classification-parity and pyfunc-loader halves are already delivered.
9. **IO-JSON-01** — `src/haute/_api_input_schema.py` `validate_v2_schema` never
   checks `emit`/`selected`/`status`; `src/haute/_json_shred.py:162,173`
   coerces them with `bool(...)`, so `"true"` (string) silently passes. Add
   typed validation (bool, bool, Confirmed|Inferred) with field-path errors and
   negative fixtures.
10. **ASSIST-03** — `src/haute/assistant/_config.py:180-228` ignores unknown
    `[assistant]` keys and accepts any string as `base_url`. Add an allow-list
    rejection naming the TOML path and http/https URL validation.

Cheap adjacent wins to batch with the above: delete `frontend/bun.lock` or add
a parity check (AUD-QUALITY-03); add `graph_fingerprint` to the
`GraphUpdatePayload` TypedDict in `src/haute/_event_bus.py:83-88` — the
publisher already sends it (`src/haute/server.py:801-808`) (part of AUD-C20).

## 2. Schedule deliberately (larger or lower urgency)

- **AUD-C20** (L) — cgroup v2/v1 memory clamping in
  `src/haute/_ram_estimate.py` (container OOM risk when host RAM exceeds the
  cgroup limit); bounded `os.replace` retry for Windows contention in
  `_train_service.py:1050-1077`.
- **ASSIST-01** (M) — resume-order fix in `routes/assistant.py:208-214`
  (lookup touches the LRU before the pipeline match check) and durable
  graph-update transcript entries.
- **SEC-ENV-01** (M) — AST-based env-accessor guard; live example of the gap:
  `routes/pipeline.py:130-142` reads `os.environ` at import time outside
  `haute._env`.
- **EDA-E12** (S) then **EDA-E11** (M) — wire existing export utilities onto
  Explore cards; extend quality profiles.
- **ROAD-TEST-05** (M) — aggregated owner/expiry summary for skips, xfails,
  flakes, and mutation survivors.
- **OPT-P11 → OPT-P12/P13 → OPT-P14** (M/L each) — staged decomposition of the
  5,150-line `_optimiser_service.py`; behaviour-preserving, do between feature
  waves.
- **ROAD-CANON-01 residual** (S) — fold the stale approved-change contract in
  `specs/json-shredding/low-level.md:508-515` to present tense and confirm the
  legacy-record `is_error` inference in `routes/assistant.py:158` is current
  schema evolution rather than obsolete-format handling.
- **MOD-M04 CV half** (L) and **EDA-E09/E10** (L) — net-new capability, treat
  as product-scoped features, not audit debt.
- **ROAD-WORKER-04** (L) — correctly deferred; blocked on versioned
  solver-specific persistence for every supported solver.
- **OPT-P06** (M) — bounded parallel frontier computation; performance only.

## 3. Open decisions

- **ASSIST-02** — assistant authoring workflow scope (prompt guidance,
  provider/model selection ownership, recovery UX).
- **MOD-M09** — which capability levers to productise; monotone constraints
  are implemented for both algorithms but not exposed in the node schema.
- **OPT-D01** — error-detail vocabulary (gates queue item 3).

## 4. Low-value residuals — do opportunistically or drop

ROAD-UI-01 per-variant owner column (S); ROAD-UI-04 accessibility-automation
decision record (S); EDA-E08 `role="progressbar"` on the Explore busy
indicator (S); MOD-M05 CatBoost contiguity benchmark decision (S, no-change
outcome permitted); OPT-P10 needs a fresh dead-code inventory before it is
actionable.

## 5. Retired in this pass (37)

- **pipeline-authoring**: AUD-C05, AUD-PIPE-01, AUD-C01 — closed by the
  2026-07-25/26 authoring-contracts commits (`754cfeb4`, `7c7dd6cf`,
  `f4641d79`): `_parser_conservation.py`, registry behavioural-body
  validation, and the codegen equivalence suite.
- **tracing-explainability**: AUD-C07 (incl. folded AUD-TRACE-01), AUD-C08 —
  fail-loud evaluation with Polars parity corpora; row-match state machine
  with surfaced relaxation reasons; structural waterfall membership
  (`4ea5add8`, `1ca273b3`).
- **optimiser**: OPT-P01–OPT-P05, OPT-P07–OPT-P09 — frontier apply, versioned
  artifacts, constraint validation, jobs, interruptibility, single-scan setup,
  stored-summary reuse, artifact lifecycle; all with named regressions.
- **explore-eda**: EDA-E01–EDA-E06 delivered; EDA-E07 and EDA-E13 superseded
  (hide-stale panel design; shared dataframe-cache/job-lifecycle coverage).
- **frontend-canvas**: AUD-C16, AUD-C17, ROAD-UI-02, ROAD-UI-03, ROAD-UI-05 —
  cache identity, fail-closed WS sync, browser journeys, zero-level warnings,
  measured CI lanes (`ea13dff0` wave).
- **modelling**: MOD-M01, MOD-M02, MOD-M03, MOD-M06, MOD-M08 — offset
  lifecycle, loss validation, evaluation correctness, robustness, export
  parity.
- **engineering-quality**: ROAD-TEST-02, ROAD-TEST-03, ROAD-TEST-04,
  AUD-QUALITY-01, AUD-QUALITY-02 delivered; ROAD-TEST-01 retired as superseded
  by the decentralised per-boundary suites plus the docs-accuracy ratchet.

## 6. Verification notes and corrections

Two workflow verdicts were corrected during root review, both in the
optimiser:

- **AUD-C10 was reported done but is partial**: only the typed
  public-contract-error layer landed; the origin-blind `type(exc)` fallback at
  `_optimiser_service.py:5041` remains. It stays active (queue item 2).
- **OPT-P02's acceptance criteria are met**, but the spec note it anchored
  records a still-true residual (missing artifact after TTL returns 500). The
  note now tracks OPT-D01, whose scope was extended to cover artifact-load
  classification.

Root-verified claims (read directly in code): the RATE-01 pinning test, the
EXEC-01 swallow path, the MOD-M07 fallback copy, the OPT-D01 raw-detail
`HTTPException`, the CANVAS-STATE-01 history gap, and both optimiser
corrections above.
