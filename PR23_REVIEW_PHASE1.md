# PR #23 full code review — Phase 1 candidate findings (pre-verification)

Branch `wave-2-cache-integrity` vs `main`, **including uncommitted working-tree changes** (265 files, +33,442/−3,276 committed, plus ~625 working-tree lines).

These are **raw candidates** from 19 parallel finder angles (max-effort recall mode: surface everything, verify later).
Every candidate below is being independently verified in Phase 2 (CONFIRMED / PLAUSIBLE / REFUTED with quoted evidence);
expect a meaningful fraction to be refuted or downgraded. The final ranked report (≤15 findings) follows after the Phase 3 gap sweep.

Line numbers refer to the current working-tree files.

## Candidate counts by angle

| Angle | Candidates |
|---|---|
| A1 — Line-by-line: core data & caching | 6 |
| A2 — Line-by-line: parser & codegen | 6 |
| A3 — Line-by-line: modelling & training | 4 |
| A4 — Line-by-line: rating, trace & optimiser | 5 |
| A5 — Line-by-line: server, routes, deploy, git & security | 4 |
| A6 — Line-by-line: frontend | 5 |
| T1 — Test quality: backend pytest suites | 5 |
| T2 — Test quality: frontend vitest/Playwright | 6 |
| B1 — Removed-behavior audit: backend | 7 |
| B2 — Removed-behavior audit: frontend & tests | 4 |
| C1 — Cross-file tracer: backend | 4 |
| C2 — Cross-file tracer: frontend/API boundary | 4 |
| D1 — Python pitfalls | 6 |
| D2 — TS/React pitfalls | 5 |
| E — Wrapper/proxy/cache correctness | 8 |
| Cleanup — Reuse | 6 |
| Cleanup — Simplification | 8 |
| Cleanup — Efficiency | 5 |
| Cleanup — Altitude | 8 |
| **Total** | **106** |

## Independently corroborated candidates

The same mechanism surfaced from multiple independent angles (a strong pre-verification signal):

| Candidate | Found by |
|---|---|
| ScenarioExpanderEditor −0 sign flip (Object.is(-0, 0)) | A6, C2, D2 |
| Trace-correlation relaxed row match enumerates 2^n column subsets | A4, D1, Efficiency |
| CacheFetchButton stale-response race across resourceKey switches | A6, D2, E |
| Unmemoized full-file sha256 re-hash on JSON cache validity checks (event loop) | A1, D1, Efficiency |
| `testserver` Host-header escape hatch bypasses session auth | A5, Altitude |
| _stat_gated_runtime_path_fingerprint duplicates the new StatGatedCache | Reuse, Simplification, Altitude |
| _swap_dir_into_place duplicates mirror_cache_to_committed's rename dance | Reuse, Altitude |
| Legacy rating-table string keys no longer match canonicalised frame keys | A4, B1 |
| Modelling export route now 500s on missing target | B1, C1 |
| WS resync-on-reconnect false 'changed on disk' banner / redundant re-apply | A6, C2 |
| materialization_lock holds the global guard while blocking on a per-key lock | A1, E |
| Git push failures now raise everywhere; pushed/push_error soft channel dead | B1, C1, Simplification |

## A1 — Line-by-line: core data & caching

*Scope: _json_shred, _cache, _dataframe_execution_cache, _stat_gated_cache, chunking, projection, execution, executor, _builders.*

### A1.1 `src/haute/projection.py:1088` — _ordered_expression_demands' rename branch never demands the rename's source columns, so projection strips a column the node's rename() still reads and a previously-working pipeline fails with ColumnNotFoundError.

**Confidence (finder):** high

**Failure scenario:** Polars node code `df = df.rename({'customer_id': 'cid'})` followed by `df = df.with_columns((pl.col('premium')*2).alias('p2'))`, with downstream demanding only {'p2'}: backward walk computes parent demand {'premium'} (rename translation touches only demanded names; unlike the filter/with_columns/select branches it never unions the rename's read-set {'customer_id'}), the parent edge is projected to ['premium'], and at execution polars rename (strict=True default) raises ColumnNotFoundError('customer_id') — the same graph succeeds unprojected. Even `rename({'a':'a'})` (no-op pair dropped by _frame_rename_mapping) triggers it.

### A1.2 `src/haute/executor.py:1385` — resolve_sink_output_path routes sink OUTPUT paths through resolve_runtime_file_path, whose existing-file-wins input heuristic silently redirects the write to a same-named project-root file and clobbers it.

**Confidence (finder):** medium

**Failure scenario:** Server sink route passes project_root=cwd. Pipeline A at project root writes &lt;root&gt;/outputs/out.parquet; pipeline B lives in pipelines/ with sink path 'out'. For B, pipeline candidate &lt;root&gt;/pipelines/outputs/out.parquet does not exist yet but project candidate &lt;root&gt;/outputs/out.parquet exists, so resolve_runtime_file_path's 'existing files win' loop (its lines 126-131) returns the project candidate despite prefer='pipeline' — B's sink silently overwrites A's output instead of creating its own pipeline-relative file, and the target flips depending on what files happen to exist.

### A1.3 `src/haute/_dataframe_execution_cache.py:432` — materialization_lock acquires the per-key lock while still holding the global _materialize_locks_guard, so one same-key waiter stalls materialization of every other key for the full duration of the first writer's parquet sink.

**Confidence (finder):** medium

**Failure scenario:** Thread T1 holds key X's materialization lock during a multi-minute bounded_sink. T2 requests the same key X: inside `with self._materialize_locks_guard:` it blocks on `lock.acquire()` while holding the guard. T3 materializing unrelated key Y now blocks on the guard until T1 finishes, serializing all dataframe-cache materializations behind one write — directly contradicting the docstring's 'while allowing different keys' (this touched function's window logic was rewritten around the unchanged acquire-inside-guard lines).

### A1.4 `src/haute/_json_shred.py:921` — build_per_port_cache's swap leaks uuid-named .build-tmp-/.build-old- directories permanently: cleanup of the unique names never happens on rename failure or locked-backup rmtree, and later builds only delete the legacy fixed-name dirs.

**Confidence (finder):** medium

**Failure scenario:** On Windows a concurrent preview holds a parquet open in the live cache dir; `_rename_dir_with_retry(live_dir, backup)` or the backup `shutil.rmtree(backup, ignore_errors=True)` fails after the ~185 ms of retries. The fully-written `json_&lt;hash&gt;.build-tmp-&lt;uuid&gt;` dir (swap is outside the `except BaseException: rmtree(tmp_dir)` block, line 873) and/or the full-copy `json_&lt;hash&gt;.build-old-&lt;uuid&gt;` backup are left on disk; subsequent builds clean only the legacy `.build-tmp`/`.build-old` names (lines 831/909), so every such failure permanently leaks a complete cache copy under .haute_cache/working/.

### A1.5 `src/haute/_json_shred.py:878` — _data_file_matches has no memo, so once a deploy copy shifts the data file's mtime away from the recorded value, every is_per_port_cache_valid call re-SHA256s the entire raw JSON data file.

**Confidence (finder):** low

**Failure scenario:** Deployed container: docker COPY preserves content but changes mtime_ns relative to the committed meta.json signature. The size check passes, the mtime fast path never matches again (the file is never rewritten), so every load_v2_api_source on a preview/batch cache miss falls through to `_hash_file(data_path)` — a full read+hash of a potentially multi-GB JSON file per execution, unbounded and unmemoised (unlike _stat_gated_runtime_path_fingerprint), turning the intended stat-fast validity check into per-request latency that scales with data size.

### A1.6 `src/haute/_stat_gated_cache.py:63` — StatGatedCache.get_or_load can return a value loaded under a stale gate to waiters that observed a newer gate, because the post-load gate recheck compares against the waiter's own loop-top stat rather than detecting the cross-thread overwrite order.

**Confidence (finder):** low

**Failure scenario:** T1 stats gate G1, starts loading under the key lock; the file is replaced (gate G2); T2 stats G2, waits on the load lock. T1 finishes loading old bytes, re-stats G2 != G1, retries, re-loads new bytes and caches (G2, new). T2 acquires the lock, re-checks the cache for G2 and correctly returns the new value — but if instead T1's second load races another replacement back to identical (mtime_ns,size) (e.g. same-size rewrite within the filesystem's mtime granularity on coarse-timestamp volumes), T1 caches stale bytes under a gate T2 trusts, and both threads serve the stale contract until the next metadata change.

## A2 — Line-by-line: parser & codegen

*Scope: _parser_regex, _ast_helpers, _code_extraction, codegen, _sandbox, schemas.*

### A2.1 `src/haute/_parser_regex.py:153` — _has_backslash_continuation_before treats a backslash at the end of a comment (or any non-code text) on the previous line as a line continuation, so a valid top-level pipeline.connect() edge is silently skipped by the fallback parser instead of recovered or failed loud.

**Confidence (finder):** medium

**Failure scenario:** File with an unrelated syntax error contains '# disable next line to unwire \' on the line directly above 'pipeline.connect("a", "b")'; _is_top_level_statement_anchor returns False, _iter_connect_anchor_matches yields nothing, the edge vanishes from the rendered graph, and the next save rewrites the file without the connect line — exactly the silent edge loss this PR claims to eliminate (the healthy AST parser extracts this edge since a comment backslash is not a continuation).

### A2.2 `src/haute/_parser_regex.py:298` — _scan_call_end tracks strings but not # comments, so an unbalanced paren inside a comment within a multi-line connect()/Pipeline()/decorator argument list corrupts the depth count and aborts the entire fallback parse with a misleading ParseError pointing at syntactically valid code (sibling helpers _parenthesized_wrapper_depth_before/_parenthesized_wrapper_tail_closes do skip comments).

**Confidence (finder):** medium

**Failure scenario:** Broken-elsewhere file contains 'pipeline.connect(\n    "a",  # step 1)\n    "b",\n)' — the ')' in '# step 1)' closes the scan early, the truncated span fails ast.parse, and _find_connect_calls raises ParseError 'fix the syntax error at the connect() call' for a perfectly valid call, so the GUI gets no graph at all; same mechanism falsely kills _recover_pipeline_meta and _recover_decorator_text (e.g. '# 1) first factor' annotations inside a multi-line decorator kwargs list).

### A2.3 `src/haute/_parser_regex.py:491` — _find_decorated_def matches the def signature with a single-line regex, so a decorated function whose parameter list spans multiple lines (e.g. black/ruff-wrapped long signatures) now raises ParseError and aborts the whole fallback parse, where the old _RE_DECORATOR ([^)]* crosses newlines) recovered the node.

**Confidence (finder):** medium

**Failure scenario:** User formats a generated file with ruff (long multi-input node becomes 'def join_all(\n    quotes: pl.LazyFrame,\n    rates: pl.LazyFrame,\n) -&gt; pl.LazyFrame:'), later introduces a syntax error elsewhere; re.match(r'def\s+(\w+)\s*\(([^)]*)\)') fails on the line 'def join_all(' and ParseError propagates out of fallback_parse, so the GUI cannot render any graph for the broken file even though the node is fully recoverable (and was recovered before this PR).

### A2.4 `src/haute/_parser_regex.py:712` — fallback_parse sets config = loaded_config for sidecar-config node types without extracting config['code'] from the parsed body, unlike the healthy path (_config_builder lines 395-408), so user code in dataSource/externalFile/modelScore/scenarioExpander/ratingStep nodes is silently emptied when a file round-trips through the fallback parser.

**Confidence (finder):** medium

**Failure scenario:** A pipeline with a dataSource node containing custom post-load code gets a syntax error in an unrelated node; the GUI loads via fallback_parse, the node's code box is empty (JSON sidecar never stores the 'code' key), and saving re-emits the .py without the user's code — silent data loss contradicting the PR's stated no-silent-drop contract, despite block['body_text'] being available and parseable.

### A2.5 `src/haute/_code_extraction.py:630` — _match_external sets saw_load=True for any 'with open(' in the body, so the new no-load import-preservation rule (restore first_import_idx) is defeated when the user's own code uses with open(): the user's leading imports and the entire with-block are still silently stripped as boilerplate in bodies that contain no generated load.

**Confidence (finder):** low

**Failure scenario:** Hand-written external-file node body 'import json\nwith open("x.json") as f:\n    raw = json.load(f)\ndf = pl.from_dicts(raw).lazy()' (no obj = load_external_object line) parses with start_idx pointing at the df line — the import and the with-block are deleted from the code box and the next save emits a file whose user code references undefined 'raw'/'json', silently corrupting the node despite the docstring's promise that bodies with no load keep their imports.

### A2.6 `src/haute/_parser_regex.py:544` — _find_function_blocks indexes into source.splitlines() using def_line_idx computed by counting only '\n' characters (_find_decorated_def line 498), so any \x0c/\u2028/\x85 character earlier in the file (splitlines splits on these, count('\n') does not) shifts the index and silently attributes the wrong lines as the function body — the same line-numbering divergence this PR fixed in codegen.py by switching to split('\n').

**Confidence (finder):** low

**Failure scenario:** A docstring containing a literal form-feed character (passes through _sanitize_description unescaped) appears before a decorated node in a file that later gains a syntax error; in fallback parse, splitlines() has one extra entry per form feed, so body_lines for the node start one line too early/late, producing a node whose body_text belongs to neighbouring code and round-tripping that wrong code into the node's config.

## A3 — Line-by-line: modelling & training

*Scope: modelling/*, _mlflow_io, _model_scorer, _model_explainability, _databricks_io.*

### A3.1 `src/haute/modelling/_metrics.py:108` — Negating the sort key in _aggregated_lorenz_points (`-sort_key` inside np.lexsort) wraps around for unsigned-integer targets, so the perfect-model Gini normaliser and perfect Lorenz curve sort y=0 rows first and order positives wrongly, silently corrupting the normalised Gini.

**Confidence (finder):** medium

**Failure scenario:** Train a frequency model whose target column is UInt32 (e.g. claim_count produced by a polars group_by count upstream); _compute_metrics passes diag_df[target].to_numpy() (uint32, never cast to float) into _gini, whose perfect curve calls _aggregated_lorenz_points(y_true, y_true, w); -uint32 wraps (0-&gt;0, 1-&gt;4294967295, 2-&gt;4294967294) so the 'perfect' ordering becomes [zeros first, then descending positives], perfect_gini gets the wrong magnitude/sign and the reported Gini is a plausible-looking but wrong number (compute_lorenz_curve's plotted perfect curve is corrupted the same way; a Boolean target instead raises TypeError on the unary minus).

### A3.2 `src/haute/modelling/_export.py:81` — The exported training script only renders variance_power when loss_function == 'Tweedie', so for GLM Tweedie configs (loss_function is None, var_power top-level) TrainingJob.variance_power is None and the tweedie_deviance metric is computed at the default p=1.5 instead of the configured power — drifting from live training, which this PR's shared-builder refactor was supposed to make impossible.

**Confidence (finder):** medium

**Failure scenario:** Config: algorithm='glm', family='tweedie', var_power=1.7, metrics=['tweedie_deviance', 'gini']; live training builds kwargs with variance_power=1.7 (build_training_job_kwargs falls back to var_power) and computes tweedie_deviance at p=1.7, but generate_training_script omits the variance_power kwarg (loss_function != 'Tweedie'), so the exported script trains the identical model (params carry var_power) yet silently reports tweedie_deviance at p=1.5 — different deviance numbers for the same model with no error.

### A3.3 `src/haute/modelling/_training_job.py:1218` — The new non-finite-row discipline filters rows for compute_metrics and the Lorenz curve, but compute_residuals_histogram at line 1218 still receives the unfiltered arrays and np.histogram raises ValueError on any NaN/Inf residual, crashing the whole training run after a successful fit — and it sits BEFORE the new lorenz mask, so the mask is unreachable for exactly the data it protects against.

**Confidence (finder):** medium

**Failure scenario:** A Poisson/Tweedie GLM with an exp link overflows on one extreme row, producing y_pred=inf on the diagnostics partition; compute_metrics (line 1142) filters the row and surfaces non_finite_rows_filtered, but compute_residuals_histogram(y_true, y_pred, w) at line 1218 computes residuals containing inf and np.histogram raises 'autodetected range ... is not finite' ValueError, failing the entire run post-fit (model lost) — while rows with only non-finite WEIGHT instead flow into np.bincount weights and emit silent NaN weighted_counts; compute_double_lift/compute_ave_per_feature (lines 1177-1187) likewise ingest the unfiltered arrays and render NaN deciles silently.

### A3.4 `src/haute/_mlflow_io.py:1027` — _positive_class_proba_vector assumes a (n, 1) predict_proba matrix is the positive-class column, but sklearn-style classifiers fitted on a single class return p(classes_[0]) — i.e. the probability of the ONLY (possibly negative) class — which gets silently published as the binary positive-class probability.

**Confidence (finder):** low

**Failure scenario:** A pyfunc/sklearn classifier whose training slice contained only the negative class (classes_=[0]) returns predict_proba of shape (n,1) with all values 1.0; both the eager and batch scorers write '&lt;output_col&gt;_proba' = 1.0, asserting 100% positive-class probability for a model that never saw a positive, and the wrong-but-plausible probability feeds downstream pricing instead of raising like the multiclass branch does.

## A4 — Line-by-line: rating, trace & optimiser

*Scope: _rating, _trace_waterfall, _trace_correlation, trace, routes/optimiser, routes/_optimiser_service.*

### A4.1 `src/haute/_trace_correlation.py:350` — Relaxed row matching enumerates combinations(shared_columns, width) for every width from n-1 down to 1, which is O(2^n) DataFrame filters when no column subset matches, replacing the old O(n^2) column-removal loop and hanging the trace endpoint on wide frames.

**Confidence (finder):** high

**Failure scenario:** User full-joins two frames (how='full' is allowed by _edge_join) with a 30-column base side and clicks a right-only output row; the base-side columns in the child row are all null, no base row matches at any width, so _find_matching_row runs ~2^29 (~5e8) indexed.filter() calls — the trace request pins the worker for hours (effective denial of service); even a node modifying 5 of 40 shared columns costs C(40,5)=658k filters per trace click.

### A4.2 `src/haute/_rating.py:565` — Switching both join sides from plain Utf8 casts to _rating_key_expr collapses float frame values to integer digit strings while string entry keys stay verbatim, so pre-existing tables keyed with float-formatted strings stop matching, and when a usable defaultValue exists the miss guard (line 577) is skipped so every row silently takes the default.

**Confidence (finder):** high

**Failure scenario:** A saved pre-PR rating table on a Float64 'age' column with entry keys "25.0"/"30.0" (the only form that matched the old cast) and defaultValue 1.0: after upgrade the frame canonicalises 25.0-&gt;"25" but entries stay "25.0", every policy misses, the guard is not wired because default_val is not None, and the whole book is silently rated at factor 1.0 with no error or warning — wrong premiums portfolio-wide (the no-default variant errors loudly per test_string_keys_are_verbatim_never_collapsed, but the default variant is silent).

### A4.3 `src/haute/_trace_waterfall.py:303` — build_waterfall_from_steps chains 'consecutive observed values' across topologically-ordered steps even when those steps live on different branches that each carry a same-named column, fabricating implied factors between unrelated quantities while still passing the final reconciliation because each cumulative snaps to its observation.

**Confidence (finder):** medium

**Failure scenario:** Two sources both carry 'premium' (A=100, B=55, B earlier in topo order) and are edge-joined keeping A's value; B's branch modifies its premium, so the waterfall opens base=55 (B), shows a fabricated x1.82 'multiply' at the join (input_row takes the join-parent's value when that parent is first in parents_of), snaps to 100, reconciles with the target value 100, and renders a confidently wrong chart; with the opposite parent order it instead returns a spurious WaterfallReconciliationError payload for a healthy pipeline.

### A4.4 `src/haute/_rating.py:370` — The non-float branch of _rating_key_expr (col.cast(pl.Utf8)) disagrees with normalise_rating_key's str(value) fallback for Datetime columns (polars emits '2024-05-01 12:30:00.000000', Python str() emits '2024-05-01 12:30:00'), breaking the documented twin contract that the new ratebook canonical-key save path relies on.

**Confidence (finder):** low

**Failure scenario:** A ratebook factor column of dtype Datetime: _ratebook_factor_level_key saves __factor_group__ keys in str() form ('2024-05-01 12:30:00'), but at apply time _apply_rating_table canonicalises the frame to the '.000000'-suffixed form, so every row misses the lookup and the onMissing='neutral' path silently rates every quote at factor 1.0 (warning log only) instead of the solved relativities.

### A4.5 `src/haute/trace.py:528` — output_value is taken from the jsonified cached row, so Int64 traced values beyond 2^53 arrive as strings (to_json_safe) and _as_finite_float rejects them, silently disabling the waterfall (returns None) for big-integer monetary/count columns rather than building or loudly failing it.

**Confidence (finder):** low

**Failure scenario:** Tracing an Int64 column whose clicked value is 9_007_199_254_740_993 (&gt;2^53): to_json_safe stringifies it, build_waterfall_from_steps gets final_output_value='9007199254740993', _as_finite_float returns None, and the waterfall feature silently vanishes for that column with no error payload.

## A5 — Line-by-line: server, routes, deploy, git & security

*Scope: server, _git, _local_security, cli/_serve, routes/*, deploy/*, _ram_estimate.*

### A5.1 `src/haute/_local_security.py:82` — The `testserver` harness escape hatch keys off the fully client-controlled Host header, allowing complete bypass of the local session-token auth.

**Confidence (finder):** high

**Failure scenario:** User opts into a non-loopback bind (e.g. `--host 0.0.0.0` or `--host 192.168.1.5`); `_trusted_hosts_for_bind` puts `testserver` in HAUTE_TRUSTED_HOSTS (or `*`). A direct (non-browser) network attacker sends `GET /api/...` with `Host: testserver` and no Origin header. TrustedHostMiddleware admits `testserver`; LocalSessionMiddleware line 108 (and `websocket_rejection_reason` line 132) treat it as the test harness and skip BOTH the Origin check and the token check, granting full unauthenticated access to the Polars-execution/file-write API and the /ws/sync socket. Without this path the token correctly gates even no-Origin clients, so this is the one hole that nullifies the protection the PR adds for exposed binds.

### A5.2 `src/haute/cli/_serve.py:293` — `_trusted_hosts_for_bind` returns `"*"` for every IPv6 bind address, disabling TrustedHostMiddleware Host validation entirely even for a specific public IPv6.

**Confidence (finder):** medium

**Failure scenario:** User binds to a routable IPv6 address; `ipaddress.ip_address(...).version == 6` short-circuits to `return "*"` before the loopback/scoped-list logic, so HAUTE_TRUSTED_HOSTS becomes `*` and TrustedHostMiddleware accepts any Host header (no DNS-rebinding protection), unlike the scoped `localhost,127.0.0.1,testserver,&lt;ip&gt;` list produced for an equivalent IPv4 bind. Combined with the testserver bypass this leaves an IPv6-exposed server fully open.

### A5.3 `src/haute/_git.py:898` — `archive_branch` deletes/renames the remote branch before the local checkout+rename, so a failing local step leaves remote and local inconsistent.

**Confidence (finder):** medium

**Failure scenario:** User archives the current branch while it has uncommitted changes that conflict with the default branch. The remote push of `branch:refs/heads/&lt;archive&gt;` and the `--delete` of the remote branch both succeed first; then `git checkout default` (line 915) fails on the dirty working tree and raises, so `git branch -m` never runs. Result: the remote original branch is gone (renamed to archive) while the local branch is untouched — an inconsistent, confusing state. The prior code did the local rename first and only then touched the remote, so a local failure left the remote intact.

### A5.4 `src/haute/server.py:403` — WebSocket auth gate is wrapped in `if headers is not None and query_params is not None`, a fail-open that accepts the socket unchecked if those attributes are ever absent.

**Confidence (finder):** low

**Failure scenario:** `websocket_rejection_reason` is only consulted when both `getattr(websocket, 'headers')` and `query_params` are non-None; if either were ever None the code skips straight to `websocket.accept()` with no origin/token check. Starlette always populates these so it is not currently reachable, but the construct fails open rather than loudly, contradicting the project's no-silent-fallback rule for a security gate.

## A6 — Line-by-line: frontend

*Scope: panels/editors, panels/modelling, hooks, api/client, trace components.*

### A6.1 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:30` — Object.is(-0, 0) in activeRangeDraft discards the draft mid-typing, so the leading minus is swallowed when typing negative decimals starting with "-0".

**Confidence (finder):** high

**Failure scenario:** User types "-0.5" into Min: at "-0" updateRangeDraft calls onUpdate(min_value, -0); the config round-trips as committedText String(-0)==="0", and activeRangeDraft compares Object.is(-0, 0)===false so the draft "-0" is dropped and the input visibly resets to "0"; the user finishes typing ".5" and the committed range becomes +0.5 instead of -0.5 — a silently wrong scenario range (=== instead of Object.is would preserve the draft).

### A6.2 `frontend/src/hooks/useWebSocketSync.ts:153` — The resync sent on every WebSocket (re)connect echoes the unchanged on-disk graph back as a graph_update, which the dirty guard misreports as 'Pipeline changed on disk' and which on a clean canvas redundantly re-applies the graph with a toast and fitView jump.

**Confidence (finder):** medium

**Failure scenario:** User edits the canvas (dirty=true); the dev server restarts or the WS drops and reconnects -&gt; onopen sends {type:'resync'} -&gt; server replies with the current (unchanged) file graph -&gt; blockDirtyGraphUpdate fires a warning toast plus a persistent 'Pipeline changed on disk (...) while you have unsaved changes. Save or reload...' banner even though nothing changed on disk; on a clean canvas (including every initial page load, since enabled flips after markSaved) the identical graph is unconditionally re-applied with a spurious 'Pipeline updated from file' toast and a fitView viewport reset.

### A6.3 `frontend/src/hooks/useKeyboardShortcuts.ts:136` — Adding !isTyping to the Ctrl+K handler makes the node-search toggle unable to close the palette, because NodeSearch autofocuses its input on open.

**Confidence (finder):** medium

**Failure scenario:** User presses Ctrl+K -&gt; NodeSearch opens and requestAnimationFrame focuses its &lt;input&gt; -&gt; pressing Ctrl+K again hits the global handler with e.target tagName INPUT, so isTyping is true and setNodeSearchOpen(prev =&gt; !prev) is skipped; the documented toggle (close half of the prev =&gt; !prev) is dead in the normal flow and the user must know to press Escape instead.

### A6.4 `frontend/src/components/CacheFetchButton.tsx:91` — The status-load effect has no stale-response guard, so out-of-order getStatus responses across resourceKey changes clobber the newer key's status/error and fire onCacheReady for the wrong resource — now user-visible via the new statusError UI.

**Confidence (finder):** medium

**Failure scenario:** User edits the cache key (e.g. API input path/table name) from A to B: the effect fires getStatus(A) then getStatus(B); A's response resolves last (or rejects, e.g. 404 for the half-typed key) -&gt; setCache(A-status)/setStatusError('Unable to check cache status: ...') overwrite B's correct result, the button turns into the red 'Cache status unavailable' state for a healthy resource, and onCacheReadyRef fires with A's cached status against resource B.

### A6.5 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:26` — A pending invalid/empty Min/Max draft survives switching to a different ScenarioExpander node whose committed text is identical, showing stale draft text against the wrong node, because NodePanel reuses the editor instance (no key by node id) and blur never clears invalid drafts.

**Confidence (finder):** medium

**Failure scenario:** Two scenario-expander nodes both have min_value unset (committedText ""): user types "abc" (or clears a value to "") in node A's Min, then clicks node B -&gt; commitRangeNumber keeps invalid drafts on blur and reconcileRangeDraft keeps the draft because committedText is unchanged, so node B's editor displays A's leftover "abc"/empty text with a red border while B's actual config value is different — the user reads the wrong range for node B.

## T1 — Test quality: backend pytest suites

*Scope: vacuous tests, mock drift, tests that cannot fail.*

### T1.1 `tests/test_dataframe_execution_cache.py:1462` — Concurrency test claims 'two threads build the body only once; materialization_lock serialises same-key writes' but the build_count it carefully tracks (lines 1480-1487) is never asserted, and _build() is invoked eagerly by each worker so it would always be 2; the only assertions (no errors, both collects succeed, entries == 1) hold even with the lock removed.

**Confidence (finder):** high

**Failure scenario:** If materialization_lock were deleted or made a no-op, both threads miss the cache, both sink to the same artifact path, and the second store_artifact replaces the first under the same key — entries == 1, both LazyFrames collect fine on most interleavings, no errors recorded, so the test passes while the single-flight/serialization contract it documents is broken; only a nondeterministic same-file write collision could ever fail it.

### T1.2 `tests/test_security_gaps.py:723` — test_url_scheme_in_read_source uses pytest.raises((ValueError, Exception)), which is equivalent to pytest.raises(Exception) and passes on ANY error, not on the claimed scheme rejection.

**Confidence (finder):** high

**Failure scenario:** If the URL-scheme guard in read_source were removed, polars would actually attempt the http/ftp fetch (a real SSRF) and then raise a network/compute error in CI — still an Exception, so the test passes even though the 'reject before any I/O attempt' behavior is gone; only the duplicate strict-ValueError test in TestSSRFViaFilePath gives real coverage.

### T1.3 `tests/test_server_concurrency.py:58` — test_concurrent_adds_and_discards_preserve_invariant claims to 'hammer ws_clients ... verify no item is lost', but every thread's final operation on its sentinel is a discard, so the asserted end state (sentinel absent) holds by construction under any interleaving and any (lack of) locking, and CPython's GIL-atomic set ops mean no exception can occur either.

**Confidence (finder):** medium

**Failure scenario:** Remove the ws_clients lock entirely (the #6 fix) and the test still passes deterministically: 'adder' ends with discard(s) and 'other' ends with discard(s), so s is always absent at join regardless of races; the behavioral assertions cannot detect a lost update or corruption, leaving only the separate structural getattr-lock test to guard the fix.

### T1.4 `tests/test_security_gaps.py:490` — TestResourceExhaustionConfig claims large configs 'should not crash node construction or codegen', but the tests only build a GraphNode and assert the length/content of the very list they just inserted into a passthrough dict[str, Any] config field — codegen is never invoked and the assertion is tautological.

**Confidence (finder):** medium

**Failure scenario:** If codegen (or any downstream consumer) hung or crashed on a 10k-entry constant/rating/banding config — the actual resource-exhaustion risk named in the docstring — these tests would still pass, because pydantic stores the config dict verbatim and len(list_we_built) == 10_000 can never be false.

### T1.5 `tests/test_host_binding.py:252` — Several non-loopback serve tests (e.g. test_explicit_all_interfaces_emits_warning, test_warning_has_warning_level_not_info, test_handle_serve_warns_on_non_loopback) cause product code _configure_trusted_hosts to set os.environ['HAUTE_TRUSTED_HOSTS']='*' directly without any monkeypatch undo, leaking the wildcard trusted-hosts env var across tests; monkeypatch.delenv(raising=False) on an absent var records no restore, so the leak persists until an unrelated loopback test happens to clear it.

**Confidence (finder):** low

**Failure scenario:** Under test selection (-k), reordering, or if a wildcard-setting test runs last, subsequent test modules in the same process run with HAUTE_TRUSTED_HOSTS='*'; any later test that constructs the server app fresh (first import after these tests) silently builds TrustedHostMiddleware with allow-all, so Host-header rejection behavior is no longer exercised under the configuration the tests believe they pinned.

## T2 — Test quality: frontend vitest/Playwright

*Scope: unasserted claims, mirror-of-implementation tests.*

### T2.1 `frontend/src/__tests__/hooks/useWebSocketSync.test.ts:237` — The 'stops reconnecting' half of the constructor-failure test is never exercised: the test fires no timers after the failure and counts mockWSInstances, a registry the throwing constructor never populates.

**Confidence (finder):** high

**Failure scenario:** If the connect() catch block regressed to also schedule reconnectTimer = setTimeout(connect, backoff) (an infinite retry/toast loop), the test still passes: status is still 'disconnected', the first toast still matches, and mockWSInstances stays 0 because ThrowingWebSocket never pushes instances and vi.advanceTimersByTime is never called to let a retry fire.

### T2.2 `frontend/src/__tests__/hooks/useWebSocketSync.test.ts:280` — The test titled 'keeps the socket connected and reports a failed reconnect resync send' discards the renderHook result and asserts only the error toast, so the 'keeps the socket connected' claim is never checked.

**Confidence (finder):** high

**Failure scenario:** If the onopen send-failure catch in useWebSocketSync regressed to call setStatus('disconnected') or ws.close() (treating a resync send failure as fatal), the test would still pass because it never asserts result.current === 'connected' nor that latestWS().close was not called — only that addToast received the 'send boom' message.

### T2.3 `frontend/src/__tests__/main.test.tsx:33` — The 'mounts App inside the root ErrorBoundary' test never verifies the mounted child is App: it stubs ../App via vi.doMock but then only asserts expect(app.type).toBeTypeOf('function'), which any function component satisfies.

**Confidence (finder):** medium

**Failure scenario:** If main.tsx mounted the wrong component inside the ErrorBoundary (e.g. &lt;ErrorBoundary&gt;&lt;ToastContainer/&gt;&lt;/ErrorBoundary&gt; or a typo'd import), the test stays green because the mocked App default export is never compared against boundary.props.children.type — 'is a function' matches every component.

### T2.4 `frontend/src/panels/modelling/__tests__/PdpTab.test.tsx:9` — The single-grid-point PDP test only asserts an &lt;svg&gt; exists and the empty-state text is absent, never inspecting path/circle coordinates, so a NaN-geometry regression on the one-point path renders a blank chart and still passes.

**Confidence (finder):** medium

**Failure scenario:** If the pre-existing divide-by-zero guard in PdpLineChart (xRange = xMax - xMin || 1, or the yPad fallback) were removed, a one-point grid yields xScale 0/0 = NaN and a path d of 'MNaN,NaN' — the svg element is still present and 'No PDP data for constant_age' is still absent, so the test passes while the chart is visually broken (unlike the LossTab test, which greps path data for NaN/Infinity).

### T2.5 `frontend/src/panels/modelling/__tests__/ChartScaffold.test.tsx:32` — The 'keeps repeated axis constants in one modelling-local module' test merely re-asserts the module's own literal values (mirror-of-implementation), which cannot detect the deduplication the title claims.

**Confidence (finder):** low

**Failure scenario:** If LossTab/PdpTab/other charts reintroduced their own local GRID_COLOR/AXIS_FONT_SIZE duplicates and stopped importing from ChartScaffold (the exact regression the title describes), the test stays green because it only checks that ChartScaffold's exports equal hard-coded copies of themselves; it fails only on intentional value edits.

### T2.6 `frontend/src/utils/__tests__/apiInputPorts.test.ts:59` — The meta-test guarding tracker item 9.3 asserts the test file's source does not contain the exact byte string 'expect(result.edges).toBe(result.edges)', which tests no product behavior and is trivially bypassed by any equivalent tautology.

**Confidence (finder):** low

**Failure scenario:** Reverting the fixed assertion in a syntactically different form — expect(result.edges).toEqual(result.edges), a line-wrapped .toBe call, or const e = result.edges; expect(result.edges).toBe(e) — reintroduces the self-referential tautology while the exact-string check still passes, giving false confidence that the regression is blocked.

## B1 — Removed-behavior audit: backend

*Scope: deleted guards/validations/error paths not re-established.*

### B1.1 `src/haute/_rating.py:551` — The old symmetric plain-Utf8 cast of both rating-join sides was replaced by asymmetric canonicalisation (frame floats collapse 25.0-&gt;"25" but string entry keys stay verbatim), and nothing migrates legacy sidecar/ratebook entries whose keys were persisted as "25.0" by the old str(float) save path (_canonical_sidecar_key only canonicalises non-string values on save; normalise_rating_tables does not canonicalise on load).

**Confidence (finder):** medium

**Failure scenario:** An existing pipeline with a Float64 factor column (e.g. an int column with nulls) whose rating-table sidecar entries were saved pre-PR as keys like "25.0" previously matched (both sides cast to "25.0"); now the frame side canonicalises to "25" while the entry stays "25.0", so every row misses -&gt; RatingTableMissError on every preview/quote (new onMissing default "error"), and the same legacy optimiserApply ratebook artifact silently applies neutral 1.0 instead of the solved factor.

### B1.2 `src/haute/routes/modelling.py:410` — generate_training_script previously tolerated a missing target (config.get("target", "")) and always returned a script; it now delegates to build_training_job_kwargs which raises ValueError, and neither the export_script route nor any global handler converts that to an HTTP 400.

**Confidence (finder):** high

**Failure scenario:** User clicks "Export training script" on a modelling node whose target column is not yet chosen -&gt; unhandled ValueError -&gt; opaque 500 Internal Server Error; the authored message ("Open the config panel and choose a target column") never reaches the user, where previously the export succeeded.

### B1.3 `src/haute/_git.py:564` — Best-effort pushes (_run_git_ok, failures ignored) were replaced by _push_or_raise everywhere, removing the ability to work with a configured-but-unreachable remote; the new GitSaveResponse.pushed/push_error soft-failure fields are never populated with a failure, so the soft path was never wired.

**Confidence (finder):** medium

**Failure scenario:** Offline user (remote configured, network/VPN down): switch_branch auto-commits then _auto_commit's push raises, aborting BEFORE checkout so branch switching is impossible; save_progress commits locally then raises (commit info lost to caller) and the next save raises "No changes to save."; delete_branch/archive_branch are fully blocked by the backup-tag push - all flows that previously completed locally.

### B1.4 `src/haute/modelling/_training_job.py:1372` — Training no longer refreshes the shared feature_contract.json that existing consumers point at (per-model {name}.feature_contract.json is written instead and the legacy file is deliberately left stale with only a server-log warning); no config repointing or consumer-side re-establishment exists.

**Confidence (finder):** medium

**Failure scenario:** A pre-existing modelScore config with feature_contract_path=outputs/feature_contract.json (or a deploy whose _bundle_feature_contract finds that bare-name file): after retraining, scoring/deploy keeps validating quotes against the PRE-retrain contract - false contract-mismatch failures after a feature change, or silently missed drift when features overlap - with nothing but a train-time log warning.

### B1.5 `src/haute/routes/_save_pipeline.py:357` — Dissolve's submodel-file deletion path validation was narrowed to exactly 'modules/&lt;name&gt;.py' (one slash, modules/ prefix) and now hard-fails the whole dissolve, whereas the old code accepted any safe project path via validate_safe_path and gracefully skipped deletion (while still completing the dissolve) on rejection; the parser still accepts any in-project submodel path.

**Confidence (finder):** medium

**Failure scenario:** A hand-authored pipeline.submodel("modules/sub/risk.py") or pipeline.submodel("./modules/risk.py") parses and loads fine, but dissolving that submodel now returns HTTP 400 ("Submodel delete path must be 'modules/&lt;name&gt;.py'") and the dissolve does not happen at all - previously the main file was rewritten and the file deleted (or deletion skipped with a warning).

### B1.6 `src/haute/_local_security.py:74` — The new LocalSessionMiddleware rejects any browser Origin outside {localhost, 127.0.0.1, ::1} with no allowance for the bind host (unlike the trusted-host config, which the CLI extends for non-loopback binds), removing the documented "non-loopback bind is honoured" capability for remote browsers.

**Confidence (finder):** medium

**Failure scenario:** User runs `haute serve --host 0.0.0.0` (explicitly supported with a warning) and opens http://&lt;machine-ip&gt;:8000 from another machine: the SPA loads with a valid injected session token, but every POST /api/* carries Origin http://&lt;machine-ip&gt;:8000 and is rejected 403 "Origin is not trusted", making the remote UI completely non-functional where it previously worked.

### B1.7 `src/haute/_trace_correlation.py:106` — The _is_nan_like equivalence (None ~ NaN treated as equal in trace row matching, and jsonified child NaN collapsing to None) was removed; the new token comparison requires null==null and nan==nan exactly, with no re-established null&lt;-&gt;NaN bridge in _trace_values_match or _build_value_mask.

**Confidence (finder):** low

**Failure scenario:** Tracing across a node that coerces null&lt;-&gt;NaN on a float column (e.g. fill_nan(None), numpy/pandas round-trips in user code, model-score outputs): the parent row holds NaN where the child holds null (or vice versa) -&gt; the 1:1 fast path and exact mask matching now fail on that shared column, so the upstream row resolves as None (unresolved lineage in the trace UI) where it previously correlated.

## B2 — Removed-behavior audit: frontend & tests

*Scope: deleted behavior/assertions not re-established.*

### B2.1 `frontend/src/hooks/useWebSocketSync.ts:124` — The old 'file on disk always wins' WS reconciliation (pinned by the deleted test 'accepts graph update even when the graph was previously dirty') was replaced by a dirty-state block whose banner advises 'Save or reload' — but the banner has no reload affordance, the save route has no disk-freshness check, and the blocked update is never re-delivered after dismissal.

**Confidence (finder):** medium

**Failure scenario:** User has unsaved canvas edits (dirty=true) when the pipeline .py is changed externally (IDE edit, git pull); the graph_update is blocked, the user follows the banner's first suggestion and hits Save, and savePipeline overwrites the external disk edits with the stale browser graph with no conflict detection — silent lost update in the direction the old disk-wins behavior made impossible; if the user instead dismisses the banner, canvas and disk stay divergent with sync dead for that change until a manual reload.

### B2.2 `frontend/src/hooks/useKeyboardShortcuts.ts:143` — Escape previously cleared the trace and closed the side panel regardless of focus; it is now a no-op whenever focus is in an INPUT, TEXTAREA, or .cm-editor, with no replacement keyboard route to close the panel while typing.

**Confidence (finder):** low

**Failure scenario:** User opens a node config panel, clicks into a text input (the most common focus state in this editor), and presses Escape to dismiss the panel/trace as before — nothing happens; they must click outside the input first, and a trace overlay can no longer be dismissed from the keyboard while any editor field has focus.

### B2.3 `frontend/src/api/__tests__/client.contract.test.ts:537` — The negative runtime-contract mutations for gitSave/gitSubmit/gitDeleteBranch were retargeted from the original fields (timestamp: 42, compare_url: 42, branch: 42) to the newly added fields (pushed, push_error, backup_tag), so wrong-type rejection of the original fields is no longer exercised by any test.

**Confidence (finder):** low

**Failure scenario:** A future refactor of parseGitSaveResponse/parseGitSubmitResponse/parseGitDeleteBranchResponse that drops or weakens validation of timestamp, compare_url, or branch (e.g. swapping expectString for a permissive cast) passes the full suite, letting malformed git responses flow typed-but-wrong into GitPanel.

### B2.4 `frontend/src/panels/editors/_shared.tsx:336` — SchemaPreview cells switched from exact String(value) rendering to formatValue(), which rounds floats to 4 fraction digits and adds locale thousands separators, with no full-precision tooltip (unlike StepCard, which gained title-attribute full precision in this same PR).

**Confidence (finder):** low

**Failure scenario:** A rate factor such as 1.00004 or 0.123456 in a data-source schema preview now renders as '1' / '0.1235', so a pricing analyst validating sidecar/source data sees a value that misrepresents the true stored number with no way to recover the exact figure from the preview.

## C1 — Cross-file tracer: backend

*Scope: changed contracts vs callers; API payload shapes.*

### C1.1 `src/haute/routes/modelling.py:410` — POST /api/modelling/export now returns an opaque 500 for a modelling node without a target, because generate_training_script was rewired through build_training_job_kwargs which raises ValueError where the old code defaulted to target='' and the route has no exception handling.

**Confidence (finder):** high

**Failure scenario:** User drops a modelling node and clicks 'Export Script' before choosing a target column -&gt; export_script() calls generate_training_script() -&gt; build_training_job_kwargs() raises ValueError('Modelling config has no target column...') -&gt; no try/except in the route and no app-level ValueError handler (server.py registers none) -&gt; FastAPI 500 Internal Server Error; the authored, actionable message ('Open the config panel and choose a target column') never reaches the user, whereas pre-PR the route succeeded.

### C1.2 `frontend/src/api/types.ts:781` — Backend JsonCacheBuildResponse/JsonCacheStatusResponse gained skipped_records/skipped_rows (W2 2.7 'zero silent record loss' surfacing), but the frontend JsonCacheBuildResponse/JsonCacheStatusResponse interfaces, guards, and ApiInputEditor were never updated to carry or display them.

**Confidence (finder):** medium

**Failure scenario:** User caches a JSONL file containing non-object lines or mixed-shape arrays -&gt; routes/json_cache.py returns skipped_records/skipped_rows (json_cache.py:243-244, 287-288) -&gt; frontend types.ts JsonCacheBuildResponse (line 781) and JsonCacheStatusResponse (line 792) omit the fields, so client/guards drop them and ApiInputEditor shows row counts with no skip warning -&gt; the record loss the wave was built to surface stays silent at the only user-facing hop.

### C1.3 `src/haute/_git.py:564` — switch_branch's auto-commit call doesn't expect _auto_commit's new raising push behavior: with a configured-but-unreachable remote, branch switching now fails outright (after creating the commit) where pushes were previously best-effort via _run_git_ok.

**Confidence (finder):** medium

**Failure scenario:** Analyst works offline (or token expired) with origin configured, has pending edits, clicks a different branch in the Git panel -&gt; switch_branch:564 calls _auto_commit(cwd) with new default push=True -&gt; _push_or_raise raises GitDomainError('Failed to push auto-saved changes. Pull latest changes and retry.') -&gt; route returns 400, checkout never runs -&gt; user cannot switch branches at all while offline, and the suggested remedy (pull) is impossible offline; pre-PR the switch always succeeded with the push silently skipped.

### C1.4 `src/haute/_git.py:596` — GitSaveResponse/GitSubmitResponse gained pushed/push_error fields, but save_progress and submit_for_review raise on push failure instead of returning them, so push_error is a dead channel (always None) and a failed push leaves a committed-but-unpushed state the UI cannot represent.

**Confidence (finder):** medium

**Failure scenario:** Save Progress with an unreachable remote -&gt; commit succeeds (line 588), then _push_or_raise at line 596 raises GitDomainError -&gt; routes/git.py maps it to 400, so the new pushed=False/push_error response contract (schemas.py:1285-1287, frontend guards.ts:1841) is unreachable -&gt; user retries Save and now gets 400 'No changes to save.' because the commit already landed; their work is committed locally with no UI signal it was never pushed.

## C2 — Cross-file tracer: frontend/API boundary

*Scope: backend payloads vs frontend types/guards/consumers.*

### C2.1 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:30` — activeRangeDraft uses Object.is to compare draft and committed numbers, and Object.is(-0, 0) is false, so typing a negative decimal starting with "-0" gets its minus sign silently eaten.

**Confidence (finder):** high

**Failure scenario:** User types "-0.5" into Min: at keystroke "-0" updateRangeDraft commits -0 via onUpdate (line 111); config re-renders committedMinText = String(-0) = "0"; activeRangeDraft compares Object.is(-0, 0) -&gt; false and drops the draft, so the input flips to "0" mid-typing; the remaining ".5" produces 0.5 instead of -0.5 — wrong sign committed to the scenario range with only a momentary visual blip. The test at ScenarioExpanderEditor.test.tsx:166 fires one change event with the full string "-0.5" so incremental typing is uncovered.

### C2.2 `frontend/src/hooks/useWebSocketSync.ts:153` — The new resync-on-open always triggers an unconditional graph_update reply (server.py:240-249) which, when the graph store is dirty, fires the false 'Pipeline changed on disk' banner/toast even though nothing changed on disk.

**Confidence (finder):** medium

**Failure scenario:** User edits the pipeline (dirty=true); the backend restarts or the laptop sleeps; the WS reconnects and onopen sends {type:'resync'}; the server replies with the unchanged on-disk graph; blockDirtyGraphUpdate (lines 124-133) sets a persistent sync banner + warning toast claiming the pipeline changed on disk while you have unsaved changes — a false alarm with no disk change. When NOT dirty, the same reply force-reapplies an identical graph on every initial connect/reconnect, firing the misleading 'Pipeline updated from file' info toast (line 264) and a forced fitView (line 268) that jumps the user's viewport.

### C2.3 `frontend/src/api/client.ts:208` — hauteSessionToken reads a token baked into the loaded page once, but the backend boot token rotates per process (src/haute/_local_security.py: _BOOT_SESSION_TOKEN = secrets.token_urlsafe(32)), and the client has no 403 detection or reload-recovery path on any fetch or WS route.

**Confidence (finder):** medium

**Failure scenario:** User restarts `haute serve` while a tab is open: window.__HAUTE_SESSION_TOKEN__ is stale, so every /api/* request gets 403 {detail:'Missing or invalid Haute session token'} from LocalSessionMiddleware and the /ws/sync socket is rejected, putting useWebSocketSync into a 50-retry backoff loop; the app degrades into scattered 403 error toasts (saves, previews, cache status all fail) with no 'session expired — reload' handling anywhere in attemptFetch or the WS hook, requiring the user to guess that a page reload fixes it.

### C2.4 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:126` — commitRangeNumber returns without clearing an invalid draft on blur, so a cleared/garbage Min or Max field keeps displaying text that diverges indefinitely from the committed config value that will be saved.

**Confidence (finder):** low

**Failure scenario:** User selects-all and deletes Min intending to unset it, then tabs away: parseScenarioNumber('') is null so commitRangeNumber returns early, the field stays empty (red border only), the min&gt;=max warning is suppressed (parsedMin null at line 95), and onUpdate was never called — saving the pipeline writes the old min_value the editor no longer shows, silently expanding scenarios over a range the user believes they removed.

## D1 — Python pitfalls

*Scope: asyncio blocking, encoding, finally/raise, falsy-zero, dtype traps.*

### D1.1 `src/haute/routes/json_cache.py:408` — Blocking full-file sha256 inside async route handlers: the new data-file signature check in is_per_port_cache_valid runs _hash_file on the event loop via _v2_status_response.

**Confidence (finder):** high

**Failure scenario:** The UI polls GET/POST /api/json-cache/status for a multi-GB JSON/JSONL file whose mtime_ns changed without a size change (touch, copy, rsync, docker COPY) -&gt; _data_file_matches falls through to _hash_file, sha256-hashing the entire file synchronously inside the async def route (build route uses run_blocking_with_response_timeout; status routes do not) -&gt; the uvicorn event loop stalls for seconds-to-minutes, freezing every concurrent request and websocket on each poll.

### D1.2 `src/haute/_trace_correlation.py:350` — Relaxed row-match rewrite enumerates itertools.combinations of shared columns at every width (2^n subsets, each eagerly materialised into a list and run as a polars filter), replacing the old linear greedy column-removal.

**Confidence (finder):** high

**Failure scenario:** User traces a row whose values do not survive upstream (e.g. through an aggregation/scaling node) where parent and child share ~25-40 passthrough columns (normal for insurance frames): no subset matches at any width, so the loop materialises [list(cols) for cols in combinations(n, w)] for every w — sum is 2^n-2 subsets (n=30 -&gt; ~1e9 lists + polars filters) — the trace request pins the CPU for hours or exhausts memory building the combination list, where the old code finished in O(n^2).

### D1.3 `src/haute/_local_security.py:89` — hmac.compare_digest is called on header/query-derived str values; for str inputs it raises TypeError when either side contains non-ASCII characters instead of returning False.

**Confidence (finder):** medium

**Failure scenario:** Any client sends a latin-1 byte &gt;= 0x80 in the x-haute-session-token header (Starlette decodes headers as latin-1, so 'caf\xe9' becomes a non-ASCII str) or in the haute_session_token websocket query param -&gt; compare_digest raises TypeError inside LocalSessionMiddleware.dispatch / websocket_rejection_reason -&gt; unhandled 500 / websocket crash instead of the intended clean 403 rejection.

### D1.4 `src/haute/server.py:230` — New ws resync handler performs blocking work inside an async def: parse_pipeline_to_graph (disk reads, AST parse, sidecar/config JSON loads) and _discovered_pipeline_paths (filesystem walk) run directly on the event loop.

**Confidence (finder):** medium

**Failure scenario:** A client sends a {"type": "resync"} frame for a large pipeline (hundreds of nodes / config sidecars) or sends resync frames repeatedly -&gt; each message synchronously walks the project directory and re-parses the pipeline on the event loop -&gt; all concurrent HTTP requests and websocket broadcasts stall for the duration; the same operation elsewhere (submodel routes) is deliberately wrapped in run_in_threadpool.

### D1.5 `src/haute/_dataframe_execution_cache.py:453` — Raise inside finally: the deferred-eviction settle (_evict_if_over_capacity) in materialization_lock's finally block can raise (documented Windows PermissionError from artifact unlink) and replaces any in-flight exception from the with-body.

**Confidence (finder):** low

**Failure scenario:** A store inside `with cache.materialization_lock(key):` raises a typed cache error the caller handles (e.g. CacheArtifactCorruptError triggering rebuild) while another process holds an artifact open on Windows -&gt; the settle's unlink raises PermissionError in the finally, superseding the original exception type -&gt; the caller's except clause no longer matches, the rebuild/fallback path is skipped and the request fails with an unrelated PermissionError.

### D1.6 `src/haute/_databricks_io.py:367` — Retry integrity check compares cursor.rownumber (a DBAPI-optional attribute typed `object` that may legally be None) against an int with !=, so a connector returning None fails the check even when no rows were lost.

**Confidence (finder):** low

**Failure scenario:** Any transient network blip causes one fetchmany_arrow retry on a databricks-sql-connector version where Cursor.rownumber is None or absent (PEP 249 permits None when the index cannot be determined) -&gt; None != rows_received is always True -&gt; every subsequent batch boundary raises FetchIntegrityError ('lost rows during a retry') and the fetch can never complete despite intact data, leaving the table uncacheable until the dependency is changed.

## D2 — TS/React pitfalls

*Scope: falsy-zero, -0, stale closures, async races, sentinel decode.*

### D2.1 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:30` — Object.is(-0, 0) is false while String(-0) is "0", so the negative-zero round-trip drops the draft mid-typing and eats the user's minus sign.

**Confidence (finder):** high

**Failure scenario:** User types "-0.5" into Min: at "-0" parseScenarioNumber returns -0 and onUpdate commits it; the config echoes back committedMinText = String(-0) = "0", activeRangeDraft compares draft -0 to committed +0 with Object.is -&gt; false -&gt; draft discarded and the controlled input snaps to "0"; the remaining ".5" keystrokes produce "0.5", silently committing min_value = +0.5 instead of -0.5 (sign flip on a pricing range bound).

### D2.2 `frontend/src/hooks/useWebSocketSync.ts:170` — graphUpdateSeq is incremented before the new foreign-source_file early return, so an ignored foreign-file graph_update cancels an in-flight current-file update.

**Confidence (finder):** high

**Failure scenario:** A graph_update for the current pipeline arrives without node positions and awaits getLayoutedElements; while the layout is pending, a graph_update for a different watched source file arrives, bumps graphUpdateSeq, and returns at the isCurrentSourceFile check; when the layout resolves, updateSeq !== graphUpdateSeq silently drops the legitimate update — the canvas stays stale with no banner, toast, or retry even though the file on disk changed.

### D2.3 `frontend/src/components/CacheFetchButton.tsx:101` — The initial-status useEffect has no cancellation/stale-response guard, and the newly added setStatusError writes let a late rejection from the previous resourceKey paint the error UI for the current key.

**Confidence (finder):** medium

**Failure scenario:** User switches the Databricks table (resourceKey A -&gt; B): B's effect clears statusError and B's getStatus resolves cached=true, then A's slow getStatus rejects late -&gt; the stale catch runs setCache(null) and setStatusError("Unable to check cache status: ...") -&gt; the button for B flips to red "Cache status unavailable" with a phantom role=alert error even though B's status loaded fine.

### D2.4 `frontend/src/hooks/useWebSocketSync.ts:44` — normalizeSourceFile lowercases paths, so source-file matching treats case-distinct files as identical on case-sensitive filesystems.

**Confidence (finder):** low

**Failure scenario:** On a Linux host with two watched pipelines differing only by case (e.g. Rating/Main.py open in the editor and rating/main.py modified on disk), isCurrentSourceFile reports a match -&gt; the other file's graph_update replaces the user's canvas and markSaved() clears dirty, silently showing the wrong pipeline's graph as the saved state.

### D2.5 `frontend/src/panels/modelling/LossTab.tsx:100` — The new Math.max(bestIteration, 0) clamp converts the common best_iteration = -1 'no early stopping' sentinel into a plausible-looking best-iteration marker at iteration 0.

**Confidence (finder):** low

**Failure scenario:** A booster trained without early stopping reports best_iteration = -1; isFiniteNumber(-1) passes and the clamp maps it to index 0 -&gt; the chart draws a dashed 'best iteration' line at the first iteration and the legend reads "Best iteration (-1)", presenting a nonexistent best iteration as real model diagnostics instead of omitting the marker.

## E — Wrapper/proxy/cache correctness

*Scope: delegation, key completeness, invalidation, eviction, locks.*

### E.1 `src/haute/deploy/_scorer.py:810` — Deploy batch scoring derives `output_lf.select(output_fields)` from the dataframe-cache scan and then drops the only reference to the pinned scan LazyFrame, releasing the cache pin before the plan is collected — violating DataFrameExecutionCache's documented keep-the-source-scan-alive contract.

**Confidence (finder):** medium

**Failure scenario:** DEPLOY_BATCH request with output_fields set: execute_lazy_graph stores the pl.scan_parquet returned by materialize_lazy_frame_with_cache (pinned via scan refcount + weakref.finalize) into lazy_outputs; score_graph_lazy builds a derived `.select(...)` plan and returns — lazy_outputs goes out of scope, refcount hits 0, the finalizer runs _release_scan which unpins the entry and runs _evict_if_over_capacity. Before score_graph's streaming_collect runs, concurrent requests filling the 16-entry cache (every distinct payload is a new key) or a cache.invalidate()/clear() from a pipeline save evict the now-unpinned entry, _remove_key unlinks the parquet, and the in-flight collect fails with a polars FileNotFound instead of returning scores.

### E.2 `frontend/src/components/CacheFetchButton.tsx:91` — The initial-status effect has no cancellation/staleness guard, so getStatus responses for a previous resourceKey can resolve after the current key's response and overwrite cache/statusError with the wrong resource's data.

**Confidence (finder):** high

**Failure scenario:** User switches the apiInput data path (or Databricks table) from A to B quickly: effect run for A starts a slow getStatus(A); effect run for B starts getStatus(B) which resolves first; then A's response lands last -&gt; setCache(A-status) shows A's cached=true, row/size stats and 'Refresh Cache' label for resource B, and fires onCacheReadyRef.current?.(A-status) telling the parent editor that B's cache is ready — the user previews/saves believing the wrong cache state; the inverse interleaving (A fails last) paints B with 'Cache status unavailable' although B's status loaded fine.

### E.3 `src/haute/routes/_job_store.py:109` — PR changed TTL eviction so 'running' jobs are now evictable when their last recorded activity is older than ttl_seconds, which deletes a live job's artifact handles out from under its worker thread and makes the worker's next update_job raise KeyError.

**Confidence (finder):** medium

**Failure scenario:** A training/optimiser job spends longer than ttl_seconds in one compute step (e.g. a long solver run) without calling atomic_update, so _running_activity_at stays stale; any get_job/create_job poll triggers _evict_stale, which now selects the still-running job (old code excluded status=='running'), runs _remove_job_locked -&gt; _cleanup_artifact_handles deleting server-owned artifact files the live job may still be writing/reading, and pops the job; when the worker finishes and calls update_job(job_id, status='completed', ...) it hits self._jobs[job_id] -&gt; KeyError in the background thread, the result is lost, and the client's status poll 404s.

### E.4 `src/haute/_mlflow_io.py:553` — The on-disk artifact cache keys by (run_id, basename(artifact_path)) — `cache_dir / Path(artifact_path).name` — so two distinct artifacts in the same run with the same filename collide, and the second load silently returns the first artifact's bytes; the PR's new _artifact_io_lock is keyed the same way, cementing rather than fixing the identity.

**Confidence (finder):** medium

**Failure scenario:** A run logs two models in subdirectories with equal basenames (e.g. 'freq/model.cbm' and 'sev/model.cbm' — _find_artifact_by_extension explicitly searches one level of subdirectories, so this layout is supported): loading freq downloads to .cache/models/&lt;run&gt;/model.cbm; a later load of sev calls _resolve_artifact_local, finds local_path.is_file() true and returns the frequency model's file; the severity ScoringModel is built from the wrong bytes and cached in _model_cache under sev's distinct in-memory key, so wrong predictions persist for the process lifetime and across restarts via the poisoned disk file.

### E.5 `src/haute/_dataframe_execution_cache.py:432` — materialization_lock acquires the per-key lock while still holding the global _materialize_locks_guard, so one same-key waiter blocks every other key's materialization (and clear()), defeating the documented 'allowing different keys' contract.

**Confidence (finder):** medium

**Failure scenario:** Thread T1 holds key A's materialize lock during a long bounded_sink; T2 requests key A: it enters `with self._materialize_locks_guard`, then blocks on lock.acquire() for A while still holding the guard; T3 requesting unrelated key B now blocks on the guard, as does any clear()/invalidate() — all cache materializations process-wide serialize behind the slowest same-key wait (exactly the thundering-herd case the lock exists for); if a thread ever nests a second materialization_lock for a different key while clear() is waiting on its held lock, the guard&lt;-&gt;key-lock cycle deadlocks.

### E.6 `frontend/src/components/CacheFetchButton.tsx:114` — The progress poll treats `active:false` as 'build finished' and clears `building`, but the JSON cache progress endpoint is a stub that always returns active=false, so the button exits the building state ~1s into every v2 shred build while the startFetch POST is still in flight.

**Confidence (finder):** medium

**Failure scenario:** User clicks 'Cache as Parquet' on a large JSON apiInput: doFetch sets building=true; at the first 1s poll tick getJsonCacheProgress returns {active:false} (routes/json_cache.py /progress is stubbed) -&gt; setBuilding(false); the button reverts to the clickable fetch label plus 'Not cached yet' hint mid-build; the user clicks again, firing a second concurrent build POST (serialized server-side but doubling work), and whichever of the two responses resolves last — possibly the older build against an older volatile_schema — wins setCache, leaving stale status/fingerprint displayed.

### E.7 `src/haute/_json_shred.py:1014` — Multi-port load filters with `if label in bundle`, silently omitting an emitting port whose parquet vanished between the is_per_port_cache_valid check and load_per_port_cache's scan, instead of raising the 'Cache as Parquet' error the single-port path gives.

**Confidence (finder):** low

**Failure scenario:** Preview thread validates the working/ cache then a concurrent rebuild (same process, different request) runs _swap_dir_into_place, leaving the live dir briefly absent or repopulated for a schema without one table's parquet; load_per_port_cache skips the missing file and the dict comprehension silently returns fewer ports (or {}), so the executor's edge resolution fails later with a confusing missing-port KeyError — or in the worst case downstream nodes consume only the surviving branch — instead of the clear stale-cache RuntimeError raised one line earlier for the single-port case.

### E.8 `src/haute/_mlflow_io.py:614` — _evict_disk_cache runs under only the current artifact's lock but rmtree's the OLDEST run directories, deleting cached files another thread (different run = different lock) is concurrently resolving or loading.

**Confidence (finder):** low

**Failure scenario:** Thread A holds the lock for (runX, model.cbm), _resolve_artifact_local returned the cached path, and A is about to open it in _load_catboost_model; thread B finishes downloading runY's artifact and its in-lock _evict_disk_cache selects runX's directory as oldest and rmtree's it; A's load hits FileNotFoundError, the corrupt-retry path re-downloads (~30s latency spike), and if eviction pressure recurs on the retry the load surfaces as a spurious 'Persistently corrupt or unloadable model artifact' RuntimeError for a perfectly healthy artifact.

## Cleanup — Reuse

*Scope: new code re-implementing existing helpers.*

### REUSE.1 `src/haute/execution.py:329` — _stat_gated_runtime_path_fingerprint hand-rolls a (mtime_ns,size) stat-gated memo with double-stat torn-read retry that re-implements the PR's own shared haute._stat_gated_cache.StatGatedCache (adopted by modelling/_feature_contract.load_contract_cached and deploy/_scorer's model/contract caches), instead of instantiating StatGatedCache with the non-file bypass kept as a thin wrapper.

**Confidence (finder):** high

**Failure scenario:** Two parallel implementations of the same invalidation discipline must now evolve together: StatGatedCache single-flights concurrent loads under a per-key lock while execution.py's copy does not (a thundering herd of concurrent previews content-hashes the same large input file N times), and any future gate fix (extra stat fields, retry-count change, clearing for test isolation) lands in the class but silently not in this private copy — _stat_gated_cache.py's docstring already has to cross-reference this function as a sibling rather than a caller.

### REUSE.2 `src/haute/_json_shred.py:901` — _swap_dir_into_place (plus _rename_dir_with_retry at line 298) re-implements the tmp-dir/backup-dir/restore-on-failure directory swap that already exists inline in haute._json_flatten.mirror_cache_to_committed (_json_flatten.py:292-310) — its own docstring admits 'Same rename dance as :func:`haute._json_flatten.mirror_cache_to_committed`' instead of extracting one shared swap helper (e.g. next to atomic_write_bytes in haute._file_ops) used by both.

**Confidence (finder):** high

**Failure scenario:** The two copies have already diverged: the new shred swap retries transient Windows PermissionError handle locks with backoff and uses uuid-unique staging names, while mirror_cache_to_committed's identical dance has neither — so the Save-time committed-layer swap still fails on exactly the Windows sharing-violation race W2 fixed for builds, and every future hardening (retry delays, legacy-dir cleanup) must be hand-mirrored across two files or they drift further.

### REUSE.3 `src/haute/routes/_save_pipeline.py:335` — _resolve_module_delete_file duplicates ~25 lines of the sibling SavePipelineService._validate_output_rel_path (same file, lines 273-333) — backslash normalisation, empty/absolute/~ rejection, '..' traversal scan, _MODULES_PREFIX single-segment allowlist, resolve + is_relative_to(self._root) containment — instead of sharing that validator (or routes/_helpers.validate_safe_path for the containment step) with a '.py'-suffix/no-main-file parameter.

**Confidence (finder):** high

**Failure scenario:** A future hardening of the write-path validator (e.g. rejecting a symlinked modules/ directory, Windows case normalisation, or new logging) will not reach the delete validator, letting a submodel delete accept a path the write path rejects (or vice versa) in the same save transaction; the copy also already dropped the logger.warning('save_reject_output_path'...) telemetry the original emits, so rejected deletes are invisible in logs while rejected writes are not.

### REUSE.4 `src/haute/_trace_correlation.py:63` — _float_non_finite_token re-implements the nan/'inf'/'-inf' token derivation that lives inside haute._json_safe.non_finite_float_sentinel (_json_safe.py:16), both added in this PR; _json_safe should expose the float-to-token mapping as the single helper (it already exports non_finite_float_token for the dict form, which this module imports three lines away).

**Confidence (finder):** medium

**Failure scenario:** The token vocabulary is a wire contract shared with the frontend's isHauteNonFiniteFloat guard; if _json_safe ever changes or extends the mapping (new token, normalisation tweak), _trace_correlation's private copy keeps comparing old tokens and _trace_values_match silently stops equating raw floats with the sentinel objects the JSON boundary emits — trace steps flip to 'unmatched' with no error.

### REUSE.5 `src/haute/execution.py:507` — dataframe_graph_input_fingerprint still digests its cache-key payload with raw json.dumps(payload, sort_keys=True, separators=(",", ":")) instead of haute._cache.canonical_json — the PR's W2.13 unification whose docstring claims it is 'THE canonical-JSON encoding for digest material — the only one' covering 'dataframe-execution payloads', and this PR modified the very payload builder (_runtime_input_fingerprint_entry) and imported canonical_json into this module for the neighbouring _runtime_file_inputs_signature.

**Confidence (finder):** medium

**Failure scenario:** The single-encoder invariant the PR establishes is broken by a straggler in the same module: a future canonical-rule change (float text, escaping, Mapping/set support) rolls every other cache key via ALGO_VERSION v5 but leaves this input fingerprint encoded under different rules, and a node config subset containing a set or MappingProxyType raises TypeError here while canonical_json handles it — two digest layers in one cache key that disagree on encoding and on what is fingerprintable.

### REUSE.6 `frontend/src/panels/modelling/GLMCoefficientsTab.tsx:57` — GLMCoefficientsTab (and GLMRelativitiesTab.tsx:38, FeaturesTab.tsx:40 in the same panels/modelling directory) keep the hand-rolled empty-state div ('flex items-center justify-center h-full text-xs' + var(--text-muted)) that is byte-equivalent to the new shared ChartEmptyState in frontend/src/panels/modelling/ChartScaffold.tsx, which Pdp/Loss/Lift/Ave/Residuals/LossChart all adopted in this PR.

**Confidence (finder):** medium

**Failure scenario:** The scaffold unification is silently incomplete: the same empty-state markup now lives in ChartEmptyState plus three sibling modelling tabs, so any styling/token change to the scaffold (colour token, spacing, a11y role) leaves the GLM coefficients/relativities and Features tabs visually and behaviourally divergent from the other modelling tabs, and the duplication the scaffold was created to remove persists in the directory it owns.

## Cleanup — Simplification

*Scope: redundant state, copy-paste, dead code.*

### SIMPLIFICATION.1 `src/haute/execution.py:329` — _stat_gated_runtime_path_fingerprint hand-rolls a module-level lock + memo dict + double-stat torn-read retry that duplicates the StatGatedCache class this same PR adds (whose docstring even cites this function as its pattern).

**Confidence (finder):** high

**Failure scenario:** Two copies of the subtle stat-gate protocol (gate check, load, re-stat, retry-once, RuntimeError) must now be fixed in lockstep — a future race or negative-caching fix applied to StatGatedCache silently misses the preview/trace fingerprint memo, and vice versa; the bespoke copy also lacks StatGatedCache's per-key single-flight, so concurrent previews re-hash the same large input. Simpler equivalent: keep the 2-line non-file early return and replace lines 322-364 with a module-level StatGatedCache[str, Mapping[str, object]] and `_memo.get_or_load(memo_key, memo_key, lambda: _runtime_path_fingerprint(resolved))`.

### SIMPLIFICATION.2 `src/haute/server.py:304` — TRUSTED_HOSTS_ENV = "HAUTE_TRUSTED_HOSTS" is defined independently in both server.py (reader) and cli/_serve.py:48 (writer) — mirrored constants that must be kept in sync by hand.

**Confidence (finder):** high

**Failure scenario:** Renaming the env var (or fixing its format) in one module silently breaks the other: _configure_trusted_hosts would set a key the server never reads, and TrustedHostMiddleware would quietly fall back to the localhost default, ignoring the operator's explicit --host trust configuration with no error. Simpler equivalent: define the constant once in haute._local_security (both files already import from it) and import it in both places.

### SIMPLIFICATION.3 `src/haute/_git.py:39` — PROTECTED_BRANCHES = DEFAULT_PROTECTED_BRANCHES is a dead alias — every runtime check now goes through _protected_branches()/the env var, and grep shows no reader of PROTECTED_BRANCHES anywhere in src or tests.

**Confidence (finder):** high

**Failure scenario:** The alias preserves the name that used to BE the live protection set, so a test or operator monkeypatching haute._git.PROTECTED_BRANCHES (the obvious historical knob) now silently has no effect — protection appears configured but _is_protected ignores it. Simpler equivalent: delete the alias and keep only DEFAULT_PROTECTED_BRANCHES + _protected_branches().

### SIMPLIFICATION.4 `src/haute/routes/optimiser.py:186` — _estimate_quote_id_column_or_raise hand-copies the column-presence and quote-id-dtype checks (with byte-identical message strings) that already exist in _validate_and_project and a third time in _validate_and_project_auto_range.

**Confidence (finder):** high

**Failure scenario:** Three verbatim copies of one validation rule: relaxing the quote_id dtype set or rewording the 'Missing columns in scored data' message in the solve path silently leaves the estimate path enforcing the old rule/message (the docstring itself admits it must 'mirror the exact messages'). Simpler equivalent: extract one schema-only helper (raise ValueError, return qid_col) used by all three sites, with the service wrapping it in _record_http_setup_failure and the route in HTTPException.

### SIMPLIFICATION.5 `src/haute/schemas.py:1289` — GitSaveResponse.push_error / GitSubmitResponse.push_error are constitutionally-null fields: _git.py only ever passes push_error=None because push failures now raise via _push_or_raise instead of being reported softly.

**Confidence (finder):** medium

**Failure scenario:** The contract advertises a soft-failure channel that cannot carry a failure — a maintainer or frontend dev writing `if (res.push_error) showWarning(...)` builds dead code while real push failures arrive as raised GitDomainError/HTTP errors; meanwhile guards.ts, fixtures, and tests all maintain plumbing for a permanently-null value (GitPanel.tsx never reads it). Simpler equivalent: drop push_error from both response models (keep `pushed`, which carries the real no-remote signal) or actually populate it where a soft failure is intended.

### SIMPLIFICATION.6 `src/haute/_model_scorer.py:895` — _run_score_pipeline's live path hand-rolls a collect-then-validate-then-relazify block to enforce categorical domains on the exact scored rows, duplicating the mechanism this PR built into _score_eager_unified via its new categorical_levels parameter (used by score_frame).

**Confidence (finder):** medium

**Failure scenario:** Two mechanisms now uphold the 'validate the exact materialised rows' invariant — a future fix to one (projection-aware validation, profile choice, error wording) silently misses the other, and the live path performs an extra full streaming_collect plus a second (DataFrame-backed) collect inside the eager scorer that a reader must prove harmless. Simpler equivalent: thread categorical_levels through _mlflow_io._score_eager into score_frame (which already forwards it to _score_eager_unified) and delete the 14-line pre-collect block.

### SIMPLIFICATION.7 `src/haute/executor.py:1453` — execute_sink normalises the sink path with _resolve_sink_path and then immediately calls resolve_sink_output_path, which applies _resolve_sink_path to the already-normalised value a second time.

**Confidence (finder):** medium

**Failure scenario:** Correctness rests on _resolve_sink_path being accidentally idempotent: any future normalisation that isn't (timestamped names, different prefix rule) double-applies here, making the path execute_sink writes diverge from the single-application path the route pre-validated via _validate_sink_output_path — an allowlist bypass/mismatch with no error. Simpler equivalent: let resolve_sink_output_path be the single owner of normalisation (take the raw path and also return/expose the normalised display string), removing the duplicate call at line 1453.

### SIMPLIFICATION.8 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:69` — The min/max draft-input machinery is fully mirrored per field — two useState draft states, two reconciliation useEffects, two setters, and parallel shown/parsed/invalid derivations differing only in the min/max name.

**Confidence (finder):** medium

**Failure scenario:** Every fix to the draft lifecycle (the Object.is numeric-equality nuance in activeRangeDraft, the blur-commit rules in commitRangeNumber where two of three branches already do the same clearDraft(null)) must be hand-applied to both wirings, and a third numeric field copies it a third time — drift between the copies shows as one input snapping back mid-edit while the other doesn't. Simpler equivalent: one useNumberDraft(committedText) hook returning {shownText, parsed, invalid, onChange, onBlur}, instantiated twice.

## Cleanup — Efficiency

*Scope: wasted work on hot paths.*

### EFFICIENCY.1 `src/haute/_trace_correlation.py:349` — The relaxed row-match in _find_matching_row enumerates combinations(original_shared, width) for every width from n-1 down to 1, running a full polars filter over the parent frame for each subset — sum C(n,w) filters, which is combinatorial/exponential in the shared-column count, replacing the old O(n^2) greedy single-column-removal loop.

**Confidence (finder):** high

**Failure scenario:** Trace correlation runs this once per node in the trace. With 25-40 shared columns (typical insurance frames) and a step that modifies m columns (rating steps recompute several), the first match sits at width n-m, costing ~C(n,m) filters: n=40, m=6 is ~3.8M polars filters (minutes per step); a row with no subset match (fully transformed) enumerates all 2^n-2 subsets — a guaranteed hang at n&gt;=30, blocking the trace request. Cheaper: precompute one per-column boolean match mask over the parent frame (n vectorized filters total) and combine masks with numpy AND while searching greedily/bounded (drop at most k columns), or keep the previous greedy removal — both stay polynomial with identical ambiguity detection.

### EFFICIENCY.2 `src/haute/_json_shred.py:267` — _data_file_matches re-computes a sha256 of the entire raw JSON/JSONL data file on every validity check whenever the file's mtime differs from the recorded one but content matches (the PR's own documented copy/rsync/touch scenario), with no memoization and no healing of the recorded mtime.

**Confidence (finder):** high

**Failure scenario:** is_per_port_cache_valid runs inside load_v2_api_source on every graph execution that reaches an apiInput (each uncached preview/trace) and in the /api/json-cache status route the editor polls; after a single touch/copy of a multi-GB JSONL the mtime never matches again, so every one of those calls re-reads and re-hashes the whole file (~3-5s/GB) forever. Cheaper: memoize the hash by (path, mtime_ns, size) — the PR's own StatGatedCache pattern — or rewrite meta.json's recorded mtime_ns once after a successful hash arbitration, making subsequent checks a pure stat.

### EFFICIENCY.3 `src/haute/_builders.py:1660` — _apply_ratebook calls result_lf.collect_schema().names() inside the per-factor-table loop, resolving the schema of a lazy plan that grows by one join + miss-guard + with_columns per iteration — O(T^2) plan-schema resolution on top of the collect_schema _apply_rating_table already performs per table.

**Confidence (finder):** medium

**Failure scenario:** The ratebook apply runs every time an optimiserApply node executes (every preview/trace and every deployed scoring request through the node). With 30-50 factor tables (one per rating factor plus composites), the final iterations resolve a plan with dozens of joins each, adding hundreds of ms to ~1s per execution that pure plan-building should not cost. Cheaper: hoist available = result_lf.collect_schema().names() once before the loop into a set and add each table's outputColumn as it is appended — join columns can only come from the original frame, so the check stays exact.

### EFFICIENCY.4 `src/haute/_parser_regex.py:517` — The rewritten regex-fallback parser rescans the source from index 0 for every anchor — _position_is_code(source, m.start()) per decorator (line 517), and per connect anchor both source.count('\n',0,idx) and _parenthesized_wrapper_depth_before(source, idx) (lines 337-338, on top of the same O(idx) scan already done in _is_top_level_statement_anchor) — making fallback parsing O(anchors x file_size) in pure-Python char loops, where the old single regex pass was linear.

**Confidence (finder):** medium

**Failure scenario:** A pipeline file with thousands of nodes (1-2MB, thousands of decorator/connect anchors) that currently has a syntax error is re-parsed by the file watcher on every save: ~10^9-10^10 Python-level character steps, i.e. tens of seconds to minutes per reparse, executed directly on the server event loop (server.py _file_watcher), freezing all WS sync. Cheaper: one forward scan precomputing a code/string/comment classification mask and cumulative newline counts (the machinery _iter_connect_anchor_matches' forward walk already has), giving O(1) per-anchor lookups.

### EFFICIENCY.5 `src/haute/_json_shred.py:388` — _iter_sampled_json_array_records reads the file one byte at a time via nested Python closures (_read_byte -&gt; f.read(1), called again per byte from _read_root_array_value), costing roughly two Python function calls per input byte (~1-2MB/s effective throughput).

**Confidence (finder):** low

**Failure scenario:** Schema inference with sample_size over a root JSON array whose sampled records total tens of MB (e.g. 1,000 nested quote records of 10-50KB) spends 10-60s in the byte loop, occupying a threadpool worker, where orjson parses the same bytes in well under a second; the sampling win over whole-file parsing evaporates unless the file is orders of magnitude larger than the sample. Cheaper: read 64KB chunks into a buffer and run the same string/depth state machine over indexed bytes (or bytes.translate/regex for structural chars), ~50-100x faster with identical semantics. Mitigating: the GUI's Infer Tables button never sends sample_size today, so only direct API callers hit this path.

## Cleanup — Altitude

*Scope: band-aids where the underlying mechanism should be extended.*

### ALTITUDE.1 `src/haute/_local_security.py:81` — A test-only auth bypass (`Host: testserver` + no Origin skips both the Origin check and the session token) is carved directly into the production LocalSessionMiddleware and websocket gate, with 'testserver' also baked into the production TrustedHost allowlists (server.py:305, cli/_serve.py:303), instead of test fixtures presenting the token.

**Confidence (finder):** high

**Failure scenario:** The PR already ships the scoped mechanisms tests should use — HAUTE_LOCAL_SESSION_TOKEN (settable per-fixture) and the documented HAUTE_DISABLE_LOCAL_SESSION_AUTH escape hatch — so the deeper fix is a shared test-client factory that injects the token header, keeping the middleware unconditional. As shipped, every backend route test silently exercises the bypass rather than the auth path (a regression in SPA token wiring passes the entire suite), the bypass is live in every production install for any client that can set Host: testserver (reverse proxies, non-browser tools), and each future security review must re-derive why a magic host string defeats W8b's protection.

### ALTITUDE.2 `src/haute/execution.py:329` — `_stat_gated_runtime_path_fingerprint` hand-rolls a module-level stat-gated memo (dict + lock + double-stat torn-read retry + RuntimeError) when the same PR introduced the generic `StatGatedCache` in _stat_gated_cache.py, whose own docstring cites this function as sharing its discipline.

**Confidence (finder):** high

**Failure scenario:** The deeper mechanism exists in-repo: this memo is exactly StatGatedCache[str, Mapping] (get_or_load keyed by resolved path). Keeping a second implementation means the subtle (mtime_ns,size) gate protocol is maintained twice and can drift (e.g. a coarse-mtime or zero-size-file fix lands in one copy), and this copy lacks the class's per-key single-flight lock, so N concurrent preview/trace requests after one edit each content-hash the same multi-GB input file — the exact thundering-herd cost StatGatedCache was built to remove.

### ALTITUDE.3 `src/haute/_json_shred.py:901` — `_swap_dir_into_place` + `_rename_dir_with_retry` duplicate the atomic cache-dir swap dance that `_json_flatten.mirror_cache_to_committed` (lines 292-310) also implements — the docstring admits 'Same rename dance as mirror_cache_to_committed' — and the Windows transient-PermissionError retry from post-closeout fix 4 was applied only to this copy, with neither living in _file_ops, the module that owns atomic-replace platform semantics.

**Confidence (finder):** high

**Failure scenario:** The deeper fix is one `atomic_replace_dir` helper in haute/_file_ops.py (which already documents the Windows MoveFileEx/PermissionError quirk for files) used by both the shred build and the committed mirror. Without it, the mirror path still uses fixed `.tmp`/`.old` names and bare renames, so the same AV/indexer-induced rename flake that broke CI in the shred path will next strike `mirror_cache_to_committed` at save time and be patched a third time, and the two sites' policies (bespoke retry delays vs no retry) silently diverge.

### ALTITUDE.4 `frontend/src/panels/editors/ScenarioExpanderEditor.tsx:8` — W7.5 hand-rolls ~80 lines of draft/commit-on-blur state machinery (ScenarioRangeDraftState, activeRangeDraft, reconcileRangeDraft, commitRangeNumber) inside one editor, re-implementing the exact contract `CommittedTextInput` (ApiInputEditor.tsx:764, built in W1) already encodes — draft until blur, external commit wins over stale draft, invalid draft stays visible — instead of extracting that primitive into the shared editors/_shared.tsx module.

**Confidence (finder):** high

**Failure scenario:** The deeper mechanism is a shared CommittedTextInput/CommittedNumberInput in _shared.tsx (the established home for editor primitives like INPUT_STYLE/InputSourcesBar). The keystroke-corruption bug class hit at least four editors in this review cycle (apiInput paths, column names, scenario min/max, TwoWayGrid paste); each got a bespoke state machine, so the next numeric config field will be patched a fifth time, and refinements to the reconcile rule (e.g. the external-change-wins logic both copies independently implement) will not propagate between copies.

### ALTITUDE.5 `src/haute/execution.py:510` — `_runtime_file_signature_paths` fixes the C4 cache-key gap with per-node-type carve-outs (`if node_type == NodeType.API_INPUT`, `if node_type == NodeType.DATA_SOURCE and sourceType == 'databricks'` reaching into the private `_databricks_io._cache_path_for`) inside the generic signature enumerator, hand-mirroring each builder's runtime dispatch rather than having node types declare their consumed files.

**Confidence (finder):** medium

**Failure scenario:** The deeper mechanism is the declarative route that already exists for the simple cases (`_runtime_input_path_fields`'s per-node-type field table) extended to express derived paths, or builder-side registration of consumed files. With the dispatch mirrored positionally, the next node type or dataSource sourceType with a file-backed input compiles and runs but silently misses preview/trace invalidation — recreating the exact stale-wrong-preview class W2.2 fixed; the W2 notes already concede the parallel sink-side databricks gap was deferred to a future 'A2 consolidation', confirming the mechanism wasn't generalized.

### ALTITUDE.6 `src/haute/trace.py:928` — `trace_result_to_dict` applies the shared `to_json_safe` codec to nine individually chosen fields instead of encoding the whole payload once at the serialization boundary.

**Confidence (finder):** medium

**Failure scenario:** The deeper form is a single `to_json_safe(payload)` over the returned dict (the codec is already recursive and a no-op on safe values). With per-field wrapping, any future TraceResult/TraceStep field carrying floats or large ints — or a numeric value added inside the unwrapped schema_diff/row_lineage shapes — silently bypasses the NaN/inf-sentinel and big-int-string encoding, reintroducing the exact W7.1/7.2 silent-wrongness class (invalid JSON or browser-rounded IDs) that this PR's remediation just closed, with no test failing because each existing field still passes.

### ALTITUDE.7 `src/haute/_mlflow_io.py:228` — `_artifact_io_lock` is the third hand-rolled per-key single-flight lock registry added by this PR (alongside _json_shred._BUILD_LOCKS and StatGatedCache._load_locks), its comment explicitly saying it 'mirrors the per-key materialization lock in _dataframe_execution_cache' — the W2.10 pattern was copied per site rather than extracted into one keyed-lock utility.

**Confidence (finder):** medium

**Failure scenario:** The deeper mechanism is a single shared KeyedLocks helper owning the subtle invariants (WeakValueDictionary entry kept alive by acquiring under the guard, reentrancy choice, key normalization). Four parallel registries mean each future fix to those subtleties — e.g. the strong-reference-during-acquire race or a deadlock-ordering rule — must be found and re-applied in every copy, and the next subsystem needing serialized loads (the pattern recurred four times in this one PR) will hand-roll a fifth.

### ALTITUDE.8 `frontend/src/hooks/useWebSocketSync.ts:60` — `isCurrentSourceFile` patches the foreign-update filtering with client-side path heuristics (lowercasing, backslash normalization, absolute-vs-relative suffix matching, and treat-missing-as-match) instead of the backend's now-canonical `_wire_source_file` id being stored and compared exactly.

**Confidence (finder):** low

**Failure scenario:** The deeper mechanism is a single canonical source-file identity: server.py already normalizes every frame to a project-relative POSIX id (`_wire_source_file`), so the client should persist that same id at load time and compare strictly. Keeping fuzzy matching means the contract lives in heuristics: case-folding can conflate distinct files on case-sensitive filesystems, `!incomingSource || !currentSource → true` reopens the apply-foreign-graph hole whenever either side omits the field, and every new frame producer must be re-checked against four normalization rules instead of one id contract.
