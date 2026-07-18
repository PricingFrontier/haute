# Haute — Architecture Map (Phase 0)

> Read-only audit of `origin/main` (== `wave-2-cache-integrity` @ `1b8eb150`, byte-identical).
> Date: 2026-06-19. Produced by 10 parallel region mappers + a synthesis pass.
> **These are leads, not verdicts.** 107 candidate risks were surfaced; each becomes an
> actionable finding only after Phase 1 verification produces a reproducing failing test
> (or a definitive code-trace proof for design issues).

## System in one paragraph

`haute` is a single-process, single-user local insurance pricing engine: a FastAPI backend +
React/TypeScript visual editor whose central artifact is the **`PipelineGraph`** (Pydantic model in
`src/haute/_types.py` — nodes, port-aware edges, preamble, submodels). Everything orbits **one
bidirectional contract**: the graph is BOTH (a) executed directly over Polars for interactive
preview/trace/batch/train/optimise/deploy, AND (b) round-tripped to standalone Python pipeline files
(codegen) that must price *identically* when run outside the canvas. **Semantic equivalence between
these two execution modes is the system's defining correctness obligation.**

## The hub and its seven engines

The `PipelineGraph` data model is the hub. From it radiate:

1. **Parser** (`parser.py`, `_parser_*.py`, `_graph_builders.py`) — user decorator-DSL Python → `PipelineGraph` (AST path + regex fallback for broken files).
2. **Codegen** (`codegen.py`, `_codegen_builders.py`) — the inverse: decorated functions + JSON sidecars + `connect()` calls, `ast.parse`-validated before write. Parser+codegen form a save/parse **fixpoint** guarded by Hypothesis round-trip property tests.
3. **Executor** (`executor.py`, `_execute_lazy.py`, `chunking.py`) — runs the graph three ways: eager preview (process-global byte-bounded `_preview_cache`), fully-lazy batch with adaptive parquet checkpoints, and a fail-loud AST-whitelisted chunked map-reduce.
4. **Projection** (`projection.py`) — reverse-topo column-demand planner bounding what execution reads.
5. **Trace** (`trace.py`, `_trace_*.py`, `_expression_parser.py`) — pure post-hoc observation layer; correlates a clicked price row backward by value-matching, builds a reconciled waterfall.
6. **Modelling** (`modelling/*`, `_model_scorer.py`, `_mlflow_io.py`) — trains CatBoost/GLM, scores via a uniform `ScoringModel`, pins a feature contract.
7. **Optimiser** (`routes/_optimiser_service.py` + bundled Rust `price_contour`) — Lagrangian / coordinate-descent price optimisation and efficient frontiers.

**Integrity spine:** `_registry.py` (`NODE_REGISTRY` — exactly one exec AND one codegen builder per
`NodeType`, validated complete at import; `KeyError`, never a silent fallback) + `_cache.py`
(`canonical_json` is THE single digest encoder; `graph_fingerprint` is `v5:`-prefixed so an algorithm
bump invalidates every keyed entry). Crucially, **the pricing math has one implementation, not two**:
both `_builders.py` (executor) and `_codegen_builders.py` (generated bodies) call the *same*
`apply_banding_from_config` / `apply_rating_step_from_config` / `score_from_config` /
`execute_edge_join` / `load_v2_api_source`. So the residual equivalence risk is **not** the shared
math — it is the **wiring + config-resolution** layer, where codegen (save time) and the executor
(run time) make independent decisions about which frame/branch feeds a node.

Routes (`routes/*`, `server.py`) wire the engines behind FastAPI with an in-memory `JobStore` for
long-running jobs and a `/ws/sync` WebSocket. The frontend mirrors the wire contract
(`api/types.ts` ↔ `types/guards.ts`), holds the graph in a Zustand store with tiered undo/redo, and
keeps its own per-node result cache keyed by a `structuralVersion` token — its own cache-identity
surface that must agree with the backend.

## Risk heat-map (audit priority, highest first)

| # | Subsystem | Score | Why it ranks here |
|--:|---|:--:|---|
| 1 | **Codegen + core graph model (semantic equivalence)** | **10** | A wrong generated body/wiring prices ALL quotes wrong when the saved pipeline runs standalone/production, with no canvas signal. Divergences (optimiserApply frame, liveSwitch branch, edge-join base/join swap, rating-key drift) produce valid-but-wrong Python that `ast.parse` passes and round-trip *structural* tests don't catch. |
| 2 | **Caching & data ingest (identity/integrity)** | **9** | Stale results served as fresh across preview/trace/dataframe-exec/deploy. Documented blind spots: `_v2_fingerprint` malformed-table collision, mtime-bucket coarseness, stat-gate byte-preserving rewrite, torn mirror copytree. **The named `wave-2-cache-integrity` concern.** |
| 3 | **Execution engine (eager/lazy/chunked + admission)** | **9** | Non-atomic preview-cache RMW + preamble-lock-on-miss races can serve merged/wrong frames or bind the wrong utility module; chunked≠full would silently mis-price if the AST whitelist admits a non-row-local construct. |
| 4 | **Price optimisation (god-file + Rust kernels)** | **8** | Inline-frontier swallow-all-to-string while solve reports success; ratebook canonical-level mis-map → wrong applied rate. 5046-LOC file duplicates failure-mapping across 4 orchestrators. |
| 5 | **Parser & DSL** | **8** | Duplicate function-name node collapse, `async def` node invisibility, submodel name-collision overwrite, implicit-edge-into-submodel drop — all lose graph structure WITHOUT a loud error (violates the fail-loud mandate). |
| 6 | **Modelling: training/scoring/MLflow** | **7** | pyfunc loader gap (latent, masked by native path), classification proba-vs-label metric mismatch, split-fraction drift. Mostly bounded by strong fail-loud contract checks. |
| 7 | **Projection & price tracing** | **7** | Projection can prune a needed column from batch/deploy (real miss). Trace shows wrong explanation to actuaries (display-only): catch-all enrichment, evaluator swallow-all, 1e-6 absolute row relocation, positional fast-path. |
| 8 | **Routes / server / job store / event bus** | **6** | Mostly availability/UX (spurious 409/504, stuck job, dropped broadcast) rather than wrong prices; lock-free `.jobs` reads are a latent free-threaded-Python hazard, benign on current CPython. |
| 9 | **Platform: sandbox/path/deploy/git/databricks/CLI** | **6** | Deploy validate-vs-serve gap and liveSwitch pruner label-collision are silent dev-vs-prod mispricing; sandbox gaps are loud-or-exploit not silent-wrong; RAM-estimate 4GiB fallback can OOM/over-downsample. |
| 10 | **Frontend: React/TS visual editor** | **5** | Browser-only: wrong cached preview/solve shown to one user; never persists wrong prices (save captures a fresh snapshot). Cross-source preview paint + djb2 config-collision show stale data as current. |

## Core pipelines & where they fail silently

### 1. Author → Parse → Edit → Codegen → Save (the round-trip fixpoint)
Parser and codegen are mutual inverses sharing `_code_extraction`/`_config_io`/`_graph_shape`. User
code is always emitted in STATEMENT form so re-emission is a fixpoint; generated bodies call the SAME
runtime helpers the executor uses.
**Silent-wrong sources:** (1) duplicate top-level function names collapse to one `GraphNode.id` with
no loud error (`_ast_helpers._extract_function_bodies`, `_graph_builders._build_rf_nodes`); (2)
`async def` `@pipeline` nodes invisible to the AST parser; (3) **CONFIRMED** — optimiserApply codegen
(`codegen.py:304-345`) emits a plain `return <first>` without rewiring to `ratebook_input` when
`sourceType in {run,registered}` and `optimiser_mode != 'ratebook'`, while the executor
(`_builders.py:1375-1415`) re-derives mode from `artifact.get('mode')` at runtime and may pick a
DIFFERENT frame → saved file diverges from canvas; (4) liveSwitch generated code hard-routes to
'live' while executor routes by `ctx.source` (`_codegen_builders.py:521-539` vs `_builders.py:633-666`);
(5) constant/dataSource `float(val)` literal-vs-config-string drift; (6) regex-fallback lexer
mis-classifying a `connect()` as in-string drops an edge silently; (7) submodel name collision
overwrites silently (`parser.py:198-202`).

### 2. Click-node → Preview (eager) + Click-cell → Trace (waterfall)
Preview is cache-first; trace is a pure observation layer reusing the exact DataFrames the user saw,
correlating rows post-hoc by value. The waterfall is value-derived and must reconcile or raise
`WaterfallReconciliationError`.
**Silent-wrong sources:** (1) preview cache is a NON-ATOMIC `try_get→execute→store/pin` under a
process-global cache shared with trace; concurrent same-fingerprint requests race store/pin; (2)
`_preamble_lock` held only on `lru_cache` MISS while `sys.path`/`sys.modules` mutate globally; (3)
`_correlate_rows_posthoc` positional fast-path with NO shared columns trusts row position
(`_trace_correlation.py:632-647`); (4) `_fix_upstream_values` 1e-6 ABSOLUTE float tolerance →
scale-dependent mis-match (`_trace_enrichment.py:1003-1011`); (5) `enrich_steps` try/except `{error}`
marker silently degrades every trace; (6) `_expression_parser` evaluator reimplements Polars
semantics and swallows ALL exceptions.

### 3. Upload dataset → JSON shred → Cache as parquet → Execute
The v2 apiInput codec is content-addressed with dual working/committed layout and atomic build/swap.
Validity requires schema_fingerprint match AND data_file signature match AND a parquet per emitting
table.
**Silent-wrong sources:** (1) `_v2_fingerprint` silently `continue`s past non-dict tables/columns →
two different configs collide to the same `schema_fingerprint`, so a stale cache is judged fresh
(`_json_shred.py:108-147`); (2) `cache_state_signature_for_graph` keys invalidation on
`int(mtime*1000)` → coarse-mtime filesystems rebuild within one bucket without invalidating
dependents; (3) `mirror_cache_to_committed` copytree without the build lock → torn mix; (4)
`_resolve_leaf` collapses a mid-walk list to `cur[0]` silently; (5) orjson keeps last duplicate key
while the parser raises → same config accepted differently by cache-build vs parser.

### 4. Train → Score → Explain (model lifecycle)
Parquet-staged at every stage for bounded memory; `config→TrainingJob` kwargs single-sourced; feature
contract pins schema + categorical domains and fails loud on drift.
**Silent-wrong sources:** (1) pyfunc/RustyStats logged with `loader_module='haute._mlflow_io'` which
has NO module-level `_load_pyfunc` → `mlflow.pyfunc.load_model` would fail, masked only because the
native `.rsglm` loads separately; (2) CatBoost classification training-metric uses `proba[:,1]` but
scoring 'prediction' returns hard label; (3) group/temporal split is Bernoulli-per-group →
realised validation fraction can drift; (4) legacy shared `feature_contract.json` only warned, not
removed → stale deploy config validates against the wrong model.

### 5. Solve price optimisation → Frontier → Apply/Save/Deploy
Numerical kernels are bundled Rust (`price_contour`); haute owns ALL orchestration, streaming,
validation, artifact lifecycle, concurrency. Heavy runtime objects have a short retention TTL; apply
re-acquires via `touch_heavy_objects` and fails loud (400) if evicted.
**Silent-wrong sources:** (1) inline frontier catches ALL exceptions to `result['frontier_error']`
while the solve reports success (`_optimiser_service.py:2184-2243`); (2) auto-range DROPS entire
read-batches containing any null `quote_id` (1456-1491); (3) legacy `frontier_min/max` now absolute,
applied identically to every constraint regardless of scale (1366-1406); (4) ratebook canonical-level
mis-map if `level_counts` came from a different frame than solved → wrong applied rate; (5) the
~5046-LOC god-file duplicates ~10-arm failure-mapping across 4 orchestrators.

## Cross-cutting concerns

1. **parse→codegen→execute SEMANTIC EQUIVALENCE** *(Phase 1 priority: highest).* Confirmed
   divergence in optimiserApply + liveSwitch + edge-join role. Build a **differential execution
   harness**: codegen a graph, run the file, diff outputs vs `execute_graph` for every node type —
   especially those the round-trip property test SKIPS (modelScore/external/liveSwitch/optimiser/
   optimiserApply/scenarioExpander/submodel).
2. **Cache identity & integrity** *(highest, tied — the named branch).* Two dossiers: (a)
   `DataFrameExecutionCache` parquet-artifact lifecycle under concurrent replacement + Windows
   file-sharing (single-unlink rule, weakref.finalize on GC threads); (b) fingerprint-COMPLETENESS
   audit — prove every output-affecting input class (file bytes, schema, model artifact, preamble
   module, categorical levels) appears in the relevant key.
3. **Fail-loud mandate vs broad try/except** *(high).* Hot paths violate the CLAUDE.md fail-loud
   mandate: trace `enrich_steps`, `_expression_parser` evaluate, inline optimiser frontier,
   `routes/_helpers.load_sidecar` swallow real bugs into silent degradation. Classify each: truly
   non-fatal vs hides a wrong number/cache decision.
4. **Concurrency & shared mutable singletons** *(high).* Preview-cache RMW race + preamble-lock-on-miss
   race are the two most likely to produce silent wrong results. Dataframe-cache finalizer-on-GC-thread,
   lock-free JobStore reads (latent on free-threaded 3.13).
5. **Numerical correctness & invariants** *(medium-high).* Rating-key Python mirror
   (`normalise_rating_key`) vs Polars twin (`_rating_key_expr`) must produce identical keys (drift →
   silent table miss → neutral/default rate). Test against REAL Polars output, not the
   reimplementation's own behaviour.
6. **Security & trust boundary** *(medium-high for one lead).* Executor resolves config paths with
   `enforce_project_root` defaulting False (vs routes' True) — arbitrary-file read at execution if a
   graph's config is ever attacker-influenced (`execution.py:179,406`).
7. **Performance hot paths** *(medium).* Well-engineered (checkpointing, projection pushdown). Two
   leads intersect correctness: byte-budget chunk sizing (128-row sample) can under-bound memory vs
   admission budget; frontend per-edit fingerprint cost on large ratebooks. Profile, don't deep-audit.

## Open questions to resolve early in Phase 1

1. Is there ANY differential-execution test for the node types the round-trip property test skips?
2. optimiserApply divergence — confirmed in code; reproduce and quantify the mispricing.
3. DataFrameCache lifecycle under concurrent same-key replacement + late weakref.finalize on a GC thread (Windows file-sharing) — can the single-unlink rule unlink a live artifact?
4. Fingerprint completeness — does the preview/trace key capture model-artifact changes?
5. Rating-key twin agreement across the full dtype matrix (Float32 non-integer, etc.)?
6. Deploy validate-vs-serve — validation runs without `artifact_paths`/remap; the container passes them. Same behaviour?
7. Executor path containment — is the `enforce_project_root` default-False asymmetry intentional?
8. Trace error philosophy — does any test assert a systematic enricher/evaluator bug surfaces loudly?
9. Preview-cache concurrency — is the non-atomic `try_get→execute→store/pin` provably safe under interleaving?
10. Frontend cache keying — preview slot keyed by `nodeId` alone while freshness depends on `(source,rowLimit)`.
11. Duplicate/async nodes — intended behaviour when two `@pipeline.<type>` funcs share a name, or a node is `async def`?
12. God-file failure-mapping drift — have all 4 duplicated failure-mapping copies stayed in sync?

## Artifacts
- `review/_working/00-map/global-map.json` — full synthesis (structured).
- `review/_working/00-map/region-maps.json` — all 10 region maps with 107 candidate risks (structured).
- `review/00-map/coverage-baseline.md` — coverage + suite health baseline.
