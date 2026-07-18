# T04 — Per-node enrichment must never explain a different calculation than the engine ran

**Severity:** MEDIUM ×4 (all reproduced, all silent-wrongness *of the explanation*) + design item
**Effort:** M · **Dev/reviewer pair: REQUIRED** (silent-wrongness class)
**Files:** `src/haute/_trace_enrichment.py`, `src/haute/_rating.py` (shared constant only),
`src/haute/trace.py` (`_assemble_steps`)
**Origin:** ENR-01/02/03/04 + ENR-08 (enrichment review), CORE-05 (backend-core review)
**Repros:** `repros/probe_rating_postcode.py`, `repros/probe_banding_ops.py`,
`repros/probe_modelscore.py`, `repros/probe_sniff.py`

The enrichment layer re-derives what the engine did in order to explain it. Each item below is a
place where the re-derivation (or presentation input) can disagree with the engine. None corrupts
the *observed values*; all corrupt the *story about them* — which, for a regulator-facing tool, is
the same defect class.

## T04.1 (ENR-01) — rating `selected_value` reports the post-user-code value as the table's output

`_trace_enrichment.py:79` reads `rate_value = output_row.get(output_col)` — the node's **final**
output, i.e. *after* the ratingStep's optional post-lookup Polars code
(`_builders.py:898-900` order: `_apply_rating_step_outputs` → `_exec_user_code`). Reproduced:
table returns 1.1, `code` doubles it → the step shows `selected_value: 2.2` beside
`matched_entry: {value: 1.1}`. Rounding/capping in post-code is common, so this is a mainstream
inconsistency in the flagship rating explanation.

**Fix:** when `config["code"]` is non-empty, derive `selected_value`/`rate_value` from the matched
entry (or default) — the value the *table* produced — and expose the node's final value separately
so the post-code delta is visible as its own line ("table lookup 1.1 → after node code 2.2").
The key-matching path (`normalise_rating_key`, reverse-walk mirroring `unique(keep="last")`) is
verified-correct — do not touch it.
**Failing test:** ratingStep with `north→1.1` + `code=rate*2`; assert
`node_detail["tables"][0]["selected_value"] == 1.1` (currently 2.2) and the post-code value is
surfaced under a distinct key.

## T04.2 (ENR-02) — banding enrichment evaluates operators the engine skips

`_match_continuous_rule` (`_trace_enrichment.py:286-295`) supports `!=`/`<>`; the engine's
`_OP_MAP` (`_rating.py:33`) supports only `< <= > >= = ==` and **silently skips** rules with other
operators (`_rating.py:50-53`). Reproduced: engine banded `v=5` via rule 1 (`>0`); enrichment
attributed the band to rule 0 (`!=3`) — a rule the engine never evaluated — with wrong
conditions/bounds displayed.

**Fix:** single source of truth for the operator set — export `_OP_MAP` (or a frozen key-set
constant) from `_rating.py` and have `_match_continuous_rule` treat any operator outside it as
non-matching. Add a unit test asserting the two modules' operator sets are identical (import-level
pin, so future engine additions force the enricher to follow).
**Failing test:** rules `[{op1:'!=',val1:3,assignment:'X'},{op1:'>',val1:0,assignment:'X'}]`,
`v=5`; assert enrichment `rule_index == 1` (currently 0).
*(Separately: the engine silently skipping unknown operators is its own fail-loud violation —
out of trace scope; flag to the maintainer rather than fix here.)*

## T04.3 (ENR-03) — model-score feature list guessed from all input columns

`_trace_enrichment.py:692-694`: with neither `feature_columns` nor `contract.inputs` in config,
`feature_columns = [k for k in input_row if k != prediction_column]` — every column, including
`quote_id`/`policy_ref`, is presented as a model feature (reproduced). The authoritative list
(`scoring_model.feature_names`, used by `_model_explainability`) never overwrites it, so the payload
can carry two disagreeing feature lists, and only the guessed one survives if the explainer errors.

**Fix:** never emit a guessed list as fact. Omit `feature_columns`/`feature_values` when no explicit
contract exists and let the `explanation` block be the source; if a placeholder is genuinely needed,
mark it `"inferred": true` and have `ModelScoreDetail` render the caveat. Reconcile with
`explanation.feature_names` when the explainer succeeds.
**Failing test:** `enrich_model_score` with config lacking both keys; assert `quote_id` is not
presented as a feature (field absent or flagged inferred).

## T04.4 (ENR-04) — `_sniff_operation_type` substring matching mislabels row lineage

`_trace_enrichment.py:1417-1423` labels node code by naive substring: `.list.join(`/`.str.join(`
→ "join"→`row_lineage_type="joined"`; `.filter(` inside a comment → "filtered". Reproduced.
The structural truth (parent/child row counts) is already available to `detect_row_lineage_type`.

**Fix:** row counts win — when parent and child row counts are equal, a sniffed join/filter must not
override `passthrough`. Tighten the sniffer: strip comments/strings, exclude `.list.join`/
`.str.join` via a `(?<!\.list)(?<!\.str)\.join\(` style match. Combine with T10's relabeling of
`passthrough` for display.
**Failing test:** node code `df.with_columns(tags=pl.col('parts').list.join(','))`, equal row
counts; assert `row_lineage_type == "passthrough"` (currently "joined").

## T04.5 (CORE-05) — join-input snapshot: first parent's colliding column stays bare

`trace.py:817-820` namespaces only the *second+* parent's colliding key (`{pid}.{k}`); the first
parent's copy keeps the bare name (order-dependent ownership), and `_compute_schema_diff` then
classifies `pB.shared` as a **removed** column (reproduced:
`input_values = {'shared': 'A_value', …, 'pB.shared': 'B_value'}`, `removed = ['pB.shared']`).

**Fix:** namespace symmetrically — when `k` occurs in >1 parent, emit `{pid}.{k}` for **every**
parent and no bare key; teach `_compute_schema_diff` to treat `{pid}.{k}` inputs as provenance
variants of `k` (compare the child's `k` against each, classify as modified/passed against the
surviving side, never "removed"). Check `StepCard`'s input rendering handles the prefixed keys
(it renders raw keys today, so prefixed keys display fine; verify the schema-diff colour logic).
**Failing test:** two parents sharing `shared` with different values into one child; assert both
values appear under `pA.shared`/`pB.shared`, no bare `shared` in `input_values`, and
`columns_removed == []`.

## T04.6 (ENR-08) — `_fix_upstream_values` rewrites observed values (design honesty)

`_trace_enrichment.py:976-1064` patches a single upstream cell from `evaluate_expression` output
when correlation left it null. Guards are tight (unique match, scale-relative tolerance, ambiguity
logged) and no wrong write was reproduced — but the patched step can mix columns from two source
rows, presented as one observation. **Fix (small):** when the patch fires, write the *entire*
matched row atomically (or nothing) and append a visible `row_relocated_during_trace` note to the
step (render as a muted chip). Keep the guards.
**Failing test:** source frame with two rows sharing the traced value but differing elsewhere;
patched step's row is column-consistent with exactly one source row and carries the annotation.

## Acceptance for the package

- All six failing tests above pass; `pytest tests/test_trace_enrichment.py tests/test_trace_banding_lineage.py tests/test_trace.py -q` green.
- Grep-level pin: one shared operator table between `_rating` and enrichment (unit test imports both).
- The verified-good behaviours stay pinned: rating key normalisation/default detection, banding
  Float32 boundary semantics (`repros/probe_f32.py`), waterfall C8 reconciliation, optimiser-apply
  reconciliation errors, `_detect_rename` code-based detection (no value-equality false renames).
