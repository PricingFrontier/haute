# Report: backend-core

## Verdict (≤10 lines)
The trace core is architecturally sound and the W4 commit (b19ff1f4) genuinely hardened the *post-hoc correlation matcher* (`_find_matching_row`) to fail-loud on ambiguity. But that same commit left the **relocation entry point untouched**, creating a live, mainstream, regulator-facing silent-wrongness bug: when the preview cache is evicted (the documented, expected case), `_find_target_row_index` re-anchors the *entire* trace to the first of several visually-identical rows with **no diagnostic** — reproduced end-to-end (CORE-01, CRITICAL). Every P03 finding (FR-03…FR-11) is **still open**; three confirmed empirically (FR-05 float split, FR-06, FR-07). The two correlation paths still disagree on float equality (a step silently vanishes from the waterfall). Column-relevance pruning can silently drop genuine contributors, breaking the README's "every contributing node" promise. Concurrency and the byte-budget interplay are safe (NOT the double-memory bug suspected — shared refs). Route error mapping and supersession/timeout machinery are solid. Net: one CRITICAL, two HIGH, several MEDIUM; the foundation is one fix (CORE-01) away from matching its own fail-loud standard.

## Findings

### CORE-01 [CRITICAL] [correctness] — Row relocation silently anchors the whole trace to the wrong row
- Location: `src/haute/trace.py:262-271` (`_find_target_row_index`), called at `src/haute/trace.py:489`
- Claim: When the clicked row is relocated (preview cache evicted / rows reordered), the first of several rows matching the *visible* columns wins with no ambiguity check, so every per-node value in the trace is correct-for-the-wrong-row.
- Evidence: `_find_target_row_index` returns `idx` on the first `all(_trace_values_match(...))` hit. The deeper matcher one call down refuses the identical ambiguity. Repro (`repro_trace.py` + `repro_e2e.py`):
  ```
  _find_target_row_index -> 0   (silently picks first)
  _find_matching_row     -> (row is None: True, idx=-1), diagnostics=['duplicate_exact_match']
  --- end-to-end (rows id=111,222 identical on region+premium; clicked col=premium) ---
  relocated row_index      : 1
  source step 'id' shown   : 111
  correlation diagnostics  : []     # <-- empty: no signal to the user
  ```
- User impact: An analyst clicks a premium cell to explain it to a regulator. Two policies with identical displayed attributes exist; the trace confidently shows the *other* policy's upstream base, rating factors, and lineage. No warning — it looks authoritative and is wrong.
- Fix sketch: Collect matches with a short-circuit at the 2nd; if `>1`, raise the same `ValueError("Trace data does not match the preview row…")` already raised at `trace.py:493` (route maps it to 409). Never return index 0 on ambiguity. Vectorise with the FR-05 tolerance expr while there. Makes the entry point consistent with `_find_matching_row`'s already-shipped fail-loud contract.
- First failing test: target frame with two rows identical on `row_values` columns but differing on a hidden column; assert `execute_trace(..., row_values=...)` raises (409) instead of returning a trace carrying the first row's hidden value.
- Confidence: high
- Overlap: tracked-as-FR-06 (still-open) — W4 fixed the sibling `_find_matching_row` but not this entry point, so W4 *widened* the inconsistency.

### CORE-02 [HIGH] [correctness] — The two correlation paths disagree on float equality; a step silently drops from the waterfall
- Location: `src/haute/_trace_correlation.py:233` (`_build_value_match_expr` float branch) vs `:101-120` (`_trace_values_match`)
- Claim: The positional fast path accepts verbatim-carried floats within `rel_tol=1e-9`; the vectorised value-match path uses exact `pl.col(column) == value`. Which path runs depends on incidental parent/child row-count equality, not the data.
- Evidence: `_build_value_match_expr` returns `cast(pl.Expr, pl.col(column) == value)` for numerics — no tolerance. Repro:
  ```
  drift = 6.173e-07  rel = 5.000e-10
  _trace_values_match(parent, child)              -> True
  _build_value_match_expr(v == 1234.5678) on parent -> False
  ```
- User impact: For a value carried through a float pipeline (f32→f64 widening, arithmetic re-association), a contributing node intermittently disappears from the trace/waterfall depending on how many rows its parent frame happens to have — a non-deterministic-looking gap.
- Fix sketch: Give the float branch tolerance: `(pl.col(column) - value).abs() <= (_TRACE_ABS_TOL + _TRACE_REL_TOL * abs(value))` with `fill_null(False)` preserved; keep exact equality for non-float dtypes. Precondition for the FR-04 dedup.
- First failing test: parametrised — feed the reproduced pair through both paths (force each via equal/unequal row counts) and assert both locate the row.
- Confidence: high
- Overlap: tracked-as-FR-05 (still-open)

### CORE-03 [HIGH] [correctness] — Column-relevance pruning can silently drop genuine contributors
- Location: `src/haute/trace.py:889-930` (`_prune_to_column_relevance`, the `ref_cols` targeted walk)
- Claim: When origin steps expose `referenced_columns`, the walk keeps only nodes producing those columns (+ ancestors). If the expression parser under-reports references (opaque helper call, preamble function reading a column, unsupported construct), a genuinely-contributing ancestor is pruned with no signal — violating the README promise that the trace shows every contributing node. The safe "keep all ancestors" fallback (`:916-924`) only triggers when `ref_cols` is *entirely* empty, not *partially* populated.
- Evidence: `ref_cols` is built solely from `s.expression.get("referenced_columns", [])` (`:892`); `_expression_parser` populates this from the AST it can parse (`_expression_parser.py:1280`), returning `[]`/raising on constructs it cannot. The `elif any(c in s.output_values for c in ref_cols)` at `:907` is the inverse defect — it *keeps* mere pass-through carriers that contribute nothing, inflating the trace. The regex origin-detector at `:874` (`\b{column}\s*=`) misses `.alias(column)` / `**{column: …}` forms and false-positives on `column == …`.
- User impact: A premium computed via a preamble helper (`premium = base * risk_multiplier(vehicle)`) can have the node producing `vehicle` pruned from the explanation; the regulator sees an incomplete lineage presented as complete.
- Fix sketch: Treat a partial `ref_cols` conservatively — if any origin's expression failed to parse or the body contains parser-opaque calls, fall back to keeping all ancestors. Separate "contributes" from "carries" (don't add carriers via `output_values`). Replace the `\b{column}\s*=` regex with the parsed `columns_added`/`columns_modified` signal (already available).
- First failing test: pipeline where the traced column's expression references a column only through a preamble helper; assert the node producing that column survives pruning.
- Confidence: medium (mechanism proven by code structure; minimal repro needs a parser blind-spot input, not constructed)
- Overlap: new (pruning completeness is not in P03)

### CORE-04 [MEDIUM] [correctness] — Trace renders Datetime/List/Struct values unlike the preview table
- Location: `src/haute/_trace_correlation.py:84-98` (`_jsonify_row`, the `else: clean[k] = str(v)` branch)
- Claim: Non-primitive cells are stringified, diverging from the preview's `to_json_safe`, so the trace shows a different value than the clicked cell — the module docstring guarantees byte-identical values.
- Evidence: Repro:
  ```
  trace   _jsonify_row : {'ts': '2020-01-02 03:04:05', 'tags': '[1, 2, 3]'}
  preview to_json_safe : {'ts': '2020-01-02T03:04:05', 'tags': [1, 2, 3]}
  ```
  `Datetime` differs (space vs ISO `T`); `List(Int64)` is a JSON *string* in the trace vs a JSON *array* in the preview.
- User impact: For temporal/list/struct columns the traced value visibly mismatches the clicked cell, undermining the "exactly your data" contract. `_trace_values_match` also compares jsonified values, so this can perturb correlation for such columns.
- Fix sketch: Route non-primitives through `to_json_safe` (`{k: to_json_safe(v) for k, v in row.items()}`), keeping primitive short-circuits; switch both comparison sides together. Note `test_non_primitives_stringified` (`tests/test_trace.py:120`) pins the OLD `date → "2025-01-01"` behavior and must be updated.
- First failing test: trace a frame with `Datetime` and `List` columns; assert step `output_values` equal the preview serialisation.
- Confidence: high
- Overlap: tracked-as-FR-07 (still-open)

### CORE-05 [MEDIUM] [correctness] — Join-node input snapshot keeps the first parent's colliding column bare; provenance is order-dependent and schema-diff misreports
- Location: `src/haute/trace.py:817-820` (`_assemble_steps` multi-parent merge)
- Claim: On a collision, only the *second+* parent's key is namespaced (`{pid}.{k}`); the first parent's copy stays bare. Which parent "owns" the bare name depends on `parents_of` iteration order, and the schema diff then classifies the namespaced copy as a *removed* column.
- Evidence: Repro (two parents each carrying `shared` with different values into a join child):
  ```
  child input_values = {'shared': 'A_value', 'a_only': 1, 'pB.shared': 'B_value', 'b_only': 2}
  schema_diff.modified = [], removed = ['pB.shared']
  ```
  `pB`'s value is reported as a *removed* input even though the join consumed it; `pA`'s is silently canonical `shared`.
- User impact: For join/merge nodes the trace's input panel misattributes which source a value came from and shows phantom "removed" columns, confusing lineage exactly where users most need it.
- Fix sketch: Namespace *all* colliding keys symmetrically (`{pid}.{k}` for every parent when `k` appears in >1 parent), or attach explicit per-parent provenance. Ensure `_compute_schema_diff` sees the same namespacing so collisions aren't miscounted as removed.
- First failing test: two parents with a shared column of differing values into one child; assert both values appear under stable symmetric keys and neither is `columns_removed`.
- Confidence: high
- Overlap: new

### CORE-06 [MEDIUM] [performance] — `_shared_key_is_unique` full-frame Python `iter_rows` scan on the warm-click path
- Location: `src/haute/_trace_correlation.py:499-529`, called at `:772`
- Claim: For the common unique-key case the uniqueness gate cannot short-circuit and jsonifies every column of every row while comparing only `shared_cols`, adding a full Python scan to each parent on the hot path the docstring promises is `<10 ms`.
- Evidence: `for raw_row in df.iter_rows(named=True): candidate = _jsonify_row(raw_row)` — jsonifies all columns; breaks early only on a *second* match, so a unique key scans the whole frame.
- User impact: On wide/long preview frames a multi-node path spends tens of ms per click on this gate alone; the "instant re-click" UX degrades on realistic data.
- Fix sketch: Vectorise via the (FR-05-fixed) `_build_value_match_expr`: `df.lazy().filter(reduce(and_, exprs)).head(2).collect().height == 1`. After FR-05 the shared-columns fast path (`:764-780`) largely collapses into `_find_matching_row` (FR-04), removing this gate.
- First failing test (structural): spy on `pl.DataFrame.iter_rows`; run the shared-columns warm path; assert it is not called on the parent frame.
- Confidence: high
- Overlap: tracked-as-FR-03 (still-open); interlocks with FR-04 (still-open)

### CORE-07 [MEDIUM] [robustness] — Trace `_cache` fingerprint omits `contracts=`, so contract enforcement is skipped on a cache hit after a toggle
- Location: `src/haute/trace.py:381-387` (trace key uses `f"{row_limit}:{source}"`) vs `src/haute/executor.py:934` (preview key includes `:contracts={int(enforce_contracts)}`)
- Claim: The trace's own cache key does not encode `ENFORCE_CONTRACTS`. If it flips `False→True` between two otherwise-identical trace requests, the second serves the first's cached frames and never runs the contract assertion that would otherwise raise `ContractMismatchError` (422).
- Evidence: `fp = graph_fingerprint(graph, target_node_id, f"{row_limit}:{source}", *runtime_extra_keys, ...)` — no `contracts=`. The executor and the trace's *preview-key reconstruction* (`trace.py:412`) both include it; only the trace's primary key omits it. `_execute_eager_core` raises `ContractMismatchError` regardless of `swallow_errors`, so a cache hit is the only bypass.
- User impact: Low in production (`ENFORCE_CONTRACTS` is a constant `True`); exposure is defense-in-depth / test-isolation and an inconsistency that will bite if enforcement ever becomes request-scoped.
- Fix sketch: Append `:contracts={int(ENFORCE_CONTRACTS)}` to the trace key's extra segment, mirroring the executor.
- First failing test: store a trace under `ENFORCE_CONTRACTS=False` for a contract-violating graph, flip to `True`, assert the second call raises rather than serving the cached trace.
- Confidence: high (behavior), low (production severity)
- Overlap: new (relates to lead #6)

### CORE-08 [MEDIUM] [robustness] — Tracing a multi-frame apiInput target crashes instead of failing cleanly
- Location: `src/haute/trace.py:472` / `src/haute/_trace_correlation.py:684-691`
- Claim: A multi-frame producer stores `eager_outputs[nid]` as `dict[label, DataFrame]` (`_execute_lazy.py:1920`). If such a node is the trace target, `target_df = eager_outputs[target_node_id]` is a dict; `len(target_df)`/`target_df.row(...)` raise `AttributeError`, surfacing as a generic 500.
- User impact: A user clicking a cell on a multi-table apiInput preview gets an opaque 500 rather than a clear message; not silent-wrong, but a crash in a plausible flow.
- Fix sketch: At `execute_trace` entry, detect a dict-valued target output and either resolve the relevant frame via `port_label` (as preview does) or raise a specific `ValueError` mapped to 400/422 with a "select a single-frame node" message.
- First failing test: graph whose target is a multi-frame apiInput; assert a clean 4xx-mapped `ValueError`, not `AttributeError`.
- Confidence: medium (code path reasoned; not reproduced — multi-frame construction is involved)
- Overlap: new

### CORE-09 [LOW] [performance|elegance] — Batched P03 tail: relaxed accounting, missing confidence signal, memoisation defeat, stale comments
- Location: `_trace_correlation.py:283-315` (FR-08), `:418-431` (FR-09), `_trace_enrichment.py:1227` (FR-10), `trace.py:368` + `_trace_enrichment.py:1092` (FR-11)
- Claim: (FR-08) `_find_matching_row` always builds the full O(rows×cols) per-cell `matched_by_row` even when a vectorised exact filter would answer the common case. (FR-09) a unique relaxed match of width 1-of-N is accepted with empty diagnostics — no `low_confidence_relaxed_match` signal. (FR-10) `_build_input_sources` passes `visited=set(visited)` per recursive branch, so a `(node_id, ref_col)` resolved under one branch re-runs `evaluate_expression` under every sibling. (FR-11) `trace.py:368` still says "single-entry cache" (it is an 8-entry byte-bounded LRU); `_trace_enrichment.py:1092` locates the current step via `all_steps.index(current_step)` (O(n) value-equality on a mutable dataclass) while `:1347` uses `node_id`.
- Fix sketch: FR-08 exact-filter-first, relaxed only on miss; FR-09 emit the diagnostic when `best_relaxed_width ≪ len(original_shared)`; FR-10 share one memo dict across the invocation; FR-11 reword the comment and unify on a `{node_id: index}` map.
- First failing test: FR-10 — counter on `evaluate_expression` for a diamond dependency (expect 1, currently 2).
- Confidence: high
- Overlap: tracked-as-FR-08/FR-09/FR-10/FR-11 (all still-open)

### CORE-10 [LOW] [elegance] — Effectively-unreachable partial-rows branch reads as a live fail-soft
- Location: `src/haute/trace.py:513-524` (the `else` of `if target_node_id in eager_outputs`)
- Claim: This branch builds partial rows from raw `row_index` with no `row_values` verification — but it is unreachable in the API: the cold path runs `_execute_eager_core(..., swallow_errors=False)`, where the first genuine failure *raises* (`_execute_lazy.py:2060-2061`) so no `None` output is ever stored and the target is always present; the preview-reuse path only returns when the full `order` (including target) is present (`trace.py:719-738`).
- Evidence: `swallow_errors=False` means the `failed_parents` None-propagation (`_execute_lazy.py:1785-1798`) has no origin — the raising node aborts execution first. Lead #2's silent-wrong concern therefore does not fire through the route today, but the code presents as a live path.
- User impact: None currently; latent risk + reader confusion. A future caller passing `swallow_errors=True` into this materialiser would silently show unverified rows.
- Fix sketch: Delete the branch and assert `target_node_id in eager_outputs` (fail-loud), or gate it behind `row_values is None` and document the invariant.
- First failing test: assert `execute_trace` raises (not returns partial) when the target genuinely has no output.
- Confidence: high
- Overlap: new (resolves lead #2)

## P03 status table
| FR | Status | Evidence |
| --- | --- | --- |
| FR-03 | still-open | `_shared_key_is_unique` still `for raw_row in df.iter_rows(named=True): _jsonify_row(...)` at `_trace_correlation.py:523-529`, called `:772` |
| FR-04 | still-open | Shared-columns fast path still duplicates `_find_matching_row` + `_shared_key_is_unique` at `_trace_correlation.py:764-780`; only the no-shared `elif` (`:777`) is unique |
| FR-05 | still-open (verified) | `_build_value_match_expr` returns `pl.col(column) == value` (`:233`), no tolerance; repro: fast=True, value-match=False |
| FR-06 | still-open (verified e2e) | `_find_target_row_index` first-match, no ambiguity check (`trace.py:262-271`); e2e relocation to `id=111` with empty diagnostics |
| FR-07 | still-open (verified) | `_jsonify_row` `else: clean[k] = str(v)` (`:97`); Datetime space-vs-T, List string-vs-array |
| FR-08 | still-open | `_match_columns_by_row_index` builds full per-cell accounting unconditionally (`:283-315`); `_find_matching_row` calls it before the exact check (`:395-400`) |
| FR-09 | still-open | Relaxed width-1 unique match returns with no `low_confidence_relaxed_match` diagnostic (`:418-431`) |
| FR-10 | still-open | `_build_input_sources` passes `visited=set(visited)` per branch (`_trace_enrichment.py:1227`) |
| FR-11 | still-open | `trace.py:368` "single-entry cache" (is 8-entry LRU); `_trace_enrichment.py:1092` `all_steps.index(current_step)` vs `:1347` node_id |

Note: commit b19ff1f4 ("W4-trace") did substantial fail-loud work on the correlation *matcher* and enrichment, but did not touch any FR-03…FR-11 site; it makes FR-06 more acute by hardening the sibling code path while leaving the entry point permissive.

## Strengths (verified-good behaviours worth pinning with regression tests)
- **Correlation matcher is genuinely fail-loud** (W4): `_find_matching_row` records `duplicate_exact_match` / relaxed-ambiguity diagnostics and returns `(None, -1)` rather than guessing (`_trace_correlation.py:401-443`) — verified in repro. Pin with a duplicate-key test asserting `(None, -1)` + diagnostic.
- **`_build_value_match_expr` is dtype-robust**: numeric/NaN/Inf vs an incompatible column dtype degrades to a non-match instead of raising `ComputeError` (`:196-232`). Pin with a numeric-value-vs-Utf8-column test.
- **Edge-join right-parent provenance** (`_edge_join_right_match_row`, `:562-616`) correctly routes suffixed/colliding columns and join keys to the right parent, and raises loudly when the base frame is missing.
- **Positional fast path is properly gated**: trusted only when shared keys match *and* uniquely identify the row, or the child transform provably preserves order (`_child_transform_may_reorder`, `:478-496`).
- **Fingerprint join is injective**: `graph_fingerprint` frames extra keys + context via `canonical_json` (`_cache.py:501-513`), avoiding separator-collision cache bleed.
- **Concurrency is safe** (contra lead #7/#10): `FingerprintCache`→`LRUCache` guards every op with an `RLock`; concurrent same-key traces are serialised by supersession; different-key concurrent first-clicks can double-execute the same materialisation but self-heal on first `store` (LOW perf, no corruption). The trace/preview byte budgets count the *same shared DataFrame objects* against separate caps — conservative over-counting, **not** a 2× memory blow-up.
- **Route error mapping** (`routes/pipeline.py:464-496`) maps 409/400/404/422/504 correctly for every reachable `ValueError`/`ContractMismatchError`; the string-coupling is fragile but currently accurate.

## Coverage note (what you did not examine)
- Did not deeply audit `_trace_waterfall.py` (build_waterfall_from_steps / C8 reconciliation) or the bulk of `_trace_enrichment.py` beyond FR-10/FR-11 sites and the `referenced_columns`/`_build_input_sources` path — enrichment/waterfall reviewers' scope.
- CORE-03 (pruning completeness) is medium-confidence: mechanism proven from code structure but no parser-blind-spot input constructed; the enrichment reviewer with `_expression_parser` context can confirm which real expressions under-report `referenced_columns`.
- CORE-08 (multi-frame target crash) reasoned from the code path, not reproduced; reachability depends on whether the frontend permits tracing from a multi-table apiInput cell — a UI-scope question.
- Did not exercise the live server or supersession under real async load; reviewed `_supersession.py` and `_timeouts.py` statically and found them sound (deferred limiter release on background completion, permit-ownership single-flag correctness).
- Repro scripts: `C:\Users\prici\AppData\Local\Temp\claude\C--Users-prici-haute\3887407c-e101-4b47-bf1f-6df135883d11\scratchpad\repro_trace.py` and `repro_e2e.py`.
