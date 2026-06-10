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

- [ ] 3a.1 **[C6]** Gini + Lorenz tie aggregation; row-permutation property tests (`modelling/_metrics.py:80,625`)
- [ ] 3a.2 **[C8]** Waterfall: delta from consecutive outputs, implied factor, reconciliation assert; tests via `execute_trace` (`_trace_waterfall.py:113`)
- [ ] 3a.3 **[H]** Rating neutral-fill on missing level → default fail-loud, opt-in neutral, miss counters (`_rating.py:359`) — **breaking, release note**
- [ ] 3a.4 **[H]** Float-vs-string factor keys normalized — same PR as `_trace_enrichment.py:131` `str()` mirror (`_rating.py:292`)
- [ ] 3a.5 **[H]** Banding codegen gets a real `apply_banding_from_config` body + standalone-run + parse-back tests (`_codegen_builders.py:334`)

## Wave 3b — Optimiser correctness

- [ ] 3b.1 **[C7]** `_validate_and_project` rejects NaN/inf in objective/constraint/scenario columns, names the column (`_optimiser_service.py:4077`)
- [ ] 3b.2 **[H]** Composite factor groups: multi-column join or save-time reject (`_builders.py:1546`)
- [ ] 3b.3 **[H]** Ratebook apply/detail pinned against real `RatebookResult`; real-solver integration tests; implement detail or gate UI (`routes/optimiser.py:1204,933`)
- [ ] 3b.4 **[M]** Single-quote solve `std()` crash (`_optimiser_service.py:1117`)
- [ ] 3b.5 **[M]** Unseen factor levels rated 1.0 on apply → fail loud (`_builders.py:1565`)
- [ ] 3b.6 **[M]** Frontier budget 100k vs library cap 10k (`_optimiser_limits.py:14`)
- [ ] 3b.7 **[M]** `/estimate` cost vs docstring (`optimiser.py:154`)
- [ ] 3b.8 **[M]** Real-solver numerical/constraint suite replacing drifted mocks

## Wave 4a — Scoring / serving correctness

- [ ] 4a.1 **[H]** Pyfunc named-DataFrame contract; real pyfunc fixture replaces MagicMock (`_mlflow_io.py:897`)
- [ ] 4a.2 **[H]** Eager scoring: collect once, no positional splice over re-executed plan (`_model_scorer.py:524`)
- [ ] 4a.3 **[H]** Deployed scorer caches model+contract by `(path, task)` (`deploy/_scorer.py:578`)
- [ ] 4a.4 **[H]** CatBoost SHAP Poisson/Tweedie repro → `RawFormulaVal` if confirmed (`_model_explainability.py:101`)
- [ ] 4a.5 **[M]** Multiclass `predict_proba` labeling (`_mlflow_io.py:917`)
- [ ] 4a.6 **[M]** Drop unconditional Float32 cast for pyfunc (`_mlflow_io.py:888`)
- [ ] 4a.7 **[M]** Serialize concurrent model download/load (`_mlflow_io.py:530`)
- [ ] 4a.8 **[M]** Databricks: retry truncation fails loud; per-fetch tmp paths; zero-row schema (`_databricks_io.py:284,266,326`)

## Wave 4b — Training lifecycle

- [ ] 4b.1 **[H]** GLM config keys no longer merged into CatBoost params (`routes/_train_service.py:465`)
- [ ] 4b.2 **[H]** GLM export shares one config→kwargs builder with `_train_service` (`modelling/_export.py:36`)
- [ ] 4b.3 **[H]** No fabricated SE/p on fallback — omit + diagnostics error (`modelling/_rustystats.py:332`)
- [ ] 4b.4 **[M]** `head(N)` downsample → seeded sample (`_train_service.py:880`) — release note
- [ ] 4b.5 **[M]** Temporal split: explicit null-date policy (`_split.py:268`)
- [ ] 4b.6 **[M]** Split parquet cleanup on failure/cancel (`_training_job.py:1186`)
- [ ] 4b.7 **[M]** GPU cancel: join fit thread, clean train_dir (`_algorithms.py:466`)
- [ ] 4b.8 **[M]** MLflow log button: correct signature + GLM artifacts (`routes/modelling.py:318`)
- [ ] 4b.9 **[M]** Per-model `feature_contract.json` (`_training_job.py:1263`)
- [ ] 4b.10 **[M]** PDP failures surfaced (`_metrics.py:759`)
- [ ] 4b.11 **[M]** Non-finite metric-row filtering counted + surfaced (`_metrics.py:34`)

## Wave 5 — Codegen/parser round-trip safety

- [ ] 5.1 **[C5]** AST-based `_unwrap_chain_assignment` (`_code_extraction.py:252`)
- [ ] 5.2 **[M]** Idempotent brace sanitization (`_codegen_builders.py:178`)
- [ ] 5.3 **[M]** Escape pipeline name in module docstring (`codegen.py:660`)
- [ ] 5.4 **[M]** String-aware paren scanner (`codegen.py:197`)
- [ ] 5.5 **[M]** Non-literal decorator kwargs: serialize or reject, never `ast.dump` (`_ast_helpers.py:48`)
- [ ] 5.6 **[M]** Preserve external-file imports (`_code_extraction.py:544`)
- [ ] 5.7 **[M]** Regex fallback handles multi-arg `connect()` (`_parser_regex.py:42`)
- [ ] 5.8 **Capstone**: corpus + hypothesis round-trip property test (`tests/test_codegen_roundtrip_property.py`) — parse∘codegen semantic identity + byte-idempotence with adversarial strings

## Wave 6 — Work-loss protection: git + live sync

- [ ] 6.1 **[C9]** `revert_to` auto-commits/stashes before tag+reset; `delete_branch` backup tag before `-D` (`_git.py:649,806`)
- [ ] 6.2 **[C10]** Frontend: filter foreign `source_file`, identity check, dirty-state banner, reconnect resync; watcher parses only discovery-positive files (`useWebSocketSync.ts:101`, `server.py:356`)
- [ ] 6.3 **[M]** Push failures surfaced + `pushed` field (`_git.py:546+`)
- [ ] 6.4 **[M]** Protected branches enforced server-side, configurable (`_git.py:37`)
- [ ] 6.5 **[H]** Submodel drill-in save gated/redirected; no silent truncation (`useSubmodelNavigation.ts`, `usePipelineAPI.ts:581`)
- [ ] 6.6 **[H]** Ctrl+Z/Escape gated by `isTyping` (`useKeyboardShortcuts.ts:49`)
- [ ] 6.7 **[M]** git subprocess `encoding="utf-8"` + platform-smoke test (`_git.py:122`)
- [ ] 6.8 **[M]** `.gitignore` exact-line check (`cli/_init_cmd.py:497`)
- [ ] 6.9 ~~`haute init` root `main.py` deletion~~ — **by design** (uv init stub; pipeline `main.py` lives in the rating folder). Only assert init scaffolds the rating-folder `main.py`

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

## Behavior-change log (release notes)

| Wave | Change |
|---|---|
| W1/W2 | One-time cache invalidation on upgrade (ALGO_VERSION 3→4, 4→5) |
| W3a | Rating neutral-fill default flips to fail-loud (opt-in flag); waterfall arithmetic corrected |
| W3b | Non-finite optimiser inputs / composite groups / frontier budgets now error with contract messages |
| W4b | Seeded sampling replaces head(N) downsample; fabricated GLM SE/p no longer rendered |
| W6 | Auto-backup commits/tags in history; protected-branch ops rejected server-side; external-change banner |
| W7 | JSON payloads: big ints as strings, NaN/inf as sentinels (consumer-visible) |
| W8a/b | Smoke non-zero exit on unsupported transports; localhost session token (escape hatch documented); pinned Dockerfile |
