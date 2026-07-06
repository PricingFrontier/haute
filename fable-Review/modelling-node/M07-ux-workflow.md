# M07 — The DS workflow in the browser: built backends with no UI, and two promises the UI breaks

**Severity: HIGH (three) + MEDIUM (nine) + LOW (five)**
**Framing: the UI-field ↔ backend-consumption cross-check found NO orphan writes (every UI field is consumed — good), but six backend capabilities have no UI setter, and two pieces of UI copy contradict backend behaviour.**

What already works well (verified — do not regress): the algorithm gateway → target/task/loss
flow; degraded-run visibility (`diagnostics_errors` banner in `SummaryTab.tsx:52`); honest
validation-vs-holdout labelling ("Validation rows" for the legacy `test_rows`); the RAM/VRAM
pre-flight probe with downsample messaging (`TrainingActionsAndResults.tsx:129-179`) — the
README's "knows your machine's limits" is real; the GLM factor builder (typed terms,
interactions, per-term monotonicity) is genuinely strong; MLflow logging is rich (signature,
model card, every diagnostic as an artifact); `best_iteration` shown in Summary and drawn on
the loss chart.

---

## The three HIGH findings

### M07-1 (HIGH): you cannot cancel a running training from the UI
Backend cancellation is fully built — `POST /api/modelling/train/cancel/{job_id}`
(`routes/modelling.py:126`), `TrainService.cancel` (`_train_service.py:593`), checkpointed
worker, GPU abort-join. Frontend grep for `train/cancel` / `cancelTraining`: **zero** hits;
`TrainingActionsAndResults.tsx` renders only "Train Model". Default timeout is 3600s. A
runaway run on big data can only be waited out or killed with the server.
**Fix:** Cancel button while `training` is true → client `cancelTraining(jobId)` → existing
poll transitions to `cancelled`. **Tests:** button renders + fires while training;
start→cancel round trip in `ModellingConfig.test.tsx`; store clears the job.

### M07-2 (HIGH): the training-script export endpoint is unreachable from the browser
`POST /api/modelling/export` → `generate_training_script` is built, tested, and emits *more*
config than the UI can set (`_export.py:72-100`). No frontend caller exists (`client.ts` has
no export function; repo grep clean). The reproducibility/"take it to Databricks/CI" story —
a headline README claim — is invisible to a browser DS.
**Fix:** "Export training script" button (SummaryTab or config panel) → download
`train_<name>.py`; surface the 400 (`TrainingConfigError`) when config is incomplete.
Depends on M08-2 (export must first assemble config the same way live training does).
**Tests:** client call + blob download; 400 body surfaced.

### M07-3 (HIGH): the GPU banner promises "will fall back to CPU automatically" — the backend refuses with 507
UI copy at `TrainingActionsAndResults.tsx:174` vs `_check_gpu_fallback`
(`_train_service.py:745-794`) which **raises** `gpu_vram_limit` (507) telling the user to
switch to CPU themselves. Directly contradictory at the exact decision moment.
**Fix:** align copy with reality ("insufficient VRAM — switch to CPU or reduce
rows/features"); per the fail-loud philosophy, do NOT implement a silent auto-fallback.
**Tests:** corrected string pinned; backend 507 behaviour pinned so the copy can't drift back.

---

## MEDIUM findings

### M07-4: offset field lacks weight-vs-offset / ln() guidance at the point of use
Both offset pickers say only "(optional, e.g. log-exposure)" (`TargetAndTaskConfig.tsx:66`,
`GLMTargetConfig.tsx:92`); the real explanation lives only in
`docs/building-models/nodes/model-training.md:107`. This is the UI half of **M01-3** —
implement together (inline helper text + un-logged-offset heuristic warning).

### M07-5: live loss history is streamed but never charted during training
`train_loss_history` (≤200 pts + truncation flag) is streamed per iteration
(`_train_service.py:1068-1088`), typed in `api/types.ts:412` — and no component renders it
live; `TrainingProgress.tsx:38` shows scalars, `LossTab.tsx:33` charts only the final result.
Watching train/eval divergence live is *the* reason to stream it.
**Fix:** small live chart in `TrainingProgress` reusing `LossChart`. **Test:** multi-point
history → two polylines.

### M07-6: hyperparameters are raw-JSON-only
`FeatureAndAlgorithmConfig.tsx` is a JSON textarea (defaults iterations/learning_rate/depth/
l2_leaf_reg/early_stopping_rounds); a typo means a parse error means no training. The GLM
side has typed controls; CatBoost — the flagship — does not.
**Fix:** typed numeric inputs for the common five, two-way-synced with the JSON editor
(which stays, as the advanced escape hatch — see M09's passthrough positive). Natural home
for M09-2 (eval_metric select) and M09-3 (imbalance control).

### M07-7: no run history or A/B comparison; retrain overwrites
One result slot per node (`useNodeResultsStore.trainResults`), 409 on concurrent train
(`_train_service.py:670-676`). Iterating loses the previous run's numbers instantly;
comparison means leaving for the MLflow UI.
**Fix:** small per-node ring buffer (metrics + config hash + timestamp) with a compare strip;
deep-link to MLflow when logged. Also reconsider the global single-training lock
(`_check_no_concurrent_jobs` blocks training node B while node A trains — a real workflow
limit for frequency+severity pairs; if kept, keep the 409 message but say *which* node holds
the lock).

### M07-8: temporal split UI hides validation/holdout sizing entirely
The temporal branch renders only date column + cutoff (`SplitAndMetricsConfig.tsx:106-131`);
`validation_size`/`holdout_size` silently carry over from whatever random-mode values
persisted. This is the UI half of **M04-3** — fix the semantics and the UI together, and show
the resulting partition percentages pre-train.

### M07-9: local MLflow silently ignores the registered-model name
Registration is Databricks-gated (`_mlflow_log.py:383`); the UI shows "Model name (registered
model)" unconditionally (`SplitAndMetricsConfig.tsx:222`). A local user fills it in, nothing
is registered, and the Score node's Registered Model picker is inexplicably empty.
**Fix:** detect backend (the `/mlflow/check` endpoint already reports it) and either disable
the field with a note or support local registration where the store allows it. Surface the
active backend near the field.

### M07-10: no one-click downstream handoff for a fresh model
After training + logging there is no "use in a Score node" affordance; the DS re-finds the
run by memory in `MlflowModelPicker`. **Fix:** show run/model identifiers with copy actions
on the success state; optionally "create Score node from this model".

### M07-11: results-panel MLflow button gets `config={}`
`ModellingPreview.tsx:111` passes a literal empty config down to `MlflowExportSection`, so
the button always sends null experiment/model-name and *only works* because the backend
falls back to the job's training-time config snapshot — meaning post-train edits to the
experiment/model-name fields are silently ignored by the button. Thread the real config (or
delete the prop and document snapshot-authoritative behaviour). Related drift: **M08-1**.

### M07-12: no feature allowlist mode
Backend supports `feature_columns` (allowlist) but the UI only writes `exclude` — on a
500-column table, building a 6-feature model means clicking ~494 exclusions.
**Fix:** include/exclude mode toggle writing `feature_columns` (and clearing `exclude`).

## LOW findings (mostly "wired backend, missing small UI")
- **M07-13** `id_columns` (keep-but-don't-train identifier columns) — no UI.
- **M07-14** group-split seed input not rendered in group mode (`SplitAndMetricsConfig.tsx:132-168`) — reproducibility knob hidden.
- **M07-15** `output_dir` documented + backend-settable, no UI field.
- **M07-16** docs drift: `model-training.md:74-75` places `monotone_constraints`/
  `feature_weights` inside `params` (the node reads them top-level); GLM term-type names in
  docs don't match the builder. Fix docs; cross-ref M09-5.
- **M07-17** `fold_column`: do NOT add UI until CV exists (see M04-2) — surfacing it now
  would advertise cross-validation that doesn't happen.

## Acceptance criteria (wave-level)
- A DS can cancel a run, export the training script, and watch the live loss curve without
  leaving the panel.
- No UI copy contradicts backend behaviour (GPU fallback, registered-model name).
- The five common hyperparameters are typed inputs; JSON remains for everything else.
- Two consecutive runs are comparable in-app.
