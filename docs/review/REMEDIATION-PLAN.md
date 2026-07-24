# Remediation plan - root-cause clusters

> **Historical source package.** Current ownership and execution state live in
> the [component improvement catalogue](../roadmap/index.md). Re-verify a
> cluster against `HEAD` before implementation.

> **Scope:** Pure review and documentation - no code changes are to be made yet. The branches under active development are unpublished and not visible here, so no branch-overlap or sequencing-vs-branch analysis applies. Order remediation by severity x real-world urgency x effort only. Remediation is deferred.

> 69 verified findings grouped by underlying defect. Honour `review/02-findings/orchestrator-notes.md` corrections (codegen cluster = standalone-portability, not deployed mispricing).

## Recommended order (severity x real-world urgency x effort)

1. **Deploy artifact identity** (roadmap #2 / cluster C4 + C3) - the only cluster that can mis-price a *live served quote* (validate loads a different model than the container serves); effort S. **Do first.**
2. **JSON-shred conservation + fingerprint completeness** (roadmap #3 / C2, C11, C12) - the cache spine; fail-loud-or-account at every loss boundary; effort S.
3. **Parser structure-conservation pass** (roadmap #4 / C5) - turns 7 silent node/submodel-loss bugs into one invariant; effort M.
4. **Chunk-sizing OOM fix** (roadmap #5 / C13) - cost row width from the target projected schema; memory safety; effort M.
5. **Codegen shared apply_*_from_config helper + parse->codegen->execute differential harness** (roadmap #1 / C1) - highest structural leverage, but standalone-.py portability only (deploy + in-canvas already run the executor and are safe); effort M.

Quick wins can land independently at any time.

## Clusters (20)

### C1 — Codegen standalone-execution non-equivalence (passthrough/branch bodies)

- **Severity:** high | **Effort:** M (one shared-helper refactor + per-node-type template rewrites + differential harness)
- **Root cause:** Several NodeType codegen templates emit bodies that do NOT mirror their executor builders. liveSwitch hard-wires the SAVE-time 'live' input; optimiserApply/optimiser/modelling/scenarioExpander emit pure passthrough (`return <first>`). Unlike banding/ratingStep/modelScore, these templates do not call a shared config-driven runtime helper, so `Node.__call__` in `pipeline.py` (no NODE_REGISTRY dispatch) performs no-op or wrong-branch routing. The single underlying gap: passthrough-body node types have no self-contained runtime helper shared between `_codegen_builders.py` and `_builders.py`.
- **Findings (3):**
  - Generated liveSwitch body hard-wires the 'live' input; standalone pipeline.run() (source=batch) diverges from canvas executor branch selection
  - Generated optimiserApply body is a pure passthrough (return first); standalone pipeline.run() never applies the optimiser while the canvas executor does
  - Passthrough-body node types (optimiser, modelling, scenarioExpander, optimiserApply) generate bodies that perform no operation under standalone pipeline.run(), unlike banding/ratingStep/modelScore
- **Files:** `src/haute/_codegen_builders.py`, `src/haute/codegen.py`, `src/haute/_builders.py`, `src/haute/pipeline.py`
- **Fix strategy:** For each affected NodeType, add a config-driven `apply_*_from_config(first, config=..., base_dir=...)` helper in the shared graph_utils layer that loads the artifact and runs the same dispatch as the executor builder (`_build_optimiser_apply`, `_build_live_switch`, etc.), then have BOTH the codegen template and the executor builder call it — eliminating the duplicated wiring. For liveSwitch, route through a shared `select_live_switch_input(input_scenario_map, source, **frames)`. Add an EXECUTION-level differential harness (codegen a graph, run the file via pipeline.run()/score() under source in {live,batch}, diff against execute_graph) for exactly the node types the structural round-trip property test skips. NOTE (orchestrator correction): this is a STANDALONE-PORTABILITY guarantee breach (the README 'it's just Python, take it with you' promise) — deploy and in-canvas preview/trace/batch are SAFE because they run the executor path on the pruned graph, NOT the generated body. Frame as 'saved-file standalone equivalence', not production/in-canvas mispricing.

### C2 — JSON-shred conservation & fingerprint integrity (the wave-2 cache spine)

- **Severity:** high | **Effort:** S-M (mostly localized raises/skip-accounting in one file + one route call; data-file hashing is the only behavioural perf trade)
- **Root cause:** The v2 apiInput shred/fingerprint engine in _json_shred.py silently drops or collapses non-conforming structure instead of failing loud or accounting the skip: malformed (non-dict) tables/columns are `continue`d past, nested lists in scalar arrays are flattened (row inflation), mid-walk lists are collapsed to element [0]. The same engine's freshness checks under-key identity: data-file match trusts (size, mtime_ns) without hashing, and the status/validity route reads the config WITHOUT validate_v2_schema. Single root cause: the shred walker and its fingerprint violate the no-silent-loss + fingerprint-completeness mandate at the malformed/edge-shape boundary.
- **Findings (5):**
  - _v2_fingerprint silently skips non-dict tables/columns, collapsing structurally-different on-disk configs to one schema_fingerprint
  - shred_to_buffers silently flattens a nested list inside a scalar array, emitting MORE rows than source elements with zero skip accounting
  - _resolve_leaf silently collapses a mid-walk list to element [0], discarding the rest with no skip accounting
  - _data_file_matches returns fresh on size+mtime_ns match WITHOUT hashing, so a byte-changing rewrite that preserves both serves a stale cache
  - The status/validity path reads an on-disk v2 config WITHOUT running validate_v2_schema, and _v2_fingerprint silently continues past non-dict tables/columns — two malformed configs can collide and a stale per-port parquet cache is judged fresh
- **Files:** `src/haute/_json_shred.py`, `src/haute/routes/json_cache.py`
- **Fix strategy:** (1) Make _v2_fingerprint and _walk/_resolve_leaf fail loud (or route through _count_row_skip) instead of silently dropping malformed/nested/mid-walk-list structure — enforce conservation so distinct on-disk configs cannot collapse to one schema_fingerprint and emitted row counts reconcile to source. (2) Either drop the mtime fast-path in _data_file_matches and always verify the recorded sha256, or keep the stat-gate as a hint and verify the hash on match. (3) Call validate_v2_schema(v2_config) inside _v2_status_response before is_per_port_cache_valid (mirroring build_per_port_cache), letting ApiInputSchemaError propagate to the existing 422 path. Pin each with a failing conservation/collision test first.

### C3 — Cache key completeness: model-artifact & preamble-module identity excluded from fingerprints

- **Severity:** high | **Effort:** S-M (key composition changes reusing an existing fingerprint helper)
- **Root cause:** Fingerprints that gate cache freshness omit output-affecting input classes. The deploy output-schema cache keys on graph_fingerprint only (excludes model-artifact bytes/version), so a retrained 'latest'/in-place artifact bakes a stale ModelSignature/manifest. The preamble-utility fingerprint hashes only pipeline_dir/cwd candidates, missing a utility module resolved elsewhere via sys.path. The feature-validation cache keys on id(scoring_model) for instances never registered in the eviction cascade. Single root cause: fingerprints do not cover every input that changes the output (the architecture's 'fingerprint-COMPLETENESS' dossier).
- **Findings (3):**
  - infer_output_schema cache key (graph_fingerprint) excludes model-artifact bytes/version, so a stale output schema can be baked into the MLflow ModelSignature / manifest
  - preamble utility fingerprint hashes only pipeline_dir/cwd candidates; a utility module resolved elsewhere via sys.path is never hashed, serving stale preview/trace on edits
  - Feature-validation cache keys on id(scoring_model) for models that are never registered in the eviction-cascade cache
- **Files:** `src/haute/deploy/_schema.py`, `src/haute/_cache.py`, `src/haute/_model_scorer.py`
- **Fix strategy:** Fold artifact identity into each key. (1) In infer_output_schema, after artifacts are resolved, mix a stat-gated fingerprint of each bundled/resolved artifact path (reuse haute.execution._stat_gated_runtime_path_fingerprint, already used in deploy/_scorer.py) plus the resolved MLflow version/run into the cache key — or skip the cache entirely for graphs containing modelScore/optimiserApply. (2) Resolve the preamble utility spec via importlib.util.find_spec under the exec-time sys.path and hash spec.origin/submodule_search_locations into the fingerprint (and reuse that set for eviction). (3) Drop id(scoring_model) from the feature-validation key and rely on contract+schema keys (payload is already a pure function of those two).

### C4 — Deploy validate-vs-serve artifact divergence (test-before-live gate loads a different model)

- **Severity:** high | **Effort:** S (resolved.artifacts already available at both call sites)
- **Root cause:** The 'test before live' gate scores test quotes and infers output schema WITHOUT passing the bundled artifact_paths, so validate-time loads a model live from MLflow (version='latest', mutable) that can differ from what the deployed container serves, and skips the contract check. Root cause: the validation path and the serve path do not share the same artifact-resolution wiring.
- **Findings (1):**
  - Test-before-live gate scores test quotes WITHOUT bundled artifact_paths, so validate-time loads a different (potentially newer) model than the deployed container serves and skips the contract check
- **Files:** `src/haute/deploy/_validators.py`, `src/haute/deploy/_schema.py`, `src/haute/deploy/_model_code.py`, `src/haute/deploy/_container.py`, `src/haute/deploy/_scorer.py`, `src/haute/_builders.py`
- **Fix strategy:** Thread the already-available resolved.artifacts into both validation call sites: pass artifact_paths={name: str(path)} into score_graph in score_test_quotes (_validators.py) and into infer_output_schema (_schema.py, called from _config.py), so validate exercises the exact bundled artifacts the container serves and runs the same contract check. Add a regression test asserting validate and serve load byte-identical artifacts.

### C5 — Parser silently loses graph structure (fail-loud mandate violations)

- **Severity:** high | **Effort:** M (one shared regex-fallback submodel recovery + ~6 localized raises/warnings across parser files)
- **Root cause:** The parser drops or collapses graph elements WITHOUT a loud error across many shapes: duplicate decorated function names collapse to one node id; async def nodes are invisible; implicit param-name edges into submodel children are dropped; the regex fallback discards ALL submodels on any main-file syntax error; two submodels resolving to one name overwrite; nested submodel calls are ignored; aliased-import preamble over-capture. Single root cause: parse-time structure-loss boundaries silently degrade instead of raising/recovering, violating the CLAUDE.md fail-loud principle.
- **Findings (7):**
  - Regex fallback parser silently discards all submodels (and their nodes/edges) when the main file has any syntax error
  - Duplicate top-level @pipeline function names produce two GraphNodes with the same id; executor collapses them and the first node's pricing body is silently lost
  - Async @pipeline node functions (async def) are silently dropped by the healthy AST parser
  - Implicit param-name edge from a main-file node into a submodel child node is silently dropped (both flattened and hierarchical)
  - Two submodel references resolving to the same pipeline_name silently overwrite each other, losing an entire subgraph
  - A pipeline.submodel(...) call inside a submodel file is silently ignored (nested submodels unsupported with no error)
  - Preamble extraction over-captures the pipeline construction line when haute is imported under an alias, duplicating it on round-trip
- **Files:** `src/haute/_parser_regex.py`, `src/haute/parser.py`, `src/haute/_ast_helpers.py`, `src/haute/_graph_builders.py`, `src/haute/_parser_submodels.py`
- **Fix strategy:** Apply the fail-loud / loud-recover policy uniformly at each structure-loss site: (1) regex fallback — replicate the healthy path's submodel recovery (regex-scan top-level submodel() string literals, resolve via resolve_submodel_reference, parse each) so a broken main file no longer nulls all submodels; (2) duplicate node id — raise ParseError naming the function in _ast_helpers._extract_function_bodies; (3) async def — raise ParseError (or broaden isinstance to AsyncFunctionDef) per chosen policy; (4) implicit cross-boundary edge — retain child param_names through merge_submodels and reconstruct the edge like the explicit-connect path; (5) submodel name collision — raise ConfigError naming both files; (6) nested submodel — attach a graph-level warning + logger.warning per the 1-level-cap design; (7) preamble — make boundary detection AST-/alias-aware. Each gated by a failing test first.

### C6 — Rating-key Python-mirror vs Polars-twin dtype divergence (silent neutral/default miss)

- **Severity:** medium | **Effort:** M (carry dtype across the trace boundary + apply-side canonicalisation + dtype-matrix tests)
- **Root cause:** The Python rating-key mirror (normalise_rating_key) and the Polars twin (_rating_key_expr) canonicalise keys differently for the same logical value: non-integer Float32 diverges because the mirror widens to Float64, and ratebook factor levels are canonicalised at SAVE time against the banding-source column dtype but APPLIED against the optimiserApply input column dtype. A dtype divergence yields a silent neutral-1.0 / default-rate table miss. Single root cause: rating-key canonicalisation is not dtype-faithful and is not pinned to one dtype across save and apply.
- **Findings (2):**
  - normalise_rating_key (Python mirror) and _rating_key_expr (Polars twin) disagree on non-integer Float32 values
  - Ratebook factor levels are canonicalised at save time against the banding-source column dtype, but applied against the OPTIMISER_APPLY input column dtype; a dtype divergence for the same factor yields a silent neutral-1.0 miss
- **Files:** `src/haute/_rating.py`, `src/haute/routes/_optimiser_service.py`, `src/haute/_builders.py`
- **Fix strategy:** Make canonicalisation dtype-faithful and dtype-stable across save/apply. (1) Thread the factor column's original dtype through the trace JSON boundary so non-integer Float32 cells are canonicalised via pl.Series([...], dtype=pl.Float32).cast(Utf8), matching the twin (verify against REAL Polars output, not the mirror's own behaviour). (2) At apply time, additionally canonicalise the Utf8 apply column through the same int-like-float collapse ('25.0'->'25') so a save/apply dtype divergence stops missing, OR validate save-vs-apply factor-column dtype agreement loudly. Pin with a dtype-matrix differential test (Float32 non-integer, Int32/Int64).

### C7 — Trace expression evaluator re-implements Polars and launders failures (wrong explanation shown loudly-clean)

- **Severity:** medium | **Effort:** S-M (remove catch-all fallback + two operator branches)
- **Root cause:** _expression_parser.py reimplements Polars semantics and (a) returns wrong/None for horizontal funcs (concat_str), replace_strict, and unsupported ops, while (b) the parse/evaluate/compute/chain layer wraps everything in `except Exception` and falls back to the raw observed row value, so a self-consistent-looking waterfall masks the evaluator bug. Single root cause: a parallel Polars re-implementation plus a value-laundering catch-all on the trace display path (fail-loud violation, display-only).
- **Findings (3):**
  - Trace expression evaluator returns wrong/None values vs real Polars for horizontal funcs and unsupported functions, shown as the computed price-explanation value
  - Expression parse/evaluate/compute/chain wrap everything in `except Exception` and fall back to the raw row value, masking evaluator bugs as self-consistent waterfalls
  - Expression evaluator silently diverges from Polars on replace_strict (and other ops), displaying a wrong/unraisable 'calculation' to the user
- **Files:** `src/haute/_expression_parser.py`, `src/haute/_trace_enrichment.py`
- **Fix strategy:** Stop laundering and close the known semantic gaps: (1) in _compute_result, do NOT return row_values.get(target_column) on arbitrary exceptions — propagate or raise a typed ExpressionEvaluationError so result_value is never silently the observed value; (2) add the concat_str branch in _eval_horizontal honouring separator + ignore_nulls=False; (3) in _eval_replace, use the passed `method` — for replace_strict with no mapping hit and no default, raise/propagate instead of returning base_val. This is display-only (no price corruption) but directly violates fail-loud.

### C8 — Trace correlation positional/tolerance heuristics relocate to the wrong row (wrong explanation)

- **Severity:** medium | **Effort:** S-M
- **Root cause:** Post-hoc row correlation in the trace layer trusts unsound heuristics: the positional fast-path attaches the wrong parent row when a same-row-count transform reorders rows and shares no columns; _fix_upstream_values uses a fixed 1e-6 ABSOLUTE tolerance that collides distinct small-magnitude factors; the waterfall membership gate omits a real multiplicative step that is a row-local no-op. Single root cause: value/position correlation lacks uniqueness + scale-relative + structural-target guarantees.
- **Findings (3):**
  - Positional fast-path in _correlate_rows_posthoc attaches the wrong parent row when a same-row-count transform reorders rows and shares no column names with the parent
  - _fix_upstream_values relocates upstream rows by a fixed 1e-6 ABSOLUTE float tolerance, colliding distinct small-magnitude factors and overwriting the displayed value with the wrong row
  - Waterfall steps are gated on schema_diff.columns_modified computed from single-row JSON-coerced values, so a real multiplicative step that is a no-op for THIS row is dropped from the explanation
- **Files:** `src/haute/_trace_correlation.py`, `src/haute/_trace_enrichment.py`, `src/haute/_trace_waterfall.py`
- **Fix strategy:** (1) Remove the blind `if not shared:` positional acceptance — return None/'unresolved' for reorderable transforms with zero shared columns rather than fabricating a row. (2) Replace the 1e-6 absolute tolerance with a scale-relative match (rel_tol=1e-9 mirroring _trace_values_match) AND require uniqueness before relocating. (3) Gate waterfall membership on structural target (expression.target_column / the with_columns regex) so an identity-for-this-row multiplicative step is shown as an explicit x1.0 entry. Display-only severity.

### C9 — Projection demand mis-attribution / silent seed drop (latent column-pruning miss)

- **Severity:** medium | **Effort:** M
- **Root cause:** The projection planner heuristically routes uncovered/collision columns to a single guessed parent and silently drops a caller-supplied seed for multi-consumer opaque nodes. The same guess also drives codegen's stale-inputs-by-parent re-attribution. Single root cause: fan-in/passthrough column ownership is inferred by heuristic rather than from the true Polars operand, and non-strict fall-throughs are silent.
- **Findings (3):**
  - PolarsFanInRule passthrough-parent inference routes ALL uncovered columns to a single heuristically-chosen parent, which can demand a column from a parent that does not produce it
  - _format_contract_source guesses fan-in column ownership when exactly one stale parent and one unmatched parent exist, re-attributing columns to a possibly-wrong parent
  - compute_prepared_plan silently drops a caller-supplied projection seed for a node with >1 child when demand is opaque, in non-strict profiles
- **Files:** `src/haute/projection.py`, `src/haute/codegen.py`
- **Fix strategy:** Make fan-in ownership evidence-based and the fall-through observable: (1) when a join is present and simple_left_join_passthrough_parent returns None, route un-suffixed collision columns to the LEFT operand (true Polars owner) or fail loud with ContractMismatchError rather than the subset heuristic; (2) in codegen's single-stale/single-unmatched case, drop the stale inputs_by_parent and log contract_inputs_by_parent_omitted_stale (edges+body remain source of truth; projection falls back to a correct wider bound); (3) set a distinct ProjectionReason (projection_seed_dropped...) on the non-strict implicit-else so the drop is diagnostic, not silent.

### C10 — Optimiser numerical / silent-failure cluster (5046-LOC god-file)

- **Severity:** medium | **Effort:** M (six independent small fixes in one god-file; the ratebook-resolve item is the highest-value)
- **Root cause:** routes/_optimiser_service.py (+optimiser.py) concentrates many independent silent-wrong / numerical defects rooted in the same god-file: inline frontier swallows all exceptions while reporting solve success; ratebook frontier-point save re-solves from stored lambdas instead of the sweep optimum; non-integer Float scenario_index truncates and merges steps; legacy frontier_min/max broadcast one absolute interval across differently-scaled constraints; auto-range null-batch drop asymmetry; Float32 envelope summation precision. Common root cause: optimiser orchestration trades loudness/precision/authoritative-result for convenience in one oversized file.
- **Findings (6):**
  - Inline efficient-frontier compute catches every exception into a non-fatal frontier_error string while the solve is reported as fully completed
  - Ratebook frontier-point selection/save re-solves coordinate descent from the stored point's lambdas instead of using the sweep's recorded optimum, so saved factor tables can disagree with the displayed frontier point
  - A non-integer or out-of-pattern Float scenario_index passes the finite-value contract but is silently truncated when cast to Int32, merging distinct scenario steps
  - Legacy frontier_min/frontier_max are reused as the SAME absolute (min,max) range for every constraint regardless of each constraint's scale
  - Auto-range accumulator drops every row of a read-batch that contains any null quote_id and accounts only the null rows (latent: validation rejects nulls first)
  - Auto-range achievable envelope sums per-quote Float32 extrema in Float32, accumulating rounding error in the frontier range bounds for large portfolios
- **Files:** `src/haute/routes/_optimiser_service.py`, `src/haute/routes/optimiser.py`, `frontend/src/panels/OptimiserPreview.tsx`, `.venv/Lib/site-packages/price_contour/ratebook.py`
- **Fix strategy:** Address individually (independent defects, shared file) and pin each with a test: (1) surface frontier_error prominently in UI (and disable the price-point picker when frontier is None) — keep solve non-fatal but unmissable; (2) reuse the sweep's recorded factor_tables/total_objective/total_constraints (already computed at ratebook.py:937-943) in _materialise_ratebook_frontier_point instead of re-solving; (3) add an integrality+dtype guard raising 400 for float scenario_index with value!=floor(value); (4) refuse to broadcast a single absolute interval across >1 differently-scaled constraints (raise, require per-constraint frontier_ranges); (5) make the auto-range null handling row-local/symmetric; (6) accumulate envelope totals in Float64. Consider extracting the ~10-arm failure-mapping duplicated across 4 orchestrators into one helper to stop drift.

### C11 — Cache mtime-bucket coarseness & mirror lock/rename concurrency (committed-cache layer)

- **Severity:** medium | **Effort:** S (reuse existing lock + ns-precision + retry helpers)
- **Root cause:** The committed-cache layer (_json_flatten.py / _json_shred.py mirror) under-protects identity and concurrency: cache_state_signature_for_graph keys on int(mtime*1000) (coarse ms bucket), and mirror_cache_to_committed copytrees working->committed WITHOUT the per-dir build lock and uses bare rename (no Windows retry), unlike _swap_dir_into_place. Single root cause: the committed mirror does not reuse the ns-precision + locked + Windows-safe-rename machinery the sibling swap path already has.
- **Findings (2):**
  - cache_state_signature_for_graph keys preview/trace invalidation on int(meta.mtime*1000); two meta.json rebuilds in the same ms bucket yield an identical fragment
  - mirror_cache_to_committed copytrees working/ -> committed/ without the per-dir build lock and uses bare rename (no Windows retry), unlike _swap_dir_into_place
- **Files:** `src/haute/_json_flatten.py`, `src/haute/_json_shred.py`
- **Fix strategy:** Reuse the sibling-path machinery: (1) key cache_state_signature on st_mtime_ns + st_size (or hash the meta.json bytes / fold in schema_fingerprint) to match the ns-precision sibling; (2) expose _json_shred._build_lock_for and acquire it in mirror_cache_to_committed around read-meta + copytree + swap, and replace bare rename with the Windows-safe retry used by _swap_dir_into_place.

### C12 — Cache-build vs parser duplicate-key / unbounded-revalidate divergence

- **Severity:** low | **Effort:** S
- **Root cause:** The cache-build read funnel and the parser read funnel disagree on the same file: _read_v2_config (orjson) keeps the last duplicate key while the parser (object_pairs_hook) rejects it; separately store_artifact re-opens the just-written parquet under the pin and can evict a valid store on a transient read. Root cause: two independent JSON-read code paths for one file, plus an over-eager post-write revalidation.
- **Findings (2):**
  - Cache-build route (_read_v2_config, orjson) silently keeps last duplicate key while the parser load path (json object_pairs_hook) rejects it — same file, two outcomes
  - store_artifact calls self.get(key) after put, re-validating (scan_parquet) the just-written artifact under the lock; a transient parquet read failure raises CacheArtifactCorrupt and evicts a valid store
- **Files:** `src/haute/routes/json_cache.py`, `src/haute/_config_io.py`, `src/haute/_dataframe_execution_cache.py`
- **Fix strategy:** (1) Extract the object_pairs_hook duplicate-key check into a shared helper and call it from _read_v2_config (and ideally the codegen-emitted body / rating/main.py) so cache-build and parser reject identical inputs identically. (2) In store_artifact, do a lock-held presence check without revalidating (e.g. super().get(key.cache_key)) instead of routing through self.get()/_evict_if_invalid, so a transient reopen cannot evict a just-written valid artifact.

### C13 — Chunk/memory budget under-bounding (OOM hazard)

- **Severity:** high | **Effort:** M for chunk-sizing (high); L for continuous memory enforcement (low)
- **Root cause:** Memory admission is under-bounded in the execution engine: byte-budget chunk sizing estimates target row width from the SOURCE schema only (downstream-created wide/string columns fall back to 64 bytes), and the ExecutionContext memory limit is sampled only at checkpoint/stage boundaries so a single ballooning collect/sink between checkpoints is never interrupted. Root cause: chunk sizing and the memory gate both reason about the wrong/too-coarse width.
- **Findings (2):**
  - Byte-budget chunk sizing estimates target row width from SOURCE schema only, so downstream-created wide/string columns fall back to 64 bytes and the target chunk can exceed the memory budget
  - ExecutionContext memory limit is a coarse post-hoc gate sampled only at checkpoint()/stage() boundaries; a single node's collect/sink that balloons RSS between two checkpoints is never interrupted and can OOM
- **Files:** `src/haute/chunking.py`, `src/haute/_execution_context.py`
- **Fix strategy:** (1) Cost the target row width from the TARGET node's projected output schema (collect_schema on the target lazyframe restricted to needed_by_node[target], sampling String/variable-width columns like _source_projected_column_widths already does for the source), not the source schema. (2) For continuous enforcement (heavy), add a bounded-interval watchdog thread per ExecutionContext that polls memory_sampler() and trips cancellation_token on breach — noting it cannot pre-empt a single in-flight Polars C++ collect, so optionally pair with a hard process_rss_limit. Prioritise the chunk-sizing fix (high); treat continuous enforcement as lower-priority (L effort).

### C14 — Modelling train/score semantics & MLflow logging contract gaps

- **Severity:** medium | **Effort:** M (three independent fixes across modelling + mlflow_io)
- **Root cause:** The modelling lifecycle has independent train-vs-score and MLflow-logging mismatches: CatBoost classification training metrics use predict_proba[:,1] but scored 'prediction' is the hard label; pyfunc/RustyStats models are logged with loader_module='haute._mlflow_io' that exposes no _load_pyfunc; MLflow signature build aborts the whole run on Date/Datetime/Decimal/Time/Duration features. Root cause: train-time and score/log-time make independent decisions about prediction semantics and dtype-to-MLflow mapping.
- **Findings (3):**
  - Training metrics use predict_proba[:,1] but scored 'prediction' column is the hard class label
  - RustyStats/pyfunc models logged with loader_module='haute._mlflow_io' that exposes no _load_pyfunc/load_model entry point
  - MLflow logging aborts (ValueError) for any model with a Date/Datetime/Decimal/Time/Duration feature dtype
- **Files:** `src/haute/modelling/_algorithms.py`, `src/haute/_mlflow_io.py`, `src/haute/_model_scorer.py`, `src/haute/modelling/_mlflow_log.py`, `src/haute/modelling/_signature.py`, `src/haute/modelling/_training_job.py`
- **Fix strategy:** (1) Make 'prediction' semantics consistent: at score time for classification write positive-class probability to 'prediction' (matching training gini/auc/calibration) and expose the hard label as a derived '<col>_label' (or vice-versa, but one definition). (2) Either log non-CatBoost models through a proper pyfunc PythonModel (mirroring deploy/_mlflow.py) or add a module-level _load_pyfunc(path) in haute._mlflow_io. (3) Extend _POLARS_TO_MLFLOW + _polars_dtype_name to map temporal/decimal to representable MLflow types instead of falling through to str(dtype). The pyfunc-loader item is latent (masked by the native .rsglm path) but a real contract breach.

### C15 — JobStore unlocked-read concurrency & job lifecycle timeout coherence (routes)

- **Severity:** medium | **Effort:** S (apply existing safe patterns)
- **Root cause:** Background-job routes read/mutate the JobStore inconsistently: the optimiser worker subscripts the unlocked .jobs dict in ~20 sites (a concurrent TTL eviction raises KeyError inside the worker, masking the real error and leaving no terminal transition); train/solve status timeouts can't fire because start_time is stamped only after _execute_and_sink; and an error flip omits terminal_reason leaving (status=error, terminal_reason=completed). Root cause: job-state access bypasses the established safe-access + lifecycle-transition patterns.
- **Findings (3):**
  - Optimiser background worker subscripts the unlocked .jobs dict in ~20 sites incl. exception handlers; a concurrent TTL eviction or job clear raises KeyError inside the worker, masking the real error and leaving the job without its terminal transition
  - Train/solve status timeouts cannot fire while the job is still executing the pipeline: start_time is only stamped in _launch_background AFTER _execute_and_sink, so a hung job stays 'running' forever and is never reaped
  - Non-finite training result flips status to 'error' via a raw atomic_update that omits terminal_reason, leaving the stale terminal_reason='completed'
- **Files:** `src/haute/routes/_optimiser_service.py`, `src/haute/routes/_job_store.py`, `src/haute/routes/_train_service.py`, `src/haute/routes/modelling.py`, `src/haute/routes/optimiser.py`
- **Fix strategy:** (1) Replace every self._store.jobs[job_id] in the frontier/auto-range worker with the safe pattern already used by the solve path (self._store.jobs.get(job_id, {}) or compute elapsed from worker-local start_time via time.monotonic()-start_time). (2) Stamp start_time+timeout at job-CREATION (TrainService.start create_job payload, inside _start_lock), mirroring the optimiser solve path, so timeouts can reap a job hung during execution. (3) Route the error flip through self._lifecycle.transition(..., to='error', expected_status='completed') (or add terminal_reason='error' to the atomic_update) so status and terminal_reason stay coherent.

### C16 — Frontend source-blind / under-keyed cache identity (stale schema & preview shown as current)

- **Severity:** medium | **Effort:** M
- **Root cause:** Frontend caches under-key on the dimensions that determine freshness: node-level _columns/_availableColumns are source-blind and not invalidated on active-source change (editors/edge-join then use the previous source's schema); the preview LRU slot is keyed by nodeId alone though freshness depends on (structuralVersion, source, rowLimit); hashConfig uses 32-bit djb2 for solve/train/explore staleness (collidable). Root cause: cache keys omit (source, rowLimit) / use a too-narrow digest, mirroring the backend fingerprint-completeness theme on the client.
- **Findings (3):**
  - Node-level _columns / _availableColumns are source-blind and are NOT invalidated when the active source changes; editors and edge-join validation then operate on the previously-previewed source's schema
  - Preview LRU cache slot is keyed by nodeId alone while freshness depends on (structuralVersion, source, rowLimit); toggling source/rowLimit evicts the other context's preview and forces a full refetch each toggle
  - hashConfig uses 32-bit djb2 over JSON.stringify(sortKeys(config)) for solve/train/explore staleness; a collision makes a config change read as 'not stale', showing an outdated result as current
- **Files:** `frontend/src/hooks/usePipelineAPI.ts`, `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/stores/useSettingsStore.ts`, `frontend/src/components/Toolbar.tsx`, `frontend/src/panels/NodePanel.tsx`, `frontend/src/utils/edgeJoinValidation.ts`
- **Fix strategy:** (1) Make _columns source-aware and strip the ephemeral schema fields (_columns/_availableColumns/_schemaWarnings) in setActiveSource, mirroring useDataInputColumns' `${dataInput}:${activeSource}` keying and storePreview's source recording. (2) Key previews by `${nodeId}|${source}|${rowLimit}` (composite), have setPreview/getPreview match on source+rowLimit, bound per-context via trimCacheByRecency, and update the two call-site guards. (3) Replace djb2 staleness with exact canonical-config comparison (reuse utils/graphSnapshot.ts serializeSnapshot) or a 128-bit hash. Browser-only severity (never persists wrong prices).

### C17 — Frontend WebSocket sync soundness (wrong/stale graph applied to canvas)

- **Severity:** medium | **Effort:** S-M (four localized guards in the WS sync hook)
- **Root cause:** useWebSocketSync apply-decisions use fail-open / unsound heuristics: a parse_error during the async dagre await doesn't bump graphUpdateSeq (an in-flight graph_update applies afterward and clears the parse-error banner); isCurrentSourceFile matches absolute vs relative paths by bare suffix and returns true on blank input (cross-pipeline apply); normalizeEdges admits dangling endpoints; the graph-wide hasPositions heuristic leaves legitimately origin-placed/new nodes overlapping at the origin. Root cause: the WS sync layer trusts fail-open identity/ordering/endpoint heuristics instead of fail-closed sound checks.
- **Findings (4):**
  - A parse_error frame arriving during the async dagre layout await does not bump graphUpdateSeq, so an in-flight graph_update for the same file still applies afterward and clears the parse-error banner
  - isCurrentSourceFile treats an absolute path and a relative path as the same file by pure suffix match and returns true whenever either side is blank/unnormalizable; an external graph_update can apply to the wrong open pipeline
  - normalizeEdges strips type/animated but never validates that edge.source/target nodes exist or that handles are live ports; a dangling edge is admitted, indexed by cascade/trace, and re-emitted by codegen
  - WS graph_update layout decision uses a graph-wide hasPositions flag; a legitimately origin-placed or newly added (0,0) node is left overlapping at the origin
- **Files:** `frontend/src/hooks/useWebSocketSync.ts`, `frontend/src/utils/graphHelpers.ts`, `frontend/src/hooks/usePipelineAPI.ts`
- **Fix strategy:** (1) In the parse_error branch, bump ++graphUpdateSeq (same counter graph_update captures) so a resumed in-flight graph_update is invalidated and the parse-error banner stands. (2) Make isCurrentSourceFile fail-closed: when both sides normalize to blank, return false; for absolute-vs-relative, require the absolute (after stripping a known project-root prefix) to equal the relative — no bare endsWith. (3) Add sanitizeImportedEdges(nodes, edges) at the import/WS choke points dropping (with a visible warning) edges whose endpoints/handles don't exist. (4) Make the layout decision per-node (anchor known positions, run ELK only for nodes lacking a real position). Browser-only severity.

### C18 — Security boundary hardening (executor path containment + reflected request-id + unpickler allowlist)

- **Severity:** medium | **Effort:** S (path-resolution + request-id); M (unpickler allowlist)
- **Root cause:** Trust-boundary defenses are weaker at the execution layer than at the HTTP layer: the executor resolves config paths with enforce_project_root defaulting False (vs routes' True) → arbitrary-file read if a graph's config is attacker-influenced; _RequestIdMiddleware reflects an unbounded client x-request-id into logs and the response header (log/header injection); safe_unpickle allows entire numpy/sklearn/scipy/pandas/joblib trees. Root cause: containment/validation is applied at the route boundary but not consistently at the execution / input-trust boundary.
- **Findings (3):**
  - Executor resolves pipeline config file paths with enforce_project_root defaulting False (prefer='project'), unlike HTTP routes which pass True — arbitrary-file read at execution if a graph's config is attacker-influenced
  - _RequestIdMiddleware trusts a client-supplied x-request-id verbatim (no length/charset bound) and reflects it into every structlog line and back into the response header — log/response-header injection
  - safe_unpickle/safe_joblib_load allow the entire numpy/sklearn/scipy/pandas/joblib trees, so __reduce__ payloads of allowlisted classes still drive object construction from externalFile pickle paths
- **Files:** `src/haute/execution.py`, `src/haute/_path_resolution.py`, `src/haute/server.py`, `src/haute/_sandbox.py`, `src/haute/_io.py`
- **Fix strategy:** (1) Flip the executor's two resolve_runtime_file_path calls (execution.py:178-184, 405-413) to enforce_project_root=True and add an is_relative_to(root) gate, making containment a property of the EXECUTION boundary not just routes. (2) In _RequestIdMiddleware, accept the inbound x-request-id only if it matches ^[A-Za-z0-9._-]{1,128}$, else regenerate uuid4().hex[:12] (loud, no silent fallback). (3) Defense-in-depth (not emergency): tighten the unpickler allowlist to explicit (module, qualname) pairs for the concrete model/data classes actually loaded. Note the path-containment item is the only one that is silent-wrong-adjacent (arbitrary read); the other two are loud-or-exploit hardening.

### C19 — Resource-leak / unbounded-cache hardening (long-lived server hygiene)

- **Severity:** low | **Effort:** S (LRU swap); GPU-fit item is intentionally no-op
- **Root cause:** Several module-global or per-job resources lack bounds/cleanup on long-lived servers: the sandbox _validation_cache is an unbounded module-global keyed by full code string; a cancelled GPU CatBoost fit can leak a worker thread/Pool/train_dir (irreducible — CatBoost fits are uninterruptible). Root cause: caches/threads accumulate without the codebase's existing bounded-LRU / accepted-leak-documentation pattern.
- **Findings (2):**
  - _validation_cache is an unbounded module-global keyed by the full code string; long-lived servers accumulate one entry per distinct previewed/traced code fragment
  - Cancelled GPU CatBoost fit can leak a live worker thread, the held Pool/model, and the train_dir
- **Files:** `src/haute/_sandbox.py`, `src/haute/modelling/_algorithms.py`
- **Fix strategy:** (1) Replace the bare _validation_cache dict with the existing haute._lru_cache.LRUCache (mirroring _feature_validation_cache), bounded to ~128. (2) For the GPU-fit leak, NO functional change is recommended — deleting train_dir under a live writer would corrupt files (strictly worse); it is the accepted outcome of an irreducible constraint and is already documented+tested. Defense-in-depth only: tag the leaked thread/train_dir for observability. This cluster is largely hardening / accept-and-document.

### C20 — Platform numerical/concurrency edge hardening (cgroup RAM, Windows file-sharing, eventbus typing)

- **Severity:** medium | **Effort:** M (cgroup read); S (others)
- **Root cause:** Assorted low-blast platform edges with no shared parent beyond 'environment-specific assumptions': available_ram_bytes reads host /proc/meminfo (cgroup-blind) and silently falls back to 4 GiB (wrong downsample in containers/CI); databricks fetch_and_cache atomic Path.replace raises PermissionError on Windows when a reader holds the parquet (no FILE_SHARE_DELETE); EventBus GraphUpdatePayload under-declares graph_fingerprint so a type-checker can't catch a narrow subscriber.
- **Findings (3):**
  - available_ram_bytes reads host /proc/meminfo MemAvailable (ignores cgroup memory limits) and silently falls back to a fixed 4 GiB, driving wrong downsample decisions in containers/CI
  - fetch_and_cache atomic Path.replace can raise PermissionError on Windows when a concurrent reader has the cache parquet open (no FILE_SHARE_DELETE), turning a fetch into a 500
  - EventBus GraphUpdatePayload TypedDict declares only {graph, source_file} but the watcher publishes graph_fingerprint too; the @overloads advertise the narrow contract while the impl widens to dict[str,Any]
- **Files:** `src/haute/_ram_estimate.py`, `src/haute/_databricks_io.py`, `src/haute/_event_bus.py`, `src/haute/server.py`
- **Fix strategy:** Independent small fixes: (1) in available_ram_bytes, read cgroup v2 memory.max/memory.current (fallback v1 limit_in_bytes/usage_in_bytes) and clamp to min(MemAvailable, cgroup_headroom); reconsider the silent 4 GiB fallback (log loudly). (2) Wrap tmp_path.replace(out_path) in a short bounded win32-only retry on PermissionError, raising a domain FetchIntegrityError if it still fails. (3) Add graph_fingerprint: str to GraphUpdatePayload so the declared contract matches the publisher. Each independent; low coordination.
