# PR #23 full code review — verified findings & handoff

**Scope:** branch `wave-2-cache-integrity` vs `main`, including uncommitted working-tree changes (265 files, +33,442/−3,276).
**Process:** 19 finder angles (106 raw candidates, see `PR23_REVIEW_PHASE1.md`) → dedup to ~75 → 32 adversarial verifier batches.
**Status:** 29/32 verifier batches complete (≈60 verdicts). 3 batches + the Phase 3 gap sweep outstanding — see **Resume** at the bottom.
**Verdict key:** CONFIRMED = trigger + wrong outcome proven against current code (several empirically reproduced). PLAUSIBLE = mechanism real, trigger uncertain. REFUTED = disproven with quoted evidence.

---

## Top 15 findings (ranked most severe first)

### 1. CRITICAL — `testserver` Host-header bypass defeats the new local session auth
`src/haute/_local_security.py:82` (+ `server.py:305`, `cli/_serve.py:303`)
`testserver` is in the trusted-host list under **every** config (default, non-loopback list, and `*`). `_is_testclient_harness` keys off the attacker-controllable Host header, so `Host: testserver` + no Origin skips **both** the Origin check and the session-token check for HTTP and WebSocket. On a supported `--host 0.0.0.0` bind, a network peer gets full unauthenticated API+WS access. Fix depth note (altitude): the test harness should authenticate with `HAUTE_LOCAL_SESSION_TOKEN` instead of a magic host string carve-out in production middleware.

### 2. CRITICAL — legacy rating-table keys silently rate whole book at the default factor (reproduced)
`src/haute/_rating.py:565` (guard skip at `:577`)
New canonicalisation collapses float frame values (`25.0` → `"25"`) but persisted string entry keys stay verbatim (`"25.0"`, the pre-PR `str(float)` save format that previously matched). No load-time migration exists. Reproduced: with no `defaultValue` every row misses → `RatingTableMissError` outage; **with** a defaultValue the miss guard is bypassed and the whole table silently rates at the default (legacy ratebooks: all relativities become 1.0, warning-log only) — wrong premiums portfolio-wide. The PR's pinning test covers only the loud no-default path. Mitigant: project stance says no deployed user base needs migration; the silent sub-path is still real and untested.

### 3. CRITICAL — uint32 training target flips the reported Gini sign (reproduced)
`src/haute/modelling/_metrics.py:108`
`-sort_key` in `np.lexsort` wraps for unsigned dtypes. `diag_df[target].to_numpy()` preserves UInt32 (e.g. claim counts from polars `count()`/`len()`); the fit path casts to Float64 but the metrics path does not. Empirical: identical data → Gini **−0.3785** (uint32) vs **+0.3653** (float64); plotted perfect Lorenz curve corrupted; Boolean target raises TypeError post-fit.

### 4. CRITICAL — MLflow disk cache collides same-basename artifacts and serves the wrong model
`src/haute/_mlflow_io.py:553`
Disk cache key is `(run_id, basename)`; `freq/model.cbm` and `sev/model.cbm` in one run collide — second load returns the first model's bytes with no integrity check, wrong `ScoringModel` cached in memory (the in-memory key also collides: `version="latest"`) and across restarts via the poisoned disk file; deploy bundler ships the wrong binary. Silently wrong predictions, persistent.

### 5. CRITICAL — exported GLM Tweedie training script silently reports different deviance than live training (reproduced)
`src/haute/modelling/_export.py:81`
Renderer emits `variance_power` only when `loss_function == 'Tweedie'` (a CatBoost-only field; GLM configs have `loss_function=None`, `var_power` top-level — the standard frontend GLM tweedie config). Exported script computes `tweedie_deviance` at default p=1.5 vs live p=1.7. Empirical: 1.0369 (live) vs 0.5320 (exported) on identical predictions. Defeats the module's stated "can never drift" purpose.

### 6. CRITICAL — trace waterfall fabricates factors across joined branches
`src/haute/_trace_waterfall.py:303`
No branch awareness: two branches carrying a same-named column (routine in insurance joins) are chained by topo order — the waterfall opens on branch B's value, fabricates an implied "×1.82" at the join, snaps to branch A's value, and **passes reconciliation** → confidently wrong chart; with opposite parent order, a spurious `WaterfallReconciliationError` for a healthy pipeline. Which one you get flips on node insertion order.

### 7. CRITICAL — typing "−0.5" into ScenarioExpander Min commits **+0.5** (keystroke-traced)
`frontend/src/panels/editors/ScenarioExpanderEditor.tsx:30`
At keystroke `-0`, the draft commits −0; `String(-0)` round-trips as `"0"`; `Object.is(-0, 0) === false` discards the draft and the input snaps to `0`; finishing `.5` commits +0.5. Natural path for entering any negative range bound in (−1, 0). The existing test fires the full string in one change event, so incremental typing is uncovered. Fix: `===` (or compare parsed-vs-parsed) instead of `Object.is`.

### 8. CRITICAL — fallback parser silently deletes pipeline edges below backslash-ending comments (reproduced)
`src/haute/_parser_regex.py:153`
`_has_backslash_continuation_before` treats a backslash at the end of a **comment** (e.g. `# data in C:\pipelines\`) as line continuation — the `pipeline.connect()` below is skipped with no error (healthy parser extracts it), and the next save regenerates the file without the edge. Violates the module's own fail-loud contract; Windows-path comments make it realistic.

### 9. CRITICAL — fallback parse + save silently empties user code in sidecar-config nodes (reproduced)
`src/haute/_parser_regex.py:712`
`fallback_parse` sets `config = loaded_config` (sidecar JSON) without extracting `config["code"]` from the parsed body — but sidecars never store `code` (stripped by `_CODE_KEYS`). Any syntax error elsewhere in the file → GUI shows empty code boxes for dataSource/externalFile/modelScore/scenarioExpander/ratingStep nodes → save emits boilerplate-only bodies, silently destroying user code. (Line predates the PR, but the PR rewrote `fallback_parse` end-to-end under an explicit no-silent-drop contract and left this path.)

### 10. HIGH — trace-correlation relaxed match is O(2^n): one trace click can pin a core for days (measured)
`src/haute/_trace_correlation.py:349`
No-match rows (e.g. right-only row of a `full` join) enumerate all 2^n−2 column subsets, one polars filter each, measured ~1ms/filter → n=30 ≈ 276 CPU-hours; n=40 with 6 modified columns ≈ 71 min. No width cap/time budget/early exit; the 120s route timeout 504s but **abandons** the thread (unkillable), and each retry adds another. Old code was O(n²).

### 11. HIGH — JSON cache status routes sha256 multi-GB files on the event loop, forever after one mtime drift
`src/haute/routes/json_cache.py:431,457` + `src/haute/_json_shred.py:265`
After a single mtime-only drift (touch/rsync/docker COPY), the stat fast-path misses **forever**: no memo, and nothing heals the recorded mtime (the no-op build trapdoor returns without refreshing). Both status endpoints call it synchronously inside `async def` (build/infer correctly offload) → every editor mount/status poll freezes the whole server for the full hash. Also re-hashes on every pipeline run via `load_v2_api_source`.

### 12. HIGH — deploy batch scoring can lose its cache pin mid-collect → `FileNotFoundError` (reproduced)
`src/haute/deploy/_scorer.py:810`
The unpin `weakref.finalize` is attached to the exact LazyFrame wrapper returned by `cache.scan()`; `output_lf.select(output_fields)` rebinds and polars keeps no Python ref to the parent → pin releases when `score_graph_lazy` returns, before `streaming_collect`. Under eviction pressure (16-entry process-shared cache) or invalidate/clear, the parquet is unlinked mid-collect. Reproduced with the repo's own classes. Triggers only when manifest `output_fields` is set (the normal deploy path).

### 13. HIGH — one non-finite prediction post-fit crashes the whole training run; NaN weights render silent NaN diagnostics (reproduced)
`src/haute/modelling/_training_job.py:1218`
The new finite-mask discipline covers `compute_metrics` and Lorenz only. `compute_residuals_histogram` (and double-lift/AvE) get unfiltered arrays: one inf prediction → `np.histogram` ValueError → run fails **after** fit, model lost (no try/except; the `_record_diag_error` protection covers only SHAP/PDP/GLM). Non-finite weights flow through silently → NaN histogram/decile stats.

### 14. HIGH — dirty-canvas sync banner recommends "Save", which silently destroys external disk edits
`frontend/src/hooks/useWebSocketSync.ts:124` (+ `:153`, `:170`; `server.py:230`)
Cluster of confirmed findings: (a) banner advises "Save or reload" but `savePipeline` has zero conflict detection → following the advice overwrites the external edit, and the blocked update is never re-delivered (banner has no reload button); (b) resync-on-reconnect replies unconditionally (no fingerprint skip, unlike the watcher) → false "changed on disk" banner when dirty, spurious toast+fitView jump when clean; (c) `graphUpdateSeq` increments **before** the foreign-file filter, so a foreign frame during layout-await silently cancels a legitimate current-file update (common in multi-pipeline bursts); (d) resync handler parses the pipeline + walks the FS inline on the event loop (HTTP routes offload the same parse).

### 15. HIGH — `archive_branch` mutates the remote before local checkout → inconsistent remote/local state
`src/haute/_git.py:898`
Remote rename+delete run first; a dirty-working-tree checkout failure then aborts before the local rename → remote archived/deleted, local untouched, sanitized 400, deterministic retry failure. The GUI archives the **current** branch by design, so a dirty tree is the common case. (Pre-PR order was local-first; the PR fixed the mirror-image remote-silent risk but swapped in this louder one.)

---

## Other confirmed findings (didn't fit the top-15 cap)

**High:**
- **SEC-2** `cli/_serve.py:293` — every IPv6 bind (even `::1`) sets trusted hosts `*`, disabling Host validation; compounds SEC-1.
- **PRX-2/PRX-3** `_parser_regex.py:298/491` — fallback parse aborts entirely (GUI can't open file) on `)` inside a comment in a multi-line connect, or on black/ruff-wrapped def signatures (both reproduced; old parser recovered both).
- **PRX-6** `_code_extraction.py:630` — user `with open(` bodies: leading imports + with-block stripped as boilerplate, also at **save** via the marker re-run; emitted file parses, fails at runtime NameError.
- **CFB-1/CFB-2** `CacheFetchButton.tsx:91/114` — stale-response race paints wrong status/error for the current resource; `/progress` endpoint is a stub (`active:false` always) so the button leaves "building" at the first 1s tick of every build → double builds possible (stub predates PR; button modified by PR).
- **API-2** `frontend/src/api/types.ts:781` — backend `skipped_records/skipped_rows` (W2 "zero silent record loss") are actively stripped by frontend guards; UI shows a green cached state after thousands of dropped records.
- **DFC-1** `_dataframe_execution_cache.py:432` — per-key lock acquired while holding the global guard: one same-key waiter serializes **all** materializations + clear() for the full sink duration (structure predates PR; function rewritten by PR, docstring contradicts behavior).
- **EFF-2** `_parser_regex.py:517` — fallback parser rescans source from index 0 per anchor (per-char Python loops, no precomputed mask; connect path scans twice). The file watcher runs `parse_pipeline_to_graph` **synchronously on the event loop** (server.py:576) and falls back to regex on any syntax error, so live-editing a broken large pipeline (thousands of nodes) stalls the whole FastAPI loop for tens of seconds to minutes. Reproduced reasoning; HIGH.
- **JS-3 partner half / SRV-1** are inside Top-15 items 11 and 14.

**Medium (selection):**
- **GIT-1** `_git.py:596` — `pushed`/`push_error` soft channel is dead (always `None`, GitPanel ignores); offline Save commits then raises, retry says "No changes to save."
- **JS-1** `_json_shred.py:921` — uuid `.build-tmp-*/.build-old-*` dirs leak permanently on swap failure (cleanup only matches legacy fixed names).
- **MLF-2** `_mlflow_io.py:614` — disk eviction under the wrong lock rmtrees dirs other threads are loading → spurious re-downloads.
- **MOD-1** `routes/modelling.py:410` — export route 500s on missing target (reproduced) — but the route has **no frontend caller today**; fix is ValueError→4xx mapping.
- **API-1** `client.ts:208` — token rotates per server process; zero 403 recovery → restart leaves open tabs in a toast-storm dead end until manual reload.
- **SEC-3** — remote browser on `--host 0.0.0.0` gets 403 on every POST (Origin allowlist never extended to bind host): the supported remote-UI config is non-functional.
- **WSF-4** — lowercased source-file matching applies the wrong pipeline's graph + `markSaved()` for case-twin filenames on Linux.
- **SCE-2/SCE-3** — cleared-but-invalid drafts persist old values on save (deliberate, test-pinned, but display diverges); drafts survive node switches (no `key` on the editor).
- **KEY-1** — second Ctrl+K lands in the autofocused palette input → toggle can't close; browser default Ctrl+K then fires.
- **DSP-1** — SchemaPreview rounds to 4 digits with no full-precision tooltip (StepCard got one in this PR).
- **MET-1 companion**: Boolean targets raise TypeError post-fit (in #3).
- **Test-quality (confirmed vacuous):** TST-1 (materialization-lock test passes with lock deleted; no sibling guards same-key serialization), TST-2 (ws_clients test exercises CPython set atomicity, never the production wrappers), TST-5 (`HAUTE_TRUSTED_HOSTS='*'` leaks across tests in the security suite), TSF-1/TSF-2 (WS reconnect-stop and keeps-connected halves unexercised), TSF-4 (PdpTab single-point NaN geometry unguarded — LossTab shows the right pattern).
- **Cleanup (confirmed):** CLN-1 (`_stat_gated_runtime_path_fingerprint` should be a `StatGatedCache`; bespoke copy lacks single-flight), CLN-2 (`canonical_json` straggler in `dataframe_graph_input_fingerprint` — byte-identical swap, no invalidation), CLN-4 (dir-swap dance duplicated; mirror copy lacks the Windows retry hardening — already diverged), CLN-5 (save/delete path validators duplicated; delete copy dropped telemetry), CLN-6 (non-finite token derivation duplicated — wire contract), CLN-7 (3 modelling tabs still hand-roll the empty state), CLN-8 (`HAUTE_TRUSTED_HOSTS` literal mirrored in server.py + cli/_serve.py — move to `_local_security`), CLN-9 (`PROTECTED_BRANCHES` dead alias — monkeypatching it is a silent no-op), CLN-10 (quote-id validation triplicated with byte-identical messages across optimiser.py + 2 sites in `_optimiser_service.py`), CLN-11 (`_run_score_pipeline` hand-rolls collect+validate that `_score_eager_unified`'s `categorical_levels` param already does — also a redundant second collect), CLN-12 (`execute_sink` double-applies `_resolve_sink_path` — idempotent today), CLN-15 (4th per-key keyed-lock registry — extract `KeyedLocks`). **CLN-3 PLAUSIBLE** (per-node-type carve-outs in `_runtime_file_signature_paths`; actionable slice: delete the redundant API_INPUT branch that shadows the table entry, publicise `_cache_path_for`). **CLN-13 PLAUSIBLE** (ScenarioExpander min/max state mirrored — but it's eager-commit, NOT CommittedTextInput's blur-commit contract, so only a *new* shared hook fits, not reuse of the existing component). **CLN-14 confirmed low** (`trace_result_to_dict` applies `to_json_safe` per-field × 9 instead of once over the payload — future numeric fields bypass the W7 sentinel/big-int contract). **EFF-1 confirmed medium** (`_apply_ratebook` calls `collect_schema()` inside the per-table loop → O(T²) plan resolution on every optimiserApply execution incl. per deploy-scoring request; hoist the schema + track added `outputColumn`s). **EFF-3 confirmed low** (byte-at-a-time JSON array sampling — API-only, GUI never sends `sample_size`, runs in threadpool).

**Low (confirmed but minor):** SEC-4 (non-ASCII token → 500 not 403, fails closed), WF-2 (big-int waterfall silently absent), JOB-1 (only pathological >24h optimiser solves), SGC-1 (documented stat-gate trade), JS-2 (TOCTOU multi-port load gives misleading error), TST-3/4, TSF-3/5/6/7, SCE-2 nuance, KEY-2 (deliberate trade-off), MLF-3 (unreachable today), TRC-2 (self-healing in common case).

## Refuted candidates
- **DBX-1** databricks `rownumber` None — pinned connector 4.2.5 provably always returns int (verified in installed source).
- **DSP-2** LossTab `best_iteration=-1` clamp — the −1 sentinel can't reach this code (only catboost/glm registered; pre-PR drew the marker too).
- **RAT-2** Datetime twin divergence — two independent **loud** gates block it (save-time level-counts cross-check raises; apply-time strict dtype revert raises); residual: Datetime ratebook factors are effectively unsupported with a confusing error.

---

## Official ≤15 findings JSON (skill output)

```json
[
{"file":"src/haute/_local_security.py","line":82,"summary":"`Host: testserver` + no Origin bypasses both the Origin check and the session token on HTTP and WS; testserver is trusted in every config, so any non-loopback bind is fully open to network peers.","failure_scenario":"haute serve --host 0.0.0.0 -> attacker sends Host: testserver, no Origin -> TrustedHostMiddleware admits it, LocalSessionMiddleware/_is_testclient_harness skips auth -> unauthenticated pipeline-execution/file API and /ws/sync access."},
{"file":"src/haute/_rating.py","line":565,"summary":"Asymmetric key canonicalisation breaks all pre-PR float-string rating-table keys, and with a defaultValue present the miss guard is skipped so the whole book silently rates at the default.","failure_scenario":"Pre-PR sidecar keys '25.0' vs Float64 column now canonicalised to '25' -> every row misses; defaultValue 1.0 -> output all 1.0 (reproduced; pre-PR returned 0.5/2.0) with warning-log only; no default -> RatingTableMissError outage; legacy ratebook artifacts lose all solved relativities."},
{"file":"src/haute/modelling/_metrics.py","line":108,"summary":"np.lexsort(-sort_key) wraps for unsigned targets, corrupting the perfect-Gini normaliser: reported Gini is plausible but wrong (sign can flip) for UInt claim-count targets.","failure_scenario":"UInt32 target (polars count()) -> diag path never casts to float -> empirical Gini -0.3785 vs true +0.3653 on identical data; Boolean target raises TypeError post-fit."},
{"file":"src/haute/_mlflow_io.py","line":553,"summary":"Artifact disk cache keys by (run_id, basename): same-basename artifacts in one run collide and the second silently loads the first's bytes; in-memory model cache key collides too.","failure_scenario":"Run logs freq/model.cbm and sev/model.cbm -> sev load hits disk-cache .cache/models/<run>/model.cbm containing freq bytes -> wrong predictions cached for process lifetime, across restarts, and bundled into deploys."},
{"file":"src/haute/modelling/_export.py","line":81,"summary":"Exported training script renders variance_power only when loss_function=='Tweedie' (CatBoost-only), so GLM tweedie exports compute tweedie_deviance at p=1.5 instead of the configured var_power.","failure_scenario":"Standard GLM tweedie config (var_power 1.7) -> live deviance 1.0369 vs exported-script 0.5320 on identical predictions (reproduced) — silent metric divergence the shared-builder refactor was meant to make impossible."},
{"file":"src/haute/_trace_waterfall.py","line":303,"summary":"Waterfall chains observed values across topo-ordered steps with zero branch awareness, fabricating implied factors between unrelated same-named columns on joined branches while reconciliation still passes.","failure_scenario":"Two sources both carry 'premium' (A=100 kept by join, B=55 earlier in topo order) + one downstream x1.2 step -> chart renders base 55, fabricated x1.82 at the join, x1.2 — confidently wrong; opposite parent order -> spurious WaterfallReconciliationError."},
{"file":"frontend/src/panels/editors/ScenarioExpanderEditor.tsx","line":30,"summary":"Object.is(-0, 0) is false while String(-0) is '0', so typing '-0.5' drops the draft at '-0' and commits +0.5 — sign flip on a pricing range bound.","failure_scenario":"Type '-','0','.','5' into Min -> at '-0' commit -0, committedText '0', Object.is(-0,0)=false discards draft, input snaps to '0', '.5' completes -> committed min_value +0.5 (keystroke-traced; test fires full string in one event so gap uncovered)."},
{"file":"src/haute/_parser_regex.py","line":153,"summary":"Fallback parser treats a backslash at the end of a comment as line continuation and silently drops the pipeline.connect() below it; the next save permanently deletes the edge.","failure_scenario":"File with syntax error elsewhere + comment '# data in C:\\pipelines\\' above a connect -> anchor rejected, edge absent from recovered graph (no error), save regenerates file without the connect line (reproduced; healthy parser extracts it)."},
{"file":"src/haute/_parser_regex.py","line":712,"summary":"fallback_parse uses the sidecar JSON as config without extracting config['code'] from the parsed body — sidecars never store code, so user code in sidecar-config nodes is emptied and the next save destroys it.","failure_scenario":"Syntax error anywhere + dataSource/externalFile/modelScore/scenarioExpander/ratingStep node with user code -> GUI code box empty -> save emits boilerplate-only body, silently losing the code (reproduced; healthy path extracts code at _config_builder:395-408)."},
{"file":"src/haute/_trace_correlation.py","line":349,"summary":"Relaxed row match enumerates combinations(shared_columns, width) for every width — O(2^n) polars filters when no subset matches — with no cap, budget, or early exit; the route timeout abandons (not stops) the thread.","failure_scenario":"Trace a right-only row of a full join with ~30 shared columns -> ~5e8 filters at ~1ms each ≈ 276 CPU-hours pinned by an unkillable thread per click (measured); n=40/m=6 partial match ≈ 71 min. Old code was O(n^2)."},
{"file":"src/haute/routes/json_cache.py","line":431,"summary":"Both JSON-cache status endpoints run an unmemoized full-file sha256 synchronously on the event loop whenever the recorded mtime drifts, and nothing ever heals the recorded mtime — every status poll freezes the server, forever.","failure_scenario":"touch/rsync/docker COPY of a multi-GB JSONL -> mtime fast-path misses permanently (no-op build trapdoor never refreshes meta) -> each ApiInputEditor mount/status poll hashes the whole file inside async def (build/infer offload; status does not) -> all requests + WS stall seconds-to-minutes, recurring."},
{"file":"src/haute/deploy/_scorer.py","line":810,"summary":"output_lf.select(output_fields) drops the only reference to the cache-pinned scan LazyFrame (finalize-based pin), so the entry can be evicted and its parquet unlinked before streaming_collect runs.","failure_scenario":"DEPLOY_BATCH with manifest output_fields + cache churn (16-entry process-shared cache) -> pin released at score_graph_lazy return -> eviction unlinks parquet -> in-flight collect raises FileNotFoundError instead of returning scores (reproduced with repo classes)."},
{"file":"src/haute/modelling/_training_job.py","line":1218,"summary":"compute_residuals_histogram (and double-lift/AvE) receive unfiltered arrays: one non-finite prediction post-fit raises in np.histogram and the whole training run fails after a successful fit; non-finite weights render silent NaN diagnostics.","failure_scenario":"Poisson/Tweedie exp-link overflow on one diagnostics row -> compute_metrics filters it (run survivable) but residuals histogram raises ValueError 'autodetected range is not finite' -> run lost before _save_artifacts (reproduced); NaN weight -> silent NaN bins/deciles."},
{"file":"frontend/src/hooks/useWebSocketSync.ts","line":124,"summary":"The dirty-canvas guard blocks disk updates and its banner recommends 'Save' — but savePipeline has zero conflict detection, so the recommended action silently overwrites external disk edits; resync echoes and a pre-filter seq bump also drop or misreport legitimate updates.","failure_scenario":"Dirty canvas + external .py edit -> update blocked, banner says 'Save or reload' (no reload button) -> Save POSTs the stale graph and destroys the edit with a success toast; reconnect resyncs fire false 'changed on disk' banners; a foreign-file frame during layout-await cancels the current file's update (seq incremented before the filter)."},
{"file":"src/haute/_git.py","line":898,"summary":"archive_branch renames/deletes the remote branch before the local checkout+rename, so a dirty-working-tree checkout failure leaves remote archived but local untouched, with a sanitized 400 and deterministic retry failure.","failure_scenario":"GUI archives the current branch (its only affordance) with uncommitted changes conflicting with the default branch -> remote mutated, then checkout raises -> inconsistent remote/local state; retry hits the same checkout failure (pre-PR order was local-first)."}
]
```

---

## Resume instructions (for a fresh agent)

**Artifacts (all under `C:\Users\prici\AppData\Local\Temp\pr23review\`):**
- `s1..s9 *.diff` — per-subsystem diff slices of `git diff main` (main == merge-base, so this includes the uncommitted working tree).
- `findings\*.json` — all 106 Phase-1 candidates by angle (a1-a6, t1, t2, b1, b2, c1, c2, d1, d2, e, reuse, simplification, efficiency, altitude).
- `findings\verdicts_partial.json` — ledger of all ~60 verdicts received (id → verdict/severity/note).
- Repo root: `PR23_REVIEW_PHASE1.md` (candidates), this file (results).

**Outstanding work:**
1. **1 verifier batch still outstanding** (the other two arrived during write-up and are folded in above). Its final JSON verdict array will be at the END of this JSONL transcript (read only the tail; the file is large):
   - `C:\Users\prici\AppData\Local\Temp\claude\C--Users-prici-haute\ccafdb49-51b5-4634-a709-4945ad03602f\tasks\ab8d0bd3e1a72ef1c.output` → **PRJ-1** (projection `rename()` demand bug, candidate in `findings\a1.json` #1 — finder rated **high**, potentially CRITICAL: a previously-working pipeline crashes with `ColumnNotFoundError` under projection because the rename branch never demands the rename's source columns) and **SNK-1** (sink output-path clobber via the existing-file-wins resolver, `a1.json` #2).
   If the transcript is unreadable, re-verify those 2 candidates from `findings\a1.json` (verifier protocol: CONFIRMED/PLAUSIBLE/REFUTED with quoted file:line evidence, adversarial, read-only). PRJ-1 is the single most important unresolved item — verify it first.
2. **Phase 3 gap sweep was never run**: one fresh finder agent re-reads the diff slices + this verified list, looking ONLY for defects not already listed (moved/extracted code that dropped guards, dataclass defaults, lock-scope shrinks, predicate side effects, test setup/teardown asymmetry, flipped config defaults). Up to 8 new candidates → verify → merge into the ranking.
3. **Re-rank if needed**: if PRJ-1 confirms as high/critical it likely enters the top-15 JSON (current #15, the git archive-branch finding, is the weakest of the 15).
4. A `ScheduleWakeup` may still fire with a "continue the review" prompt — **do not** launch new agents from it if usage is still constrained; the user paused the review deliberately.

**Verification quality bar used so far:** every CONFIRMED verdict quotes current file:line evidence; several were empirically reproduced (MET-1/MET-2 numerics, MOD-2 deviance, DSC-1 cache-pin repro, PRX-1/2/3/4 parser repros, RAT-1 end-to-end, TRC-1 measured). Maintain that bar.
