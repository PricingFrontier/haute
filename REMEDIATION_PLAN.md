# CODE_REVIEW.md remediation tracker

Working through every finding in [`CODE_REVIEW.md`](./CODE_REVIEW.md), in dependency order, as a
sequence of wave PRs. Each item: failing test first, then the fix (one developer + one independent
reviewer per item); each wave ends with a fresh audit team over the whole diff; a cross-wave
holistic review runs after each merge before the next wave starts. Coverage and CI gates are never
lowered. Items the review marked *partially verified* / *needs repro* start with a repro spike —
no repro means the item is closed with a note, not speculatively "fixed".

Severity tags below: **[C]** critical, **[H]** high, **[M]** medium, **[L]** low.

## Delivery

- **W0 + W1** landed on `code-review` → merged via [#22](https://github.com/PricingFrontier/haute/pull/22) (pre-dated the single-PR directive).
- **W2 onward (per Ralph, 2026-06-10): ALL remaining waves accumulate on one branch (`wave-2-cache-integrity`) and one PR ([#23](https://github.com/PricingFrontier/haute/pull/23)). No auto-merge — the PR stays open for Ralph's independent review; merging is his call.** Wave-by-wave commits, per-item dev+reviewer agents, and wave audits continue unchanged.
- **ALGO_VERSION bumps exactly twice**: W1 (3→4, edge handles) and W2 (4→5, encoder unification).

## Wave 0 — Rails

- [x] 0.1 Draft PR `code-review`→`main` (CI matrix baseline) — [#22](https://github.com/PricingFrontier/haute/pull/22)
- [x] 0.2 **[H]** e2e reset guard: assert `rev-parse --show-toplevel` == e2e root + `GIT_CEILING_DIRECTORIES` (+ `GIT_DIR`/`GIT_WORK_TREE` stripped) before destructive git ops — `ea2bacdc`
- [x] 0.3 **[H]** `tests/test_file_ops.py` on platform-smoke CI selection — `4bd18ffa`, CI-verified on windows-latest
- [x] 0.4 Baseline both preflight legs + e2e green; fix pre-existing failures; snapshot coverage
  - [x] backend leg green local (9842 passed, 91.09%, 18 critical gates) + CI ×3 Python; coverage snapshotted
  - [x] frontend leg: `useEdgeHandlers.ts` critical gate fixed with 14 behavioral tests — `4f011cb8`; CI green
  - [x] local e2e: Windows-deterministic stuck-preview defect fixed (in-flight previews terminalize on mid-flight `structuralVersion` drift) — `11c06dbc`; 15/15 e2e local, CI run 27275208625
  - [x] W0 wave audit: pass (sole finding was this tracker line)
- [x] 0.5 This tracker committed — `fdb45da2`

## Wave 1 — Edge-join / multi-port blockers (on `code-review`)

- [x] 1.1 **[C1]** Edge-join codegen wrote raw frontend node ids → reload failed; remapped to role-ordered sanitized names + round-trip test (id ≠ sanitized(label)); submodel-boundary variant fixed too — `0765ba6f`
- [x] 1.2 **[C3]** Edge handles in graph fingerprint (JSON-array serialization); ALGO_VERSION 3→4; class audit: single digest site; executor-level stale-serve proof — `91c8d9d5`
- [x] 1.3 **[H]** Port rename: contract is handle==raw label end-to-end, so labels commit atomically on blur/Enter and the same commit migrates edges (guarded) then reconciles — edges survive renames — `74b8561f`
- [x] 1.4 **[H]** `port_N`/`label__N` synthesis removed; blank/duplicate/sanitised-collision labels surface inline validation (astral code-point parity incl.) and render no handle; empty-path commit no longer silently drops tables — `74b8561f`
- [x] 1.5 **[H]** apiInput path inputs keep focus; paths commit on blur (PathInput); silent readV2 table-drop window closed — `f76de368`
- [x] 1.6 **[H]** One undo entry per committed rename (rebind via setEdgesRaw inside the setNodes snapshot); deterministic pin — closed by 1.3 construction — `74b8561f`
- [x] 1.7 **[M]** Edge-join right-parent trace correlation returned decoy rows (left-value poisoning); suffix-aware mapping from build_edge_join_kwargs; first 12 edge-join trace tests — `1ef97d32`
- [x] 1.8 **[H]** Runtime join-semantics matrix 27→76 tests: how×on/leftOn-rightOn/coalesce/validate/suffix/dtype through both production surfaces; no runtime bugs found — `e32fd90b`
- [x] 1.9 **[H, found in review]** Column-name inputs: blank commit silently dropped the row via readV2; now CommittedTextInput with blank+per-table-duplicate validation mirroring `_api_input_schema.py:328-343` — `74b8561f`

W1 notes for later waves: preview `_columns` enrichment pushes its own history entries after any config commit (live-confirmed twice; belongs to item 7.3's preview-setter rework). Tracker item 9.3 (apiInputPorts tautological test) was completed early inside 1.3/1.4.

## Wave 2 — Cache, fingerprint & JSON-cache integrity

- [x] 2.1 **[C2]** `committed/` mirror never populated → route marks working-consulted on successful build only; HTTP build→save→fresh-session→committed-serve test — `7db05482`
- [x] 2.2 **[C4]** Preview keys sign every file-backed input via `runtime_input_extra_keys` (single source, §A2 seed; stat-gated memo; incl. flat-file apiInput + databricks table cache — both review-caught) — `57153a92`
- [x] 2.3 **[C4/M]** Trace key + both preview-key reconstructions share the same extras (latent bug fixed: trace's rebuild omitted the cache-state signature, so apiInput preview reuse always missed) — `57153a92`
  - W2 notes: sink-side `dataframe_graph_input_fingerprint` still doesn't sign the databricks table cache (pre-existing, outside C4's preview/trace scope — candidate for W8a or A2 consolidation)
- [x] 2.4 **[H]** Validity records data-file signature {size, mtime_ns, sha256} with stat fast-path + hash arbitration — `7db05482`
- [x] 2.5 **[H]** One shared `table_is_emitting` predicate across shred/build/validity/load; wedge gone — `7db05482`
- [x] 2.6 **[M]** Atomic whole-dir swap (win32 failure paths pinned) + per-cache-dir build serialization — `7db05482`
- [x] 2.7 **[H]** ShredSkipStats counted + surfaced (summary, meta, both route responses, warning log; conservation property) — `7db05482`
- [x] 2.8 **[H]** Date columns reject ints/bools loudly (epoch-day hazard named); floats fail at strict Series build — `7db05482`
  - Follow-ups (LOW, from review): pin float-in-date rejection with one test; surface skip counts in the cache UI (→ W7 panels)
- [x] 2.9 **[M]** Trace cache byte-bounded with the preview cache's exact wiring (estimator reused, LRU/oversized policy mirrored, env knob) — `b37f71fb`
- [x] 2.10 **[M]** Store-pinned window: the artifact being stored is never eviction-eligible mid-run; settle exception-safe against Windows unlink PermissionError (review-caught deadlock hazard) — `37bd7b77`
- [x] 2.11 **[M]** Chunk whitelist: every entry carries a chunked==full hypothesis proof enforced by a bidirectional meta-test; fill_null restricted to literal values, is_in to literal collections, composite frame refs de-whitelisted as a class — `38f13d91`
- [x] 2.12 **[M]** Projection respects renames: ordered backward demand propagation, full-width safe fallback, rename-free path byte-identical — `edf2d254`
- [x] 2.12b **[M, found in 2.12]** Rename-free derived-reference demand re-add fixed via `_references_derived_column` dispatch to ordered propagation; unprovable shapes deliberately keep today's loud over-demand (pinned) — `1369a7f3`
- [x] 2.12c **[M, found in 2.12b review]** Select demands inputs of every select output (the union walk modeled a prune the node never performs); select_seq twinned; un-aliased with_columns regression caught in review and remediated with the prescribed bail — `9ca600ba`
- [x] 2.13 **[L→here]** One `canonical_json` for all digest material (8 divergences found incl. set-ordering splits); `_normalise_execution_policy` deleted; ALGO_VERSION 4→5; dfexec version stays 1 (evidence pinned) — `a05911d3`

## Wave 3a — Rating / metrics / trace-number correctness

- [x] 3a.1 **[C6]** Tie-aware gini + Lorenz (single chord-per-tie-group helper, bit-exact permutation invariance, sklearn AUC oracle); degenerate artifacts corrected (constant/single-row/constant-target → 0) — `a7042c8a`
- [x] 3a.2 **[C8]** Waterfall derives from observed values with two live reconciliation invariants; classifier labeling-only; tests through execute_trace — `5cdf6978`
- [x] 3a.3 **[H]** Rating misses raise RatingTableMissError (lazy/streaming-safe guard); `onMissing: neutral` explicit opt-in with counted, logged misses; junk defaults raise — **breaking** — `9222236a`
- [x] 3a.4 **[H]** `normalise_rating_key` + expression twin on both join sides; `_trace_enrichment` + sidecar compaction share it; engine/trace agreement pinned end-to-end — `9222236a`
- [x] 3a.5 **[H]** Banding codegen emits real `apply_banding_from_config` body (shared executor loop); cross-surface equality for all rule kinds incl. rightClosed=False — `9222236a`
  - W3a notes: WaterfallChart hardcodes the × glyph (→ 7.10/7.11); decorator-display `tables=` repr omits `onMissing` (cosmetic, sidecar authoritative); contract-kwarg quoting divergence on re-save is pre-existing cross-node (LOW); `compute_lorenz_curve` at `_training_job.py:1140` gets unfiltered preds (→ W4b)

## Wave 3b — Optimiser correctness

- [x] 3b.1 **[C7]** Non-finite contract in `_validate_and_project` at the solver's Float32 precision, one fused scan, names columns + counts; solver-never-invoked spied (RED proved the library converges on NaN with wrong totals) — `2774a217`
- [x] 3b.2 **[H]** Composite groups apply via multi-column join (price-contour's `\x1f` level format is self-describing); malformed shapes raise named errors — `7c967300`
- [x] 3b.3 **[H]** Ratebook Load-detail had no real backend (phantom `.dataframe`, full re-solve then 500) → clean 422 naming what IS available; save/frontier-select pinned working in a 16-test zero-mock real-library module — `c737a0b0`
- [x] 3b.4 **[M]** Single-quote `std()` crash → explicit n==1 → 0.0 (schema/guards-grounded); real-solver lifecycle pinned — `2774a217`
- [x] 3b.5 **[M]** Unseen levels: loud-neutral via W3a `onMissing` machinery (counted WARNINGs, per-row `unseen` flag); explainability mirror on `normalise_rating_key` — `7c967300`
- [x] 3b.6 **[M]** Frontier limit aligned to the verified 10k cap; 422 before the solver naming both numbers; live signature pin against library drift — `c737a0b0`
- [x] 3b.7 **[M]** `/estimate`: one projected quote-id scan + honest docstrings; spies pin route==1/service==0 — `c737a0b0`
- [x] 3b.8 **[M]** Covered distributively: real-solver tests in 3b.1/3b.2/3b.3 replaced or backstopped every drifted mock in their lanes
- [x] 3b.9 **[M, found in review]** Seven phantom-`dataframe` ratebook mocks aligned to the real field set (seventh review-caught); absence pin drives `_solve_ratebook` so the divergence cannot return — `59d15bcd`
- [x] 3b.10 **[M, found in review]** Levels canonicalised at save on both sides (typed counts + fewest-collapsed label resolution; uniqueness proof validated adversarially against the real solver); float factor columns now round-trip zero-miss e2e — `59d15bcd`
  - New follow-up (cosmetic): numeric banding assignment-order entries store verbatim `str()` vs canonical row keys → configured-order lookup falls to insertion order for that edge (pre-change apply was broken there anyway)
  - W3b handoffs: aggregate `unseen` count in the apply/score response (route surface); composite `input_value` renders "[object Object]" in OptimiserApplyDetail (→ W7); artifact join-column metadata for true self-description (future); solve-time auto-frontier bypasses the new budget gate (non-fatal lane, library names both numbers — acceptable)

## Wave 4a — Scoring / serving correctness

- [x] 4a.1 **[H]** Pyfunc gets a named pandas frame with native dtypes (real-mlflow repro: named signatures hard-reject numpy); numpy fast path = catboost-no-cats only; 13-test real-pyfunc module — `85bfd837`
- [x] 4a.2 **[H]** Eager scoring collects once (proven 2×/3× upstream execution + wrong-rows divergence at HEAD); structural alignment; batch path pinned — `94cfcdff`
- [x] 4a.3 **[H]** Deploy scorer caches model+contract via new shared `StatGatedCache` (single-flight, stat-gated, 100/100 covered); second per-request contract read found in the planner and consolidated — `94cfcdff`
- [x] 4a.4 **[H]** SHAP repro HELD (Poisson/Tweedie default predict = Exponent vs raw SHAP); additivity always checks RawFormulaVal (catboost's own dispatch rule verbatim); ladder labeled raw-space — `69751915`
- [x] 4a.5 **[M]** Multiclass proba shape dispatch (a class-2 P=0.75 prediction reported 0.20); k≥3 fails loud — BREAKING; eager parity via shared helper — `85bfd837` + `0def6cc6` (4a.9)
- [x] 4a.6 **[M]** Float32 cast dropped for pyfunc (silently destroyed precision); int64-vs-double now fails with mlflow's own message — BREAKING — `85bfd837`
- [x] 4a.7 **[M]** Per-artifact single-flight download/load locks (W2.10 pattern); deploy bundler inherits — `85bfd837`
- [x] 4a.8 **[M]** Databricks: retry truncation → FetchIntegrityError via connector rownumber; unique tmp + atomic replace (+ latent `_cache_path_for` race fixed); zero-row caches the real terminator schema — `69751915`
- [x] 4a.9 **[H, found in 4a.1]** Eager k≥3 guard shares the batch shape dispatch (one helper, byte-equal errors) — `0def6cc6`

## Wave 4b — Training lifecycle

- [x] 4b.1 **[H]** GLM config keys no longer merged into CatBoost params (`routes/_train_service.py:465`)
- [x] 4b.2 **[H]** GLM export shares one config→kwargs builder with `_train_service` (`modelling/_export.py:36`)
- [x] 4b.3 **[H]** Fabricated SE=0.0/p=1.0 eliminated (both vectors incl. per-index padding); typed GLMInferenceUnavailableError + diagnostics error; table omitted (frontend guards hard-reject per-row absent/null stats); estimates remain via relativities — `6702a4de`
- [x] 4b.4 **[M]** `head(N)` downsample → seeded sample (`_train_service.py:880`) — release note
- [x] 4b.5 **[M]** Null dates fail temporal splits loud (HEAD had three inconsistent silent behaviors incl. leakage-direction routing) — BREAKING — `6702a4de`
- [x] 4b.6 **[M]** Owned temp parquets cleaned in finally on failure/cancel (7 RED scenarios); caller-owned inputs never deleted; + the W3a Lorenz handoff (finite mask extended to weights at the call site) — `5758e35e`
- [x] 4b.7 **[M]** GPU cancel: bounded join → rmtree only after a dead worker; zombie path retains the dir loudly; callback-exception swallow fixed — `5758e35e`
- [x] 4b.8 **[M]** MLflow log button: correct signature + GLM artifacts (`routes/modelling.py:318`)
- [x] 4b.9 **[M]** Per-model `{name}.feature_contract.json` (two models in one dir overwrote each other's contracts → wrong-contract serving); shared name dropped outright on bundler evidence; legacy file warned, never trusted — `5758e35e`
- [x] 4b.10 **[M]** PDP failures surfaced (`_metrics.py:759`)
- [x] 4b.11 **[M]** Non-finite metric-row filtering counted + surfaced (`_metrics.py:34`)

## Wave 5 — Codegen/parser round-trip safety

- [x] 5.1 **[C5]** AST-based `_unwrap_chain_assignment` (`_code_extraction.py:252`) — `3016dacf`
- [x] 5.2 **[M]** Idempotent brace sanitization (`_codegen_builders.py:178`) — `d216be87`
- [x] 5.3 **[M]** Escape pipeline name in module docstring (`codegen.py:660`) — `d216be87`
- [x] 5.4 **[M]** String-aware paren scanner (`codegen.py:197`) — `d216be87`
- [x] 5.5 **[M]** Non-literal decorator kwargs: serialize or reject, never `ast.dump` (`_ast_helpers.py:48`) — `d07f1f09`
- [x] 5.6 **[M]** Preserve external-file imports (`_code_extraction.py:544`) — `3016dacf`
- [x] 5.7 **[M]** Regex fallback handles multi-arg `connect()` (`_parser_regex.py:42`) — `d07f1f09`, audit fixes `b5ee605e` + `b4a47324`, gate follow-up `eca9cf02`
- [x] 5.8 **Capstone**: corpus + hypothesis round-trip property test (`tests/test_codegen_roundtrip_property.py`) — parse∘codegen semantic identity + byte-idempotence with adversarial strings — `255b2877`, formatter follow-up `eca9cf02`

## Wave 6 — Work-loss protection: git + live sync

- [x] 6.1 **[C9]** `revert_to` auto-commits/stashes before tag+reset; `delete_branch` backup tag before `-D` (`_git.py:649,806`) — `c8ad245b`
- [x] 6.2 **[C10]** Frontend: filter foreign `source_file`, identity check, dirty-state banner, reconnect resync; watcher parses only discovery-positive files (`useWebSocketSync.ts:101`, `server.py:356`) — `8b6dc6fa`
- [x] 6.3 **[M]** Push failures surfaced + `pushed` field (`_git.py:546+`) — `c8ad245b`
- [x] 6.4 **[M]** Protected branches enforced server-side, configurable (`_git.py:37`) — `c8ad245b`
- [x] 6.5 **[H]** Submodel drill-in save gated/redirected; no silent truncation (`useSubmodelNavigation.ts`, `usePipelineAPI.ts:581`) — `8b6dc6fa`
- [x] 6.6 **[H]** Ctrl+Z/Escape and graph shortcuts gated by `isTyping` (`useKeyboardShortcuts.ts:49`) — `8b6dc6fa`
- [x] 6.7 **[M]** git subprocess `encoding="utf-8"` + platform-smoke test (`_git.py:122`) — `c8ad245b`
- [x] 6.8 **[M]** `.gitignore` exact-line check (`cli/_init_cmd.py:497`) — `c8ad245b`
- [x] 6.9 ~~`haute init` root `main.py` deletion~~ — **by design** (uv init stub; pipeline `main.py` lives in the rating folder). Existing scaffold tests assert `rating/main.py` is created (`tests/test_cli.py`, `tests/test_cli_init.py`, `tests/test_starter_pipeline_e2e.py`)

## Wave 7 — Boundary fidelity & UI trust

- [ ] 7.1 **[H]** Int64 |v|>2^53 → JSON string; trace row-match + frontend display same PR (`_json_safe.py:16`, `trace.py:227`)
- [ ] 7.2 **[H]** NaN/±inf → explicit sentinels, distinct from null end-to-end (`_json_safe.py:14`)
- [ ] 7.3 **[H]** Preview results via raw setter; `_`-keys out of persisted fingerprint (`usePipelineAPI.ts:390,442,562`)
- [ ] 7.4 **[H]** Timeout aborts → typed timeout error (`api/client.ts:266`, `usePipelineAPI.ts:449`)
- [ ] 7.5 **[H]** ScenarioExpander min/max commit-on-blur (`ScenarioExpanderEditor.tsx:130`)
- [ ] 7.6 **[H]** TwoWayGrid paste failure toasts offending cell (`rating/TwoWayGrid.tsx:131`)
- [ ] 7.7 **[H]** Trace conditional uses backend taken-branch index (`CalculationHero.tsx:462`)
- [ ] 7.8 **[M]** Trace correlation surfaces duplicate/relaxed-match ambiguity (`_trace_correlation.py:203`)
- [ ] 7.9 **[M]** Relevance pruning keeps branches feeding later modifications (`trace.py:794`)
- [ ] 7.10 **[M]** Full-precision affordance in trace panels (`StepCard.tsx:21`)
- [ ] 7.11 **[M]** Error toasts persist; cache-status error ≠ "not cached"; root ErrorBoundary; LossTab guard (`Toast.tsx:31`, `CacheFetchButton.tsx:94`, `main.tsx`, `LossTab.tsx:70`)
- [ ] 7.12 **[M, found in W4b review]** `diagnostics_errors` parses in guards but renders NOWHERE for any diagnostic (glm_coefficients, pdp, shap) — a failed diagnostic just silently vanishes from the UI; surface the entries (Summary tab or per-tab notice). Also: guards strip the new per-feature `error`/`error_type` keys from `pdp_data` rows, so a failed PDP feature shows as empty with no reason — surface those too

## Wave 8a — Server robustness + deploy

- [ ] 8a.1 **[M]** Save/submodel/`/infer` off the event loop; `sample_size` bounds I/O (`routes/pipeline.py:341`, `json_cache.py:447`)
- [ ] 8a.2 **[M]** WS send stall closes socket instead of permanent mute (`routes/_helpers.py:375`)
- [ ] 8a.3 **[M]** Submodel routes use save allowlist + rollback (`submodel.py:90`)
- [ ] 8a.4 **[M]** Preview 504 releases admission only when thread ends (`pipeline.py:666`)
- [ ] 8a.5 **[M]** Job store evicts stuck "running" jobs (`_job_store.py:94`)
- [ ] 8a.6 **[M/L]** RAM estimate: strings + join columns; log 4GiB fallback (`_ram_estimate.py:350,101`)
- [ ] 8a.7 **[M]** Pin haute/polars/fastapi in generated Dockerfile (`deploy/_container.py:448`)
- [ ] 8a.8 **[M]** Build the advertised expected-output/tolerance deploy validation (`deploy/_validators.py`)
- [ ] 8a.9 **[M]** Impact compares predictions, not envelopes; zero-baseline % guard (`deploy/_impact.py:147,188`)
- [ ] 8a.10 **[M]** Single deploy validation + shipment-resolution path (`cli/_deploy.py:132`)
- [ ] 8a.11 **[M]** Smoke exits non-zero on unsupported transports (`cli/_smoke.py:77`) — release note

## Wave 8b — Security bundle (isolated PR)

- [ ] 8b.1 Endpoint-level cross-origin repro tests for `/preview`/`/trace`/`/sink`
- [ ] 8b.2 **[H]** TrustedHost localhost allowlist; per-session token on `/api/*` + WS; WS Origin check before accept (`server.py:215`)
- [ ] 8b.3 **[H]** Sink path confinement via existing `validate_safe_path` (`executor.py:1418`, helper at `routes/_helpers.py:78`)
- [ ] 8b.4 e2e token wiring same PR; documented escape hatch; release note

## Wave 9 — Maintainability

- [ ] 9.1 **[L]** Delete dead code: `_build_input_kwargs` machinery, `save_node_config`
- [ ] 9.2 **[L]** RestrictedUnpickler dot-anchoring + tighter allowlist; honest `_sandbox` docstring (`_sandbox.py:346`)
- [ ] 9.3 **[L]** Replace tautological test (`apiInputPorts.test.ts:172`)
- [ ] 9.4 **[L]** Move planning docs out of the mkdocs tree (`docs/EDGE_JOIN_*.md` + `*_PLAN.md`)
- [ ] 9.5 **[L]** Ruff B/PT/S incrementally; mypy strictness step-up
- [ ] 9.6 **[L]** Cheap per-PR frontend benchmark gate
- [ ] 9.7 **[L]** Shared chart scaffold across modelling tabs

## Dropped

- Model-card "Holdout" mislabel — already fixed; tests pin it (review verification correction).
- `_graph_utils` vs `graph_utils` — deliberate facade (review verification correction).
- `haute init` root `main.py` deletion — by design: uv init stub removed; pipeline `main.py` lives in the rating folder.

## PR log

| Wave | PR | Status |
|---|---|---|
| W0+W1 | [#22](https://github.com/PricingFrontier/haute/pull/22) `code-review` → `main` | **merged** 2026-06-10 (full CI matrix green; both wave audits pass) |
| W2 → W9 | [#23](https://github.com/PricingFrontier/haute/pull/23) `wave-2-cache-integrity` → `main` | open — single accumulating PR, awaiting Ralph's independent review; no auto-merge |

**Wave status (2026-06-11):** W2/W3/W4 complete (audits PASS; W4 gate 10,568 tests / 91.53% / 18 critical). **W5 complete:** extraction (`3016dacf`), emission safety (`d216be87`), parser fail-loud helpers (`d07f1f09`), capstone property (`255b2877`), regex-decorator audit fix (`b5ee605e`), preflight contract follow-up (`eca9cf02`), regex fallback parity closeout (`b4a47324`), and Windows hygiene gate hardening (`a49077b3`). W5 review trail: Darwin APPROVE (extraction), Lagrange CHANGES→fixed (helpers), Goodall CHANGES→fixed (capstone), Parfit/Hilbert CHANGES→fixed (wave audits), Volta/Beauvoir/Rawls APPROVE (audit/gate/parity follow-ups), Faraday CHANGES→fixed→APPROVE (gate hardening). Backend gate: `scripts/preflight.ps1 --backend-only` PASS — Ruff, format, mypy, collection, 10,767 tests / 91.63% coverage / 18 critical gates, package build. **W6 complete:** git safety (`c8ad245b`) and live-sync/work-loss guards (`8b6dc6fa`). W6 review trail: Gibbs/Herschel CHANGES→fixed→APPROVE (git safety), Avicenna implementation + Gauss/Franklin CHANGES→fixed→APPROVE (live sync). W6 local gates: 122 focused frontend tests, frontend lint/typecheck, `tests/test_server.py` 101 passed / 1 skipped, ruff check/format, `git diff --check`. Next: W7. Review protocol: the calibrated split — full pairs for W5/W6 + CRITICAL/HIGH; batch review for mechanical W7/W8a/W9; verbatim-prescription fix-ups orchestrator-verified.

## Behavior-change log (release notes)

| Wave | Change |
|---|---|
| W1/W2 | One-time cache invalidation on upgrade (ALGO_VERSION 3→4, 4→5) |
| W3a | Rating neutral-fill default flips to fail-loud (`onMissing` opt-in); old sidecars persisted from numeric float keys under the previous str() compactor now miss loudly with remediations; waterfall arithmetic corrected (regulator-facing numbers change); tied-prediction gini scores change (tie-corrected), degenerate cases now 0 |
| W3b | Non-finite optimiser inputs rejected naming the column (the solver previously "converged" on NaN with wrong totals); ratebook apply/Load-detail returns 422 instead of phantom 500s; composite factor groups now APPLY (previously ColumnNotFoundError at deploy); unseen factor levels rate loud-neutral (counted warnings + per-row `unseen` flag); **float-typed factor columns now apply real solver factors — APPLIED PRICES CHANGE for affected pipelines (previously every row rated neutral 1.0)**; single-quote solves no longer crash post-convergence; over-budget frontier requests fail 422 naming both numbers |
| W4a | **BREAKING**: multiclass (k≥3) `predict_proba` now raises on BOTH batch and eager surfaces instead of silently mislabeling P(class@1) as the positive probability; **BREAKING**: pyfunc int64 features vs a double signature now fail with mlflow's own message (previously masked by a precision-destroying Float32 cast); named-signature pyfunc models (sklearn/LightGBM/XGBoost via infer_signature) previously could not score — now work with native dtypes; SHAP ladders for Poisson/Tweedie models display raw (log-space) contributions labeled `output_space=raw_formula_val`; eager scoring no longer re-executes upstream (wrong-rows hazard eliminated); deployed scorers load each model artifact once per change instead of per quote |
| W4b | RAM/row-limited training now trains on a deterministic seeded random sample (seed 42) instead of the oldest `head(N)` slice — **downsampled training results change**; CatBoost workflows with a top-level `offset` (log-exposure frequency) no longer crash at fit; exported GLM scripts now train the configured family/link/terms model (previously a silent Gaussian all-features model) and carry feature_columns/fold_column/id_columns/categorical_levels; fabricated GLM SE/p no longer rendered (coefficients table omitted with a diagnostics error when inference stats are unavailable; estimates remain via relativities); **temporal splits now fail loud on null dates** (previously: mask path silently routed nulls into validation — the leakage direction; split path silently dropped them; holdout crashed opaquely); "Log to MLflow" button logs the model's true signature (from its feature contract — previously every feature was signed `double`, so reloaded models with categorical/int features could not score) plus the previously-dropped GLM diagnostics artifacts and aic/bic/deviance metrics, and errors loudly when the model has no contract; PDP per-feature failures now appear in `pdp_data` as named entries with `error`/`error_type` (all-features-failed = diagnostics error); metrics payloads gain `non_finite_rows_filtered` when rows were dropped, and an all-non-finite evaluation set now fails the training job instead of returning NaN metrics |
| W5 | Codegen/parser save-load now fails loud for unresolved decorator kwargs, `pipeline.connect()` arguments, malformed visible fallback decorators, and unresolved pipeline metadata instead of serializing `ast.dump` strings or silently dropping nodes/edges; regex fallback now preserves multi-arg/chained/keyword `connect()` calls, top-level parenthesized connect expressions, escaped metadata, and `Contract(...)` declarations while rejecting nested/disabled connect calls and computed kwargs; chain-assignment code extraction preserves statement form and external-file imports; generated source is guarded by corpus + Hypothesis parse∘codegen semantic identity and byte-idempotence properties with adversarial braces, quotes, docstrings, and path-like strings |
| W6 | Auto-backup commits/tags in history; protected-branch ops rejected server-side; push failures surface loudly with `pushed`/`push_error`; live sync filters foreign `source_file`, resyncs on reconnect, and blocks external graph application while the canvas is dirty |
| W7 | JSON payloads: big ints as strings, NaN/inf as sentinels (consumer-visible) |
| W8a/b | Smoke non-zero exit on unsupported transports; localhost session token (escape hatch documented); pinned Dockerfile |
