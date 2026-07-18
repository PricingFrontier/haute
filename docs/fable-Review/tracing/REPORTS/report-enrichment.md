# Report: enrichment-fidelity

## Verdict (≤10 lines)
The enrichment/waterfall layer is, on the whole, well-hardened against the deadliest drift class: the waterfall derives every number from consecutive observed values and fails loud on reconciliation breaches (C8 holds — verified end-to-end); rating key-matching shares `normalise_rating_key` with the engine; banding continuous re-matching is Float32-faithful when the parent dtype is available; optimiser-apply reconciles selected-scenario/factor-product against the output and raises otherwise. No CRITICAL silent-wrongness in a mainstream flow was reproduced. However, I found **real per-node fidelity gaps**, all reproduced: (1) rating-step `selected_value`/`rate_value` reports the **post-lookup user-code** value and attributes it to the table (README says this field is "the selected value per table"); (2) continuous-banding enrichment evaluates `!=`/`<>` operators the engine's `_OP_MAP` silently skips, attributing a band to a rule the engine never used; (3) model-score feature list is guessed from all input columns (incl. `quote_id`) when config lacks `feature_columns`/`contract`, and is not reconciled with the authoritative `explanation`; (4) `_sniff_operation_type` mislabels `list.join`/`str.join`/commented tokens as row-lineage joins/filters. Tracked FR-10 (memoisation defeat) and FR-11 (index/node_id inconsistency) are both **still open** — FR-11 has no correctness impact (node_ids unique). `_trace_export.export_trace` is dead production code (test-only). Baseline trace suites green (236 passed).

## Findings

### ENR-01 [MEDIUM] [correctness/informativeness] — rating-step trace attributes post-lookup user-code value to the rate table
- Location: `src/haute/_trace_enrichment.py:79` (`rate_value = output_row.get(output_col)`), used through `_enrich_single_table`→`enrich_rating_step:200-211`; execution order in `src/haute/_builders.py:898-900` (`_apply_rating_step_outputs` then `_exec_user_code`).
- Claim: `_enrich_single_table` reads the rate from the node's **final** output row, which for a ratingStep with `code` is the value **after** post-lookup user code. The table's reported `selected_value`/`rate_value` therefore reflects the post-code value, not the value the rating table produced, while `matched_entry` still shows the real table entry — an internally inconsistent rating explanation. README (lines 63-75) states rating traces show "the selected value per table"; that field is wrong here.
- Evidence: repro `scratchpad/probe_rating_postcode.py` — table `region→rate` returns 1.1; node `code = df.with_columns(rate=pl.col('rate')*2)`:
```
ENGINE actual output rate: 2.2   (table gave 1.1, post-code x2 = 2.2)
  selected_value: 2.2  rate_value: 2.2  matched: True  status: matched
  matched_entry: {'region': 'north', 'value': 1.1}
  top-level rate_value: 2.2  matched: True
```
- User impact: a regulator reading the rating step sees the table "selected 2.2" while its own matched entry says 1.1; the post-code transform is silently folded into the table's attributed output. Realistic trigger — rounding/capping/flooring premiums in ratingStep post-code is common.
- Fix sketch: distinguish the raw table output from the post-code output. Either (a) surface the raw lookup value separately (recompute via shared `_apply_rating_table` semantics, or capture pre-code) and label the post-code delta as a distinct step; or (b) when `config.get("code")` is non-empty, derive `selected_value` from `matched_entry["value"]`/default and label it "table lookup (before node post-processing)".
- First failing test: ratingStep with single table (`north→1.1`) and `code=rate*2`; assert `node_detail["tables"][0]["selected_value"] == 1.1` (currently 2.2).
- Confidence: high
- Overlap: new

### ENR-02 [MEDIUM] [correctness] — continuous-banding enrichment evaluates `!=`/`<>` operators the engine skips, attributing bands to rules the engine never used
- Location: `src/haute/_trace_enrichment.py:286-295` (`_match_continuous_rule` `op_fn` includes `"!="`,`"<>"`) vs engine `src/haute/_rating.py:33` (`_OP_MAP` = only `< <= > >= = ==`) and `_rating.py:50-53` (unknown op → `method is None` → `continue`, rule contributes no condition).
- Claim: the two modules use divergent operator tables. A rule with `op1:"!="` is a no-op in the engine (skipped) but truthy in enrichment. Because enrichment's `_values_equivalent(assignment, selected_band)` guard only requires the assignment to match, a `!=` rule that shares its assignment with the real matching rule is reported as the matched rule (with `!=` conditions/no bounds), even though the engine produced the band from a different rule.
- Evidence: repro `scratchpad/probe_banding_ops.py`:
```
engine _OP_MAP keys: ['<', '<=', '=', '==', '>', '>=']
ENGINE band for v=5: X   (from rule 1 '>0'; rule 0 '!=3' is skipped)
ENRICHMENT rule_index: 0  matched_rule: {'op1':'!=','val1':3,'assignment':'X'}
ENRICHMENT conditions/bounds: {'lower_bound': None,'upper_bound': None,'conditions':[{'operator':'!=','value':3.0}]}
```
- User impact: the banding step shows "condition `!= 3`" as the reason for the band when the engine actually applied `> 0`; the displayed rule index, matched_rule and bounds are wrong. Reachable — no operator allow-list in `_banding_config.py`/`schemas.py` rejects `!=`. (Separately, the engine silently dropping `!=` rules is its own latent issue, outside trace scope.)
- Fix sketch: make the enricher's operator set the single source of truth shared with the engine (import/reuse `_OP_MAP`, or both derive from one table). Enrichment must treat operators the engine doesn't support as non-matching so it can never claim a rule the engine skipped.
- First failing test: `_apply_banding` + `enrich_banding` on `rules=[{op1:'!=',val1:3,assignment:'X'},{op1:'>',val1:0,assignment:'X'}]`, `v=5`; assert enrichment `rule_index == 1` (currently 0).
- Confidence: high
- Overlap: new

### ENR-03 [MEDIUM] [informativeness] — model-score feature list guessed from all input columns; not reconciled with the authoritative explanation
- Location: `src/haute/_trace_enrichment.py:683-701`; guess path `:692-694` (`feature_columns = [k for k in input_row if k != prediction_column]`). Authoritative list lives in `src/haute/_model_explainability.py` (`features = list(scoring_model.feature_names)`, e.g. `:46,:182,:275,:488`).
- Claim: when the node config carries neither `feature_columns` nor a `contract.inputs`, the enricher lists **every** input column (including technical columns such as `quote_id`, `policy_ref`) as the model's `feature_columns`/`feature_values`. These top-level fields are set before the explainer runs and are never overwritten by the authoritative `explanation` (which uses `scoring_model.feature_names`). The payload thus carries two disagreeing feature lists; if the explainer errors (no model/env), only the guessed one remains.
- Evidence: repro `scratchpad/probe_modelscore.py`:
```
feature_columns: ['quote_id', 'policy_ref', 'driver_age', 'vehicle_value']
feature_values: {'quote_id': 'Q123', 'policy_ref': 'P9', 'driver_age': 40, 'vehicle_value': 20000}
-> quote_id/policy_ref (technical cols) reported as model features: True
```
- User impact: the trace claims the model consumed `quote_id`/`policy_ref` as features. Regulator-facing misrepresentation of which inputs drove the prediction.
- Fix sketch: don't emit a guessed `feature_columns`/`feature_values` at top level. Either omit them when no explicit contract exists, or populate from `explanation`'s authoritative `feature_names` once available (mark clearly as "inferred — model artifact contract unavailable" only as a last resort).
- First failing test: `enrich_model_score` with config lacking `feature_columns`/`contract`; assert `quote_id` not in `detail["feature_columns"]` (or field absent) — currently present.
- Confidence: high
- Overlap: new

### ENR-04 [LOW→MEDIUM] [informativeness] — `_sniff_operation_type` substring match mislabels string ops / commented tokens as row-lineage joins/filters
- Location: `src/haute/_trace_enrichment.py:1417-1423` (`_sniff_operation_type`), table `:1407-1414`; consumed at `:1856-1863` feeding `detect_row_lineage_type`.
- Claim: the label is derived by naive substring search of the node code. `.list.join(...)`/`.str.join(...)` (string concatenation, row-count-preserving) contain the substring `.join(` and are labelled `join`→`row_lineage_type="joined"`; `.filter(` in a comment/string is labelled `filter`→`"filtered"`.
- Evidence: repro `scratchpad/probe_sniff.py`:
```
'list.join (string concat, NOT a row join)' -> op='join'   lineage='joined'
'str.join'                                  -> op='join'   lineage='joined'
'comment mentioning .filter('               -> op='filter' lineage='filtered'
```
- User impact: the row-lineage label (shown as authoritative in the payload) misclassifies passthrough transforms as joins/filters; for a `list.join` on a passthrough (rows in == rows out) the "joined" label is simply false.
- Fix sketch: prefer the structural signal already available — `detect_row_lineage_type` receives parent/child row counts; when counts are equal, don't let a sniffed `join`/`filter` override `passthrough`. Better: match operators with word-boundary/`.method(` regexes that exclude `.list.join`/`.str.join`, and strip comments/strings before sniffing.
- First failing test: node code `df.with_columns(tags=pl.col('parts').list.join(','))` with equal parent/child row counts; assert `row_lineage_type == "passthrough"` (currently "joined").
- Confidence: high
- Overlap: new

### ENR-05 [MEDIUM] [performance] — `_build_input_sources` copies `visited` per branch, re-deriving shared subtrees (FR-10 still open)
- Location: `src/haute/_trace_enrichment.py:1227` (`visited=set(visited)` on the recursive call).
- Claim: passing a fresh copy of `visited` into each recursive branch defeats cross-branch memoisation; a column resolved under one branch (running `evaluate_expression`) is re-derived under every sibling branch. Diamond dependencies re-walk the shared subtree once per path (bounded by `max_depth=3`).
- Evidence: repro `scratchpad/probe_fr10.py` — diamond `T←{A,B}`, both `A,B←S`:
```
eval calls: 4        # A, S, B, S  → 's' evaluated twice
Does 's' appear under both a and b? True True
```
- User impact: redundant expression evaluation on every trace click for diamond lineages; contradicts the module's warm-click latency budget. Not a fidelity defect.
- Fix sketch (from FR-10): thread one shared `visited`/memo dict through the whole invocation; the key already includes `(node_id, ref_col)` so cross-branch reuse is sound.
- First failing test: counter on `evaluate_expression` for the diamond above; expect the shared `s` evaluated once (currently twice).
- Confidence: high
- Overlap: tracked-as-FR-10 (still open — unchanged since P03)

### ENR-06 [LOW] [elegance] — `_trace_export.export_trace` is dead production code with test-only coverage, drifted from `trace_result_to_dict`
- Location: `src/haute/_trace_export.py:8`.
- Claim: `export_trace` is referenced nowhere in `src/` except its own definition (and a docstring mention in `trace.py:29`); the only callers are `tests/test_trace_coverage.py`. No route, CLI, or package export uses it. Its `formula`/`sources` derive from "the first step that adds/modifies the column" (`_trace_export.py:27-33,66-69`), a re-derivation independent of the enriched `step.expression`/`calculation` that `trace_result_to_dict` serialises — so the two report shapes can disagree (e.g. multiple assignments, banding lineage).
- Evidence: `grep -rn export_trace src/` → only `_trace_export.py`; all other hits under `tests/`.
- User impact: none live; carries maintenance weight and coverage that masks its unused status (per the repo's "delete dead code, don't game coverage" guidance).
- Fix sketch: wire it to the report/CLI path it was written for, or delete it and its tests; if kept, source `formula`/`sources` from the same enriched step fields the API serialises.
- First failing test: n/a (removal) — or an integration test asserting a route/CLI produces `export_trace` output.
- Confidence: high
- Overlap: new

### ENR-07 [LOW] [elegance] — FR-11 index/node_id lookup inconsistency still open (no correctness impact)
- Location: `src/haute/_trace_enrichment.py:1092` (`all_steps.index(current_step)`, O(n) value-equality on a mutable dataclass) vs `:1347` (`s.node_id == current_step.node_id`).
- Claim: two different idioms locate the current step. I adversarially checked the value-equality collision hypothesis: it cannot fire because each step carries a unique `node_id` (one step per node id from `_assemble_steps`), so `TraceStep.__eq__` never ties two distinct steps. FR-11 is therefore **style/perf only**, not a correctness risk.
- Evidence: `scratchpad/probe_final.py` — two steps differing only by `node_id` are `!=`; steps in `all_steps` always have distinct ids.
- User impact: negligible (minor O(n) per column); consistency/readability.
- Fix sketch (from FR-11): build a `{node_id: index}` map once per `enrich_steps` and index by `node_id` everywhere.
- First failing test: n/a (refactor); a micro-test asserting a `{node_id:index}` helper matches `.index()` on a unique-id list.
- Confidence: high
- Overlap: tracked-as-FR-11 (still open; correctness concern downgraded — node_ids are unique)

### ENR-08 [LOW] [correctness/robustness] — `_fix_upstream_values` mutates observed output values (design concern; well-guarded)
- Location: `src/haute/_trace_enrichment.py:976-1064` (writes `s.output_values[col_name] = new_row.get(col_name)` at `:1036-1037`).
- Claim: in an explainability tool that promises to "show exactly the data the user sees", this function *rewrites* an upstream step's observed cell using a value re-derived by `evaluate_expression` to relocate a row. It patches a single column when correlation left it null; the rest of the step's `output_values` still come from the correlator's row, so a patched step can display a row that never existed (some columns from row R, one column from row R'). A wrong write additionally requires `evaluate_expression` to diverge from the engine.
- Evidence: code reading + the tight guards — unique-match required (`:1035`), scale-relative float tolerance `abs*1e-9+1e-12` (`:1031`), ambiguity logged not guessed (`:1038-1044`). I did **not** reproduce a live wrong write (guards held in every constructed case), so this is a design/honesty observation, not a confirmed defect.
- User impact: potential per-column Frankenstein rows in degraded correlation cases; bounded by the guards.
- Fix sketch: prefer re-correlating the whole upstream row from the known value (write all columns of the matched row atomically, or none), and annotate the step visibly as "row relocated during trace" so the substitution is not presented as a raw observation.
- First failing test: source frame with two rows sharing the traced column's value but differing elsewhere, a null-correlated upstream step; assert the patched step's full row is self-consistent (all columns from one source row) — or that the step is annotated as relocated.
- Confidence: medium (guards appear sound; concern is design honesty, not a reproduced miswrite)

## Node-type fidelity matrix
| Node type | Enriched? | Re-derivation drift risk | Verified? |
|---|---|---|---|
| ratingStep (tables) | yes | **post-code misattributes selected_value (ENR-01)**; key-match/default via shared `normalise_rating_key` faithful | yes (engine-vs-enrich + e2e trace) |
| banding continuous | yes | **op-set asymmetry `!=`/`<>` (ENR-02)**; Float32 boundary faithful when parent dtype present; falls back to f64 if dtype missing | yes (engine-vs-enrich + probe_f32) |
| banding categorical | yes | `str()` vs Polars Utf8 cast — agree for common float/int/label values | yes (probe_final) |
| banding breakpoints | yes | converts to continuous via shared `_breakpoints_to_rules` | yes (existing test) |
| modelScore | yes | **feature list guessed from all input cols (ENR-03)**; authoritative list only inside `explanation` | yes (probe_modelscore) |
| scenarioExpander | yes | thin (value/index/params echoed from output row); lineage type via row-count | partial (code read) |
| liveSwitch | yes | maps `input_scenario_map` → active/pruned by `source`; no cross-check vs executor prune | partial (code read) |
| optimiserApply (online/ratebook) | yes | strong fail-loud reconciliation (selected≠output, exactly-one selected/baseline, factor product) | yes (code + existing suite green) |
| edgeJoin | **no node_detail enricher** | falls to raw values + row-lineage "joined"; waterfall has dedicated edge-join branch-collision guards | partial |
| polars/custom code | expression/calculation only | evaluate_expression fidelity is the load-bearing assumption (see ENR-08) | partial |
| output/sink | **no enricher** | raw passthrough values only | n/a |

## Strengths (verified-good behaviours worth pinning)
- **Waterfall C8 reconciliation holds**: `build_waterfall_from_steps` derives factors from consecutive observed values, shows a no-op ×1.0 step as an explicit identity contribution, and the final cumulative reconciles to the traced output (`probe_rename_waterfall.py`: 100→×1.0→×1.2→×1.0→×0.9 = 108, exact). `_check_display_consistency`, zero/negative/sign-flip → additive-delta fallbacks, and the `{"error":...}` structured payload are sound fail-loud designs.
- **Banding dtype faithfulness**: `_coerce_pair_through_dtype` matches Polars' own f32 comparison semantics (Polars downcasts the literal to the column dtype; `probe_f32.py` confirms engine `le/gt` == coerced-pair result). Wired correctly from parent eager-output schemas in `enrich_steps:1772-1784`.
- **Rating key matching**: `_enrich_single_table` shares `normalise_rating_key` with `_rating_key_expr` and walks entries in reverse to mirror `unique(keep="last")` — matched_entry/default detection cannot diverge from the join for the *key* decision (only the *value* field drifts, ENR-01).
- **Optimiser-apply reconciliation**: online and ratebook paths both raise `OptimiserApplyTraceError` when the reconstructed selection/factor-product disagrees with the clicked output — clamped-output cases fail loud rather than mislead.
- **`_detect_rename` is code-based, not value-equality** (refutes lead #7): `probe_rename_waterfall.py` shows two equal-valued columns produce no false rename chain; detection keys on `.rename({...})`/pure `pl.col('old')` syntax.

## Coverage note
- Reproduced empirically (scratchpad scripts, engine-vs-enrichment and end-to-end `execute_trace`): ENR-01, ENR-02, ENR-03, ENR-04, ENR-05, ENR-07, plus banding-dtype and waterfall strengths. Baseline: `test_trace_enrichment/banding_lineage/waterfall/optimiser_apply_trace_enrichment` = 236 passed.
- Code-read only (no live run): optimiserApply online/ratebook (needs `price_contour` + MLflow artifact) and modelScore `explanation` (needs a loaded model) — relied on passing existing suites and reconciliation-guard reading. scenarioExpander/liveSwitch enrichers reviewed but not adversarially fuzzed.
- Not reproduced: a live `_fix_upstream_values` wrong-write (ENR-08) — guards held in every constructed case; reported as a design/honesty observation at medium confidence. `evaluate_expression`-vs-engine fidelity (the load-bearing assumption behind input_sources and ENR-08) was out of scope for deep fuzzing and is the highest-value place to look next for a CRITICAL.
- Prior tracked items verified against current tree: **FR-10 still open** (ENR-05), **FR-11 still open** but downgraded to style-only (ENR-07).

All scratch repros live in `C:\Users\prici\AppData\Local\Temp\claude\C--Users-prici-haute\3887407c-e101-4b47-bf1f-6df135883d11\scratchpad\` (probe_rating_postcode.py, probe_banding_ops.py, probe_modelscore.py, probe_sniff.py, probe_fr10.py, probe_f32.py, probe_rename_waterfall.py, probe_final.py). No repo files were created/edited/deleted.
