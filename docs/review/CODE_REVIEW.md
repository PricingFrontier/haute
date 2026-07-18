# Haute — full codebase review

Scope: working tree on branch `edge-joins` (in-progress changes included). Method: 15 parallel
component reviews, then independent verification of the headline CRITICALs. Findings are tagged
with confidence; items marked **[verified]** were independently re-read/reproduced during synthesis.

Severity: **CRITICAL** = wrong prices / lost work / stale-wrong data served silently / RCE.
**HIGH** = broken feature, silent fallback that hides errors, data-entry corruption.
**MEDIUM/LOW** = robustness, performance, maintainability.

---

## Verification corrections before implementation

Follow-up verification found that the review is materially correct, but a few items are stale or
overstated. Treat these notes as authoritative before turning the review into an implementation backlog:

- **C7 is partial, not fully reproduced as written.** Current tests show NaN/Inf `scenario_value`
  is caught during grid construction and surfaced as a contract error. The remaining issue is the
  missing proactive non-finite validation in `_validate_and_project`, especially for objective and
  constraint columns; the "reports converged" outcome is not currently proven.
- **C10 is partial.** Frontend clobber risk remains, but backend now includes `source_file` in
  broadcasts and has self-write suppression. The unresolved work is frontend identity/dirty-state
  arbitration, reconnect resync, and filtering non-pipeline `.py` changes.
- **The model-card HIGH finding is no longer actionable as written.** Current code labels metrics
  by diagnostics set and renders holdout separately; do not spend implementation time on that item
  unless a fresh repro appears.
- **Several HIGH/MEDIUM items need narrower wording before fixing:** ratebook apply/detail, pyfunc
  scoring breadth, CatBoost SHAP, apiInput focus loss, preview timeout "spinning forever",
  security exploit mechanics, push failures "everywhere", deploy validation, and LossTab eval
  curve detection. The underlying risks mostly remain, but the exact claims below should be read
  with the narrower wording added inline.

---

## Cross-cutting root causes

Most findings collapse into six systemic patterns. Fixing the pattern is worth more than fixing
each instance.

1. **Cache keys don't capture every input that affects output.** Edge `sourceHandle`/`targetHandle`,
   flat-file dataSource content, external files, model artifacts, and the JSON-cache state signature
   are each missing from at least one fingerprint. Every gap = stale wrong results served silently —
   the worst failure mode for a pricing engine. (C2, C3, C4, trace cache, model disk cache.)

2. **Silent fallbacks survive despite the fail-loud charter.** Neutral-factor fill on rating miss,
   NaN→null in previews, incomplete non-finite optimiser validation, fabricated GLM standard errors,
   `head(N)` "downsampling", swallowed push failures in several paths, `|| true`-style warn-and-continue
   in deploy/CLI.
   These are exactly the "incorrect and hard to notice" cases CLAUDE.md bans.

3. **The codegen↔parser round-trip is not idempotent and not injection-safe.** Chain-unwrap mangling,
   brace doubling that grows each save, unescaped pipeline name in the docstring, paren-counting that
   ignores string literals, and the edge-join node-id bug all corrupt the .py file on a normal
   save/load cycle.

4. **The file-watcher → websocket return path still lacks frontend identity/version discipline.**
   Backend broadcasts now include `source_file` and suppress self-write echoes, but the frontend still has
   no `source_file` filter, content/revision check, dirty-state arbitration, or reconnect resync. Any
   changed `.py` that parses as a graph can clobber the canvas.

5. **Int64 > 2^53 crosses the JSON boundary as a raw number.** Found independently by four reviewers.
   16+ digit policy/quote IDs round in the browser → wrong IDs displayed, trace row-match breaks.

6. **The optimiser service mocks the real solver in nearly every test.** Apply/detail paths and
   numerical/constraint behaviour are not pinned tightly enough against the real `price-contour`
   dependency, and some mocks have drifted from real result shapes.

---

## CRITICAL

### C1. Edge-join codegen writes raw frontend node ids → saved pipeline won't reload **[verified — MERGE BLOCKER for this branch]**
`src/haute/_codegen_builders.py:1044-1047`, `src/haute/_edge_join.py:47-59`
`edge_join_config_to_decorator_kwargs` emits `base_input=config["baseInput"]` / `join_input=config["joinInput"]`
verbatim. Frontend stores live node ids (`dataSource_5`) there, but parsed node ids are sanitized
function names (`Data_Source_5`). On reload, `resolve_edge_join_role_indices` requires those values ∈
`source_ids` (sanitized) → `ConfigError: edgeJoin role input is not connected (missing=['dataSource_5'])`.
The `target_port="base"/"join"` handles in the `connect()` calls are only a cross-check, not the role
source, so they don't save it. Any edge-join built from canvas-created nodes works until the page
refreshes/server restarts, then preview fails, the pipeline can't be re-saved, and the deployed artifact
crashes. `tests/test_edge_join.py:182-267` passes only because its node ids equal their sanitized labels.
**Fix:** rewrite role ids to function names at codegen time (codegen already role-orders sources via
`_role_order_node_sources`), mirroring the `instanceOf` remap. Add a round-trip test where id ≠ sanitized(label).

### C2. Dual-cache mirror is dead code: `committed/` JSON cache never populated **[verified]**
`src/haute/_json_flatten.py:106,255`
`mirror_cache_to_committed` short-circuits on `_is_working_consulted(data_path)`, but `_mark_working_consulted`
has **zero production callers** (only tests). So save never promotes `working/ → committed/`, and the
`load_v2_api_source` committed fallback (the documented deploy / fresh-server path) can never fire. User
caches + saves + restarts → "click Cache as Parquet" error despite having done exactly that. Tests pass
because they call the private marker by hand. **Fix:** mark consulted on successful build; add an
HTTP-level build→save→assert-`committed/`-exists test.

### C3. Graph fingerprint omits edge `sourceHandle`/`targetHandle` → stale cache on rewire **[verified]**
`src/haute/_cache.py:145-146`
Edge serialization is `f"{e.source}->{e.target}"`. The handle selects which port of a multi-port
apiInput feeds a consumer. Rewiring port "policies"→"drivers" (same node ids) produces an identical
fingerprint → preview, trace, and dataframe-execution caches all serve the old wiring's data silently.
Directly undermines the multi-port / edge-join work on this branch. **Fix:** include handles in the edge
key and bump `ALGO_VERSION`; add a flip-the-handle fingerprint test.

### C4. Preview/trace cache keys omit dataSource / externalFile / model-artifact state **[verified mechanism, wording corrected]**
`src/haute/executor.py:891-906`, `src/haute/trace.py:314-320`
Verification correction: preview keys include apiInput JSON-cache meta mtimes, but omit flat-file
dataSource content, external files, and model artifacts. Trace keys are even weaker: the trace cache
key omits the JSON-cache state signature as well as flat-file/model state. The stale-data conclusion
below remains valid, but the preview/trace mechanisms differ.
Flat-file CSV/parquet content, external files,
and model artifacts are absent, and the watcher only watches `.py` + config JSON. Re-export `data.csv`
or retrain a model out-of-band (the normal workflow — there's no in-GUI upload) → months-stale preview
and trace served with no indication. The sink path already content-hashes file inputs
(`dataframe_graph_input_fingerprint`), so the input class was understood but never wired into preview.
**Fix:** add a dataSource/externalFile/model signature extra-key (reuse `_runtime_path_fingerprint`).

### C5. `_unwrap_chain_assignment` corrupts `df = (expr).method()` on save **[verified]**
`src/haute/_code_extraction.py:252-256`
`inner = code.split("(", 1)[1]` then strips a trailing `)`, assuming the first `(` and last `)` are a
matched wrapping pair. They often aren't: `df = (a + b) * c` → `a + b) * c`; `df = (up.filter(...)).join(...)`
→ unbalanced, invalid Python. One save/load cycle makes the file unrunnable. **Fix:** AST-parse; only
unwrap when the body is a single `df = (...)` whose parens span the whole RHS.

### C6. Gini is row-order dependent under tied predictions **[verified + reproduced]**
`src/haute/modelling/_metrics.py:80`
Verification correction: the row-order bug is real, but the exact numeric examples below came from
an earlier repro. Current local repros still show materially different scores under pure row
permutation; do not rely on the specific values in this paragraph.
`order = np.argsort(-y_pred)` with no tie aggregation. A constant predictor scores −1.0 on
target-ascending data, +1.0 descending; a 2-level GLM scores 0.945 vs 0.757 purely by row permutation.
Validation/holdout partitions preserve source order, so any target-correlated ordering biases the metric.
Coarse-prediction models (GLMs, banded factors) are systematically mis-scored and comparisons are invalid.
`tests/test_metrics.py:561` only asserts finiteness. **Fix:** aggregate by unique predicted value (each
tie group = one Lorenz segment) or use the tie-corrected AUC formula; same fix for `compute_lorenz_curve`.

### C7. Optimiser non-finite validation is incomplete **[partially verified; original converged claim stale]**
`src/haute/routes/_optimiser_service.py:4139-4163`
Verification correction: current tests show NaN/Inf `scenario_value` is rejected during grid
construction and surfaced as `contract_error`. The remaining actionable issue is narrower:
`_validate_and_project` does not proactively reject NaN/inf in objective, constraint, or scenario
columns, so downstream library behaviour is still being trusted too late.
`_validate_and_project` validates quote_id nulls, but does not make numeric finiteness an explicit
contract before the optimiser stack is invoked. Keep the fix focused on that boundary: reject NaN/inf
in objective, constraint, and scenario columns in `_validate_and_project`, failing with the column name.
Do not rely on downstream solver/library behaviour to turn non-finite values into user-facing errors.

### C8. Trace waterfall arithmetic is nonsense for multiplicative chains **[high confidence, code-confirmed]**
`src/haute/_trace_waterfall.py:113-127`
For a modified step it feeds the column's **post-step cumulative value** in as the multiply/add factor:
`new_cumulative = cumulative * value` with `value` = 120 (the new premium) rather than the 1.2 factor, so
100 × 120 = 12,000. The op classifier also misreads `premium * (1 - discount)` as additive. For exactly
the flagship base→×factor→×factor rating chain the regulator-facing waterfall shows "×120.0" and totals
orders of magnitude off, irreconcilable with the final value beside it. Arithmetic tests feed hand-written
factors directly into `build_waterfall`, never through `execute_trace`. **Fix:** derive delta from
consecutive output values, compute implied factor `val/prev_val` for display, assert the cumulative
reconciles to the traced output value.

### C9. Git `revert_to` destroys uncommitted work while reporting "backup created" **[high confidence]**
`src/haute/_git.py:649-658`
`switch_branch`/`pull_latest`/`submit_for_review` all `_auto_commit` pending changes first; `revert_to`
does not. It tags HEAD (a commit) then `reset --hard` — wiping staged + unstaged work that is in no
commit, tag, or reflog. The exact non-git user the module promises to protect loses today's edits while
the UI says a backup was made. `delete_branch` (`:806-823`) is similar: `-D` force-delete with no backup
and no dirty-tree handling. **Fix:** `_auto_commit`/stash before tag+reset; tag before `-D`.

### C10. Websocket sync can still clobber the canvas; frontend ignores update identity **[partially verified]**
`frontend/src/hooks/useWebSocketSync.ts:101-152`, `src/haute/server.py:356-418`
Verification correction: backend now includes `source_file` in broadcasts and has self-write
suppression, so the original "no echo suppression" wording is stale. The remaining blocker is that
the frontend ignores update identity, revision/content identity, dirty state, and reconnect resync,
while the watcher can still parse non-pipeline `.py` files into an empty graph.
The watcher can still parse non-pipeline `.py` files into empty graphs, and the frontend applies every
`graph_update` without checking `msg.source_file`, revision/content identity, or local dirty state before
calling `markSaved()`. Consequences: editing a test/helper script can replace the canvas with an empty
graph; an external file change can overwrite unsaved UI edits with only a generic info toast; and missed
events during a server restart can leave a stale-but-"connected" UI because reconnect does not resync.
**Fix:** watcher parses only discovery-positive files; frontend compares `msg.source_file` to the loaded
file and ignores foreign updates; compare content/revision identity; refetch `/api/pipeline` on reconnect;
prompt/banner on external change when dirty.

---

## HIGH

### Data integrity / silent-wrongness
- **Int64 > 2^53 serialized as raw JSON number** `src/haute/_json_safe.py:16` **[verified]** — JS rounds
  it; 16+ digit IDs display wrong and break trace row-match (`trace.py:227`). Stringify ints with
  `abs(v) > 2**53`. (Found by 4 reviewers.)
- **NaN/+inf/-inf collapsed to null in previews** `src/haute/_json_safe.py:14` — indistinguishable from
  genuine null; Explore `null_count` disagrees with the preview for the same cell. Emit sentinels.
- **JSON cache validity ignores the data file** `src/haute/_json_shred.py:413` — re-clicking "Cache as
  Parquet" after editing `data.json` is a no-op (schema fingerprint unchanged), serving stale rows with
  old counts. Record a data-file signature.
- **Emit-true table with zero selected columns wedges the cache permanently** `_json_shred.py:441` vs
  `618-626` — build skips its parquet, validity then fails forever; the advertised remedy ("Cache as
  Parquet") can't fix it. Make build/validity/load agree on "emitting = emit AND ≥1 selected column".
- **Records silently dropped on shape mismatch** `_json_shred.py:151-170,281-291` — malformed JSONL
  lines / mixed arrays lose rows with no count. Count and surface skips.
- **Declared `date` column accepts JSON ints as epoch-days** `_json_shred.py:341-366` — `2024` → a garbage
  date, strict build succeeds. Mirror the bool-in-numeric guard.

### Rating / banding numeric correctness (silent fallbacks)
- **[Partially verified] Missing rating-table level can be priced as neutral (1.0×/0.0+)** `src/haute/_rating.py:359-373` — a renamed
  band label can silently price rows at base rate in the combined-rating path, no log/counter. Make
  neutral-fill opt-in; default to error/null; log miss counts.
- **[Partially verified] Float factor columns can miss string-keyed entries** `_rating.py:292-308` — integer-like
  floats such as `25.0` vs `"25"` miss and can then be neutral-filled; non-integer floats may already
  match their string form. Normalise numeric factor formatting on both sides or fail loud.
- **Generated banding node body is a passthrough** `_codegen_builders.py:334-347` — rating/model bodies
  embed `apply_*_from_config`; banding just `return {first}`, so a standalone `pipeline.run()` of the saved
  file silently skips banding. Add `apply_banding_from_config`.

### Model scoring (a wrong score is a wrong price)
- **[Partially verified] Pyfunc scoring loses named-column contracts on the numpy fast-path** `src/haute/_mlflow_io.py:897` —
  MLflow pyfunc models with named multi-column signatures can reject unnamed numpy input. Hidden by
  MagicMock-only tests. Return a named DataFrame for pyfunc; keep numpy only for catboost-no-cats.
- **Eager path re-executes the upstream plan and splices predictions positionally** `_model_scorer.py:524-543`
  — features collected once, predictions attached to a lazy plan that executes again; an order-unstable
  upstream op (user `group_by` without `maintain_order`, `unique`, streaming join) lands predictions on the
  wrong rows, silently. Collect once and score the materialized frame.
- **Deployed container reloads the model from disk on every `/quote`** `deploy/_scorer.py:578` — no cache on
  `load_local_model`, plus per-request contract re-read/re-hash. Cache by `(path, task)`.
- **[Needs focused repro] CatBoost SHAP may compare Poisson/Tweedie values in different spaces** `_model_explainability.py:101-159` —
  if `predict` returns exponentiated predictions while ShapValues are raw-formula values, the additivity
  check raises. Add a Poisson/Tweedie fixture, then use `prediction_type="RawFormulaVal"` for the checked
  regression path if the repro holds.

### Modelling / training
- **GLM config keys (incl. `offset`) merged into CatBoost params** `routes/_train_service.py:465` — CatBoost
  has no `**kwargs`, so the standard log-exposure frequency workflow crashes at fit. Gate the merge on
  `algorithm == "glm"`.
- **Exported GLM training script trains a different model** `modelling/_export.py:36-48` — terms/family/link/
  regularization live at config top level, not `params`; export drops them → a Gaussian all-features model
  trains "successfully". Share one config→kwargs builder with `_train_service`.
- **[Not actionable after verification] Model card labels validation metrics as "Holdout set" and hides true holdout** `modelling/_model_card.py:105-116`
  Verification correction: current code labels metrics from `diagnostics_set` and renders holdout separately;
  tests already pin this. Leave this out of the implementation backlog unless a fresh repro appears.
- **Fabricated GLM inference stats (SE=0.0, p=1.0) on fallback** `modelling/_rustystats.py:332-337` — invented
  significance rendered as real. Omit fields and flag a diagnostics error instead.

### Optimiser
- **[Partially verified] Ratebook "apply / Load detail" needs a real-library integration test** `routes/optimiser.py:1204-1212,933-966` —
  the original `.dataframe` claim is stale for newer materialisation paths, but apply/detail remains risky
  because UI affordances and result shapes are not pinned against the real `RatebookResult`. Verify the
  current shape, implement ratebook detail or gate the UI, and add a real-solver integration test.
- **Composite factor groups solve and save but cannot be applied** `_builders.py:1546-1567` — the saved
  artifact joins on a literal `"channel:age_band"` column → `ColumnNotFoundError` at deploy. Split composite
  names/levels into a multi-column join, or reject at save time.

### Frontend editors (data-entry corruption)
- **[Partially verified] apiInput path inputs can lose focus every keystroke** `ApiInputEditor.tsx:488,591` —
  React keys include the edited path, and half-typed paths also commit to config. The same label-input
  focus loss was not reproduced; keep the fix to stable keys/raw edit state where needed.
- **Renaming a connected port destroys its edges on the first keystroke** `App.tsx:322-339`,
  `utils/apiInputPorts.ts:74-80` — handle id is the mutable label and reconciliation runs per keystroke. Give
  tables a stable port id; rebind on rename.
- **Frontend manufactures ports (`port_N`, `label__N`) the backend can't emit** `apiInputPorts.ts:74-80` —
  blank/duplicate labels are hard-rejected by the backend but synthesized in the UI; edges bind to handles the
  executor never produces → KeyError at run. Surface blank/duplicate-label validation in the editor.
- **ScenarioExpander min/max corrupts decimals/negatives ("1.5"→5)** `ScenarioExpanderEditor.tsx:130,141` —
  `parseFloat(...) || 0` per keystroke on a controlled number input. Use commit-on-blur / raw-string pattern.
- **TwoWayGrid paste silently aborts on any non-numeric cell** `rating/TwoWayGrid.tsx:131-159` — one `N/A` or
  locale comma decimal drops the whole paste, no toast; a test pins the silence. Toast the offending cell.

### Frontend core / panels
- **Preview results flow through the history-aware setter** `usePipelineAPI.ts:390,442,562` — every node click
  pushes a garbage undo entry, clears redo, and dirties a freshly loaded pipeline. Use `setNodesRaw`; exclude
  `_`-prefixed keys from the persisted fingerprint.
- **Timeout aborts are indistinguishable from user aborts, then swallowed** `api/client.ts:266`,
  `usePipelineAPI.ts:449` — current code clears busy state, so "spinning forever" is stale, but the user
  still gets no useful timeout error. Abort with a reason / rethrow as a timeout error.
- **Ctrl+Z / Escape not gated by `isTyping`** `useKeyboardShortcuts.ts:49-64` — undo while editing a config
  mutates the graph (can delete the node being edited); Escape closes the panel mid-edit. Apply the existing
  `isTyping` guard.
- **Saving while drilled into a submodel posts the submodel interior as the main pipeline**
  `useSubmodelNavigation.ts` + `usePipelineAPI.ts:581` — Ctrl+S errors with a cryptic codegen 500, or (unwired
  submodel) silently truncates the main file; in-submodel edits are discarded on navigate-out. Gate/redirect
  save when `viewStack.length > 1`.
- **Trace conditional highlights the wrong branch** `frontend/src/trace/CalculationHero.tsx:462-477` —
  substring-matches the result against branch text, so arithmetic then-branches highlight "otherwise".
  Backend now emits branch metadata; the frontend should consume the taken-branch index instead of guessing.

### Security (local-server threat model)
- **[Partially verified] Unauthenticated local API with no CSRF/Origin/Host defense** `src/haute/server.py:215-227` —
  `/preview`, `/trace`, `/sink` execute attacker-supplied graph Python, and sink paths are not confined
  (`executor.py:1418`, no `validate_safe_path`). Treat the `text/plain` no-preflight exploit mechanics as
  needing an endpoint-level repro, but the local-server threat model is still real: no Host allowlist, no
  Origin/CSRF token, and dangerous write/execute routes. **Fix:**
  `TrustedHostMiddleware` localhost allowlist + a per-session token the SPA presents on every `/api/*` and the
  WS; validate WS `Origin` before `accept()`; confine the sink path.

### Tests / CI
- **e2e `resetE2eProject` can target the real repo** `frontend/e2e/projectIsolation.ts:19` — `switch --force`/
  `reset --hard`/`branch -D`/`clean -fdx` run with `cwd=.tmp-e2e-project` but no toplevel assertion; if that
  dir's `.git` is missing while a stale reused server keeps `/ready` green, git walks up and nukes the parent
  working tree. Assert `rev-parse --show-toplevel == e2eProjectRoot` / set `GIT_CEILING_DIRECTORIES`.
- **win32-only tests never run on CI** `.github/workflows/ci.yml:112` — the full suite runs only on ubuntu;
  the Windows job omits `test_file_ops.py`, so the new atomic-write reader-contention contract (and every
  win32-by-design test) executes on no CI leg. Add `test_file_ops.py` to platform-smoke.

---

## MEDIUM (condensed, grouped)

**Codegen/parser round-trip:** `_sanitize_description` doubles `{}` unconditionally so braced descriptions grow
each save (`_codegen_builders.py:178`); pipeline name interpolated unescaped into the module docstring →
breakout/injection, inconsistent with the quote-stripped submodel path (`codegen.py:660`); contract-injection
paren scanner counts parens inside string literals (`codegen.py:197-211`); non-literal decorator kwargs stored
as `ast.dump(...)` and re-emitted as corrupt strings (`_ast_helpers.py:48`); external-file imports after the
obj-load dropped (`_code_extraction.py:544`); regex fallback drops multi-arg `connect()` (`_parser_regex.py:42`).

**Caching/chunking:** chunk-local whitelist accepts `fill_null(strategy=...)` and `is_in(df[...])` whose chunked
results differ from full execution (`chunking.py:421-461`); projection re-adds post-rename names, hard-failing
rename+filter pipelines (`projection.py:839-904`); trace cache is count-bounded only, busting the preview byte
budget (`trace.py:210`); RAM estimate ignores string widths and join-added columns (`_ram_estimate.py:350`);
fresh artifact can be evicted at store time → hard run failure under byte pressure
(`_dataframe_execution_cache.py:465`).

**Server/routes:** save and submodel ops run fully on the event loop (`routes/pipeline.py:341`); `/infer` parses
the whole file on the loop and `sample_size` doesn't limit I/O (`json_cache.py:447`); JSON-cache build is
non-atomic and unserialized → concurrent builds can stamp one schema's meta onto another's parquets
(`_json_shred.py:456`); a 1s WS send stall permanently mutes a client without closing the socket
(`_helpers.py:375`); submodel routes bypass the save path's allowlist + rollback (`submodel.py:90`); preview 504
releases admission while the zombie thread runs (`pipeline.py:666`); "running" jobs never evicted → bricks the
training concurrency guard (`_job_store.py:94`).

**Optimiser:** single-quote solve crashes after solving (`std()` of 1 element, `_optimiser_service.py:1117`);
unseen factor levels silently rated 1.0 on apply (`_builders.py:1565`); frontier budget 100k vs library cap 10k
→ opaque 500s (`_optimiser_limits.py:14`); `/estimate` runs the full pipeline + two full scans despite a
docstring claiming it doesn't (`optimiser.py:154`); numerical/constraint behaviour untested against the real
solver.

**Modelling:** RAM/row-limit "downsample" is `head(N)` (biased, `_train_service.py:880`); temporal split routes
null-date rows into validation (`_split.py:268`); split parquet leaks on failure/cancel (`_training_job.py:1186`);
GPU cancel leaves a zombie fit thread + leaks train_dir (`_algorithms.py:466`); "Log to MLflow" button logs a
wrong signature and drops GLM artifacts (`routes/modelling.py:318`); shared `feature_contract.json` per output
dir → two models overwrite each other (`_training_job.py:1263`); PDP failures swallowed per-feature
(`_metrics.py:759`); non-finite rows filtered out of metrics silently (`_metrics.py:34`).

**Scoring/integrations:** multiclass `predict_proba` mislabeled as binary, eager/batch divergence
(`_mlflow_io.py:917`); unserialized concurrent model download/load — thundering herd, overwrite of in-use file
(`_mlflow_io.py:530`); Databricks per-batch fetch retry risks silent row truncation (`_databricks_io.py:284`);
concurrent fetch of one table shares a fixed tmp path (`:266`); zero-row fetch caches an all-string schema (`:326`);
unconditional Float32 cast degrades pyfunc precision (`_mlflow_io.py:888`).

**Tracing:** trace cache fingerprint omits the JSON-cache state signature → stale traces after a cache rebuild
(`trace.py:314`); progressive column-removal relaxes row matching and resolves duplicates arbitrarily with no
uncertainty surfaced (`_trace_correlation.py:203`); edge-join `_right` suffix poisons right-parent matching, zero
edge-join trace tests (`_trace_correlation.py:305`); column-relevance pruning drops branches feeding later
modifications of the traced column (`trace.py:794`); rating-step matched/default flags re-derive lookup semantics
with Python `str()` and diverge from `_rating.py`'s Utf8 left join (`_trace_enrichment.py:131`).

**Git/deploy/CLI:** `haute init` unconditionally deletes the user's root `main.py` (`cli/_init_cmd.py:383`); push
failures are swallowed in several paths and responses have no `pushed` field (`_git.py:546+`); generated Dockerfile
installs unpinned haute/polars/fastapi (`deploy/_container.py:448`); no expected-output/tolerance validation exists
despite the advertised "validate against test inputs with tolerances" (`deploy/_validators.py`); container impact
analysis compares response envelopes not predictions → "9,980 rows failed" garbage reports (`deploy/_impact.py:147`);
percentage-change on zero baselines yields astronomic values (`_impact.py:188`); deploy has duplicated validation
and shipment resolution paths that can drift (`cli/_deploy.py:132`); unsupported transports warn and exit 0
(`cli/_smoke.py:77`); protected branches enforced for 4 hardcoded names only, "read-only" is UI-only (`_git.py:37`);
`.gitignore` substring check can skip ignoring `.env` (`cli/_init_cmd.py:497`); `subprocess(text=True)` without
`encoding="utf-8"` can crash on non-ASCII paths on Windows (`_git.py:122`).

**Frontend panels:** trace values truncated to 2dp with no full-precision affordance — displayed factors don't
multiply to the displayed total (`StepCard.tsx:21` et al.); LossTab eval curve can vanish when eval metrics first
appear after the first history row (`LossTab.tsx:70`); error toasts auto-dismiss after 3s (`Toast.tsx:31`); cache
status-fetch failure rendered as "not cached" (`CacheFetchButton.tsx:94`); no root ErrorBoundary — Toolbar/modals/
NodeSearch crashes white-screen the app (`main.tsx`); heavy chart-scaffold duplication across the six modelling
tabs.

---

## LOW / maintainability (themes)

Two canonical-JSON encoders with divergent rules (`_cache.py` vs `_dataframe_execution_cache.py`); dead kwargs-
binding machinery (`_build_input_kwargs` has no runtime caller); `_graph_utils` vs `graph_utils` is a deliberate
facade (not duplication — leave it); `save_node_config` writes non-atomically and is production-dead; silent 4GiB
RAM fallback with no log (`_ram_estimate.py:101`); RestrictedUnpickler allowlist over-broad and lacks dot-boundary
anchoring (`_sandbox.py:346`); `_sandbox` AST docstring overstates what it blocks; tautological test
`expect(result.edges).toBe(result.edges)` (`apiInputPorts.test.ts:172`); planning docs (`EDGE_JOIN_*.md`,
1,300+ lines) committed into the mkdocs `docs/` tree; narrow ruff selection (no `B`/`PT`/`S`) and non-strict mypy;
frontend benchmark gates run only on a weekly cron, not per-PR.

---

## In-progress changeset (edge-joins branch) — merge verdict

Two coherent workstreams: a full-stack edge-join node (committed) and JSON shred v2 hardening + apiInput
multi-port (uncommitted). Quality is generally high — extensive new tests, error paths surfaced as 4xx not 500,
coverage gates raised not lowered, and **no weakened test assertions** (modified tests strengthen: malformed-JSON
now `raises` instead of returning None; the save-lock spy now uses real schema fields). **Blocking before merge:**
C1 (edge-join node-id round-trip). **Should fix before merge:** C3 (handles in fingerprint — the feature this
branch ships is the one that breaks), the apiInput port-identity issues (HIGH), and the undo-granularity on the
edge-reconciliation path. Edge-join has no trace tests and an incomplete runtime join-semantics matrix
(suffix/validate/coalesce/semi/anti/full/cross unasserted).

---

## Suggested triage order

1. **C1** — unblocks the branch.
2. **C2, C3, C4 + the cache-key theme** — stale-wrong-data is the worst class; one coherent pass over fingerprints.
3. **C6, C7, C8 + rating/scoring silent fallbacks** — wrong-number correctness.
4. **C5 + codegen round-trip cluster** — protects user work on every save.
5. **C9, C10 + sync/watcher discipline** — protects user work in the live loop.
6. **Security token + sink confinement**, **e2e reset guard**, **win32 CI** — guardrails.
