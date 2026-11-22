# Fable review (condensed record of the subagent report, 22 Nov 2026). Verdict: PLAN: SOUND WITH CHANGES

## Findings
- Snapshots: confirmed and understated. 411.96 MB of result JSON; snapshots 319.9 MB, of which 80.3 MB is unique (22 distinct). The own snapshot duplicates the top-level diagnostics (`_staged_runtime.py:606`).
- `full_model`: counts confirmed (55 / 19 / 19). Root cause: `_attach_model` (`sklearn.py:5152`), `_restore_metadata` (`:1138`) and `_native_json_kind` (`:1324`, called twice) serialise the native bank to JSON and parse it. Cost 18.0 / 8.8 / 6.4 s.
- `data_digest`: confirmed, 45 / 31 / 27 calls, 5.88 / 3.83 / 3.27 s. Four callers per point: `_paths.py:152`, `_paths.py:81`, Haute `_staged_runtime.py:518`, Haute `_tboost.py:303`.
- `full.json`: duplicated; 21.66 MB each, 1.79 MB deflated each.
- Caps: the manifest is at 16.55 MB of 32 MiB. The per-result read cap (`_stage_paths.py:463`) is at 26.5 MB, 83% of the limit. `_pack` enforces neither.
- `_save_artifacts` 5.34 s: `remember` 3.56 (`pruning_selection` 2.13, `data_digest`) plus `TBoostModel.save` 1.77. The real export is about 0.4 s. Drop D1.
- Of `_run_evaluation`'s 36 s, 14.5 s is `StageBundle.session` (`from_candidate_bytes` 7.88, `apply` 3.71, a load).
- Missing:
  - Checkpoint churn: pruning does 3 loads (8.45 s) and 2 saves (7.84 s); banding and smoothing do 2 loads and 1 save each. `_stage_paths.py:190-192`/`:250` rewrites defeat the stat-keyed cache.
  - A same-process candidate bytes round trip (`:255` → `_staged_runtime.py:233-237`).
  - A second result pass at `:367-380` (~4 s).
  - Path read builds and walks the pydantic `TrainResponse` (2.04 of 2.69 s; `_training_paths.py:45-54,61,69`).

## Placement and recommendations (ranked)
R1. **High.** Native accessors for objective, provenance and kind in `crates/t-boost-py/src/lib.rs` plus the stub. Use them in the three helpers and pass `kind` through. No format change.
R2. **High.** Write the immutable fit evidence once.
- Immediate: skip the save when unchanged.
- Structural, in v2: split the immutable fit evidence from the stage chain. Alternatively, Haute keeps the GBM checkpoint and applies the selected candidate chain.
- Compression is a band-aid until this is done.

A2 on `_FullFitState`: compute it in `capture()`; pass the verified manifest value in at load; make owned arrays read-only.

A1, if still needed:
- Use a private template estimator and `copy.copy`, not a parameter-only estimator. `tweedie_rho` and `objective` come from the native schema (`sklearn.py:4106`, `:5155-5161`).
- Take `n_features_in_` from `x.shape[1] + len(categories)`.

Phase C:
- Extend the store's descriptor and hydration idiom (`_stage_store.py:262-335`).
- Key by content SHA-256, with the revision as a checked field.
- Hydrate the own snapshot from the top-level diagnostics.
- Enforce caps in `_pack`.

D3: finiteness is already enforced at write (`allow_nan=False`) and at store decode. Share the strict decode hooks in `read_path_result` and `read_path_artifact`, drop the walk, and write a compact view per point.

D4 should come before C. Order: R1, R2-immediate, A2, then A1 simplified, D4 plus the harness default, C, then B as one v2, then D2/D3, then E.

## Open questions
- v2: no v1 reader. Refuse loudly. Bump `.hstage` too. Amend `staged-training.md:202`.
- `loss_history`: once per fit (t-boost already models it at session level). Haute holds it once per bundle and hydrates it into snapshots (`stageResults.ts:25` reads `snapshot.loss_history`).
- Status: return the compact projection (`_stage_views.py:54`, `client.ts:1715,1744`), computed once in `_train_status_response`. Never null it (`useBackgroundJobs.ts:187`, `ModellingConfig.tsx:850`). Keep the full result on the job record (`_training_lifecycle.py:415`).
