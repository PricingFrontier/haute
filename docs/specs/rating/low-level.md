# Rating — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_rating.py` | Pure-logic frame transforms: banding rule evaluation (`_apply_banding`, `_banding_condition`, `_breakpoints_to_rules`) and the banding-factor loop (`_apply_banding_factors`), rating-table lookup (`_apply_rating_table`), combining (`_combine_rating_columns`, `_combine_rating_output`), the canonical factor-key form (`normalise_rating_key`, `_rating_key_expr`), the rating-step loop (`_apply_rating_step_outputs`) and the two generated-code entry points (`apply_banding_from_config`, `apply_rating_step_from_config`). |
| `src/haute/_rating_step_config.py` | Rating-table config normalisation: compact-sidecar nested-map ⟷ canonical row-array conversion (`expand_rating_step_config_from_sidecar`, `compact_rating_step_config_for_sidecar`, `normalise_rating_tables`), factor/entry validation, and the sidecar factor-key canonicalisation (`_canonical_sidecar_key`) shared with `_rating.normalise_rating_key`. |
| `src/haute/_banding_config.py` | Banding config normalisation: compact key/value-map ⟷ canonical row-array conversion for `categorical`/`breakpoints` rules (`expand_banding_config_from_sidecar`, `compact_banding_config_for_sidecar`, `normalise_banding_rules`, `normalise_banding_factors`). |

## Key types and data structures

- **Banding factor** (`dict`): `{"banding": "continuous"|"categorical"|"breakpoints", "column": str, "outputColumn": str, "rules": [...] | {...}, "default": Any, "rightClosed"?: bool}`. `rules` is a list of row dicts in the canonical in-memory shape; `categorical`/`breakpoints` sidecars persist it as a `{key: value}` map instead.
  - `continuous` rule row: `{"op1"?, "val1"?, "op2"?, "val2"?, "assignment": str}`.
  - `categorical` rule row: `{"value": str, "assignment": str}` (sidecar map: `{value: assignment}`).
  - `breakpoints` rule row: `{"boundary": str, "label": str}` — empty `boundary` marks the open-ended tail (sidecar map: `{boundary: label}`).
- **Rating table** (`dict`): `{"name"?: str, "factors": list[str] (1-3 cols), "outputColumn": str, "entries": list[dict] | dict (compact), "defaultValue"?: str|number, "onMissing"?: "error"|"neutral"}`. Canonical `entries` row: one key per factor plus `"value"` (numeric). Invariant: `len(factors) <= _MAX_RATING_FACTORS` (3), enforced in `_rating_step_config._validate_factors`.
- **Combined output** (`dict`): `{"outputColumn": str, "operation": "multiply"|"add"|"min"|"max", "baseValue": float | None, "_legacy"?: bool}`. Legacy shape is `config["combinedColumn"]` + `config["operation"]` (top-level, singular) — converted to one `_legacy: True` entry with `baseValue: None` by `_normalise_combined_outputs`; a legacy entry with `< 2` table output columns is skipped (no lone-column combine).
- **`RatingTableMissError(ValueError)`** — raised at frame materialisation, not at config-build time, by `_rating_miss_guard_expr`'s `map_batches` callback.
- Invariant enforced across both config modules: `_canonical_sidecar_key` (rating) and `_compact_rule_map`/`_compact_rule_rows` (banding) are *provably symmetric* with their expand-side counterparts — a value that compacts to key `K` must expand from `K` back to an equal value — so a save/load round trip never silently drops or renames a table entry.

## Control flow

**Banding** — `apply_banding_from_config(lf, config, base_dir=None)`:
1. Resolve `config` (dict, or load a JSON sidecar via `_config_io.load_node_config`).
2. `_normalise_banding_factors(config)` → `_banding_config.normalise_banding_factors` → expands sidecar rule maps to row-array shape via `expand_banding_config_from_sidecar`.
3. `_apply_banding_factors(lf, factors)` loops factors in order, calling `_apply_banding` per factor; each factor's output column is added via `lf.with_columns(...)`, so later factors can already see earlier factors' output columns.
4. Inside `_apply_banding`: `breakpoints` rules are converted to `continuous` rules first (`_breakpoints_to_rules`); float input columns are NaN/Infinity-sanitised to null (`when(is_nan|is_infinite).then(null).otherwise(col)` — built as a *local* expression, never aliased back onto the source column, so it cannot corrupt other nodes' view of that column); then a `pl.when/then` chain is built rule-by-rule (`_banding_condition` turns each rule's `op1/val1[,op2/val2]` into a boolean expression, ANDed together) and finished with `.otherwise(default)`.
   Operators are resolved through the exported immutable
   `SUPPORTED_BANDING_OPERATORS` contract. An unknown operator raises before a
   `when` branch or output frame is published; trace enrichment imports the same
   contract and cannot interpret a broader operator set.
5. `categorical` bypasses the when/then chain entirely and uses `col.cast(Utf8).replace_strict(remap, default=...)`.

**Rating** — `apply_rating_step_from_config(lf, config, base_dir=None)`:
1. Resolve `config` (dict or sidecar path), same pattern as banding.
2. `normalise_rating_tables(config)` (from `_rating_step_config.py`) expands compact sidecar entry maps to row-array shape.
3. `_normalise_combined_outputs(config)` validates/normalises `combinedOutputs` (or the legacy `combinedColumn`/`operation` pair).
4. `_apply_rating_step_outputs(lf, tables, combined_outputs)`:
   a. Coerce `pl.DataFrame` input to `.lazy()`.
   b. Collect the frame schema **once** up front into a local `dict`, then thread it through every table call (`input_schema=schema`) instead of re-collecting after each table — this keeps schema resolution `O(n)` instead of `O(n²)` in the number of tables, since each `_apply_rating_table` call would otherwise re-run `collect_schema()` on a lazy plan that has grown by one join.
   c. For each table: call `_apply_rating_table`; if it actually materialised an output column (`_rating_table_skip_reason(table) is None`), register the output column name and its `Float64` dtype in the local schema dict for subsequent tables/combines to see; otherwise log `rating_table_skipped_incomplete` at WARNING with the specific skip reason and omit it from combining.
   d. For each combined output: call `_combine_rating_output(lf, out_cols, operation, output_col, base_value)`, skipping legacy combined outputs when fewer than 2 table columns exist.
5. `_apply_rating_table(lf, table, input_schema=...)` (per table):
   a. Return `lf` unchanged (documented no-op) if `factors`, `entries`, or `outputColumn` is empty.
   b. Parse `defaultValue`: tolerate non-numeric/non-finite strings (treated as "no usable default", noted in the eventual miss error rather than silently ignored).
   c. Build a `pl.DataFrame(entries)`; return unchanged if it has no `"value"` column; reject (raise `ValueError`) any NaN/Infinity or null `value` entries.
   d. Select only `[*factors, "value"]` from entries (drops any extra keys the sidecar/GUI may have left in an entry dict); return unchanged if any factor is absent from the entries' columns.
   e. Canonicalise the lookup side's factor columns with `_rating_key_expr` **before** deduplication, then `lookup.unique(subset=factors, keep="last")` — see [Edge cases](#edge-cases-and-invariants) for why the ordering does not actually change any observable output today.
   f. Rename `value` → an internal collision-proof name (`__haute_lookup_val__`).
   g. Validate factor dtypes are not `Date`/`Datetime` (raise otherwise); canonicalise the **input** frame's factor columns with the same `_rating_key_expr`.
   h. Left join (`how="left", maintain_order="left"` — explicit to defend against Polars streaming joins otherwise emitting hash-partition row order).
   i. If no usable default: wire in `_rating_miss_guard_expr` (see below) — placed *before* the dtype revert so any error/warning shows the canonical join key strings, not the original dtype's repr.
   j. Revert factor columns to their original dtypes (the canonicalisation cast in step g was join-local).
   k. Rename the internal lookup column to `outputColumn`, applying `fill_null(defaultValue)` if a default exists.
   l. Drop the internal lookup column.
6. `_rating_miss_guard_expr` wraps `pl.struct([*factors, lookup_val]).map_batches(_check, is_elementwise=True)`: runs as a Polars `map_batches` (lazy- and streaming-compatible; fires once per batch at materialisation, never re-executes the upstream plan). Per batch: unnest the struct, find null lookup values, and either log a WARNING (`onMissing: "neutral"`) or raise `RatingTableMissError` (`onMissing: "error"`), listing up to `_MISS_KEY_DISPLAY_CAP` (10) distinct missing key combinations.
7. `_combine_rating_output` (per combined output): if `baseValue` is `None`, delegates straight to `_combine_rating_columns`; otherwise adds a uniquely-named literal column (`__haute_rating_base_{output}__`, prefixed with more `_` until it doesn't collide with an existing column or the table-output list) holding `baseValue`, prepends it to the columns list, combines, then drops the scratch column.
8. `_combine_rating_columns`: single-column input is a plain alias/rename (no arithmetic). Multi-column: `add` folds nulls/NaN to `0.0` per column before summing; `multiply` folds to `1.0` before multiplying; `min`/`max` use `pl.min_horizontal`/`pl.max_horizontal` (which skip nulls natively, no fill needed).

## Edge cases and invariants

- **Canonicalise-then-dedup ordering (F084):** canonicalising factor keys before `lookup.unique(...)` is the structurally correct order, but is *observationally a no-op* for every constructible input today — `pl.DataFrame(entries)` already coerces a mixed float/string factor column to one `String` column via Polars' own float→string cast (which already collapses `25.0` to `"25"`), and within a homogeneous numeric column Polars' `unique` groups raw values exactly as their canonical strings would. The order is kept because it is the one that stays correct if that Polars coercion behaviour ever changes; it is deliberately *not* covered by a test that could detect a reorder (an exhaustive pairwise entry sweep in `tests/test_rating_key_agreement.py::TestDedupOnCanonicalKeys` finds zero divergence either way), only by the reasoning left in the source comment.
- **Float32 → Float64 widening in `_rating_key_expr`:** `Float32` factor columns are widened to `Float64` *before* formatting to a string key. The Python mirror (`normalise_rating_key`) only ever sees values already promoted to `Float64` across the trace/JSON boundary (Python has no distinct `Float32` scalar), so formatting at native `f32` precision on the engine side would make the engine's join key diverge from the trace's mirror key for the same logical value — a trace could then report "matched" when the join actually missed (or vice versa). Widening first keeps the two in agreement for every float dtype; pinned by `tests/test_rating_key_agreement.py`.
- **Int-like float collapse is range-bounded:** `normalise_rating_key`/`_rating_key_expr` only collapse a finite int-like float to its integer digit string when it is inside `[-(2**63), 2**63)` (the `Int64` range), because the cast to `Int64` on the engine side (`strict=False`) is only exact/lossless there. Outside that range (e.g. `1e300`), both sides fall through to Polars' `Utf8` cast instead, deliberately consistently.
- **`Decimal` factor columns** fall through to a plain `Utf8` cast at the column's declared scale — a `Decimal` factor level must be authored at that same scale (`"25.50"` for a scale-2 column); `"25.5"` and `"25.50"` are distinct string keys and will not match each other.
- **String factor levels never collapse:** a string label that happens to spell an int-like float (e.g. the literal string `"25.0"`) is kept verbatim by `normalise_rating_key` — only a genuine Python `float`/`int` collapses. The *sidecar* key canonicaliser (`_canonical_sidecar_key`) is stricter: a string label containing `.`/`e`/`E` that parses as a finite float is deliberately collapsed to the canonical numeric form too, because a compact JSON object key cannot otherwise distinguish the number `25.0` from the label `"25.0"` — leaving them distinct there would let two logically-identical entries compact to different keys and then be silently unrecoverable on expand.
- **Duplicate breakpoint boundaries / multiple open-ended breakpoints / a sole open-ended breakpoint with no bounded anchor** all raise `ValueError` in `_breakpoints_to_rules` rather than silently producing an empty interval or dropping data (see high-level Failure model).
- **Unknown continuous-band operators fail loudly:** `_banding_condition`
  raises for any operator absent from `SUPPORTED_BANDING_OPERATORS`; neither
  eager/lazy execution nor trace enrichment may silently skip or reinterpret it.
- **B15 entry-column pollution guard:** `_apply_rating_table` selects only `[*factors, "value"]` from the entries `DataFrame` before joining, so stray extra keys left in an entry dict (e.g. leftover UI state) never leak into the main frame as spurious columns.
- **B14 fan-out guard:** the lookup side is deduplicated on `factors` with `keep="last"` before the join, so a config with two entries for the same factor combination can never fan out (multiply) rows in the output — the last-authored entry wins, matching trace enrichment's own reverse-walk resolution of "the winning row" for the same duplicate-key case.
- **Bug #1/#2 (naming collision):** the joined lookup value is renamed to an internal sentinel (`__haute_lookup_val__`) before merging with the main frame, specifically so a table whose *input* frame happens to already have a column literally named `"value"` cannot collide with the lookup's own `"value"` column.
- **Empty-config no-ops are load-bearing, not incidental:** a banding factor with no `column`/`outputColumn`/`rules`, or a rating table with no `factors`/`entries`/`outputColumn`, is a *documented* passthrough (see Failure model) — both the executor's GUI node builder and the generated-code entry point route through the exact same `_apply_banding_factors`/`_apply_rating_step_outputs` functions, so an empty/incomplete config behaves identically in preview and in a saved standalone script.
- **`normalise_banding_factors` degrades gracefully on a non-list `factors` key**, returning `[]` rather than raising — this differs from the rating-table side, where a non-list `tables` raises `ValueError` (`expand_rating_step_config_from_sidecar`). This asymmetry is intentional but not called out in either module's docstring.
  > NOTE: because `normalise_banding_factors` on a malformed (non-list) `factors` value silently returns an empty list instead of raising, a corrupted banding sidecar can silently execute as a no-op node rather than surfacing a config error — inconsistent with the "fail loud" pattern used everywhere else in this component.

## Error handling

| Condition | Exception | Where raised | Where it surfaces |
|---|---|---|---|
| Rating-table miss, no default, `onMissing: "error"` | `RatingTableMissError` (subclass of `ValueError`) | `_rating_miss_guard_expr._check`, inside `map_batches` | At `.collect()`/materialisation of the lazy plan — propagates up through whichever caller (executor preview, sink write, codegen'd script) triggers execution |
| Non-numeric/non-finite banding rule value or breakpoint boundary | `ValueError` | `_banding_condition`, `_breakpoints_to_rules` | Eagerly, during `_apply_banding` — before any frame materialisation |
| >1 open-ended breakpoint, or a sole open-ended breakpoint with no bounded anchor, or a duplicate breakpoint boundary | `ValueError` | `_breakpoints_to_rules` | Eagerly |
| Rating table entries contain NaN/Infinity `value` | `ValueError` | `_apply_rating_table` | Eagerly, before the join |
| Rating table entries contain a null `value` | `ValueError` | `_apply_rating_table` | Eagerly, before the join |
| Rating factor column has `Date`/`Datetime` dtype | `ValueError` | `_validate_supported_factor_dtypes` (called from `_apply_rating_table`) | Eagerly, before the join |
| Unsupported `onMissing` value | `ValueError` | `_normalise_on_missing` | Eagerly |
| Unsupported combine `operation` | `ValueError` | `_normalise_combine_operation` | Eagerly, from both `_combine_rating_columns` and `_normalise_combined_outputs` |
| `combinedOutputs` item missing/non-finite `baseValue`, missing/duplicate `outputColumn`, or non-list `combinedOutputs` | `ValueError` | `_normalise_combined_outputs` | Eagerly, at config normalisation |
| `ratingStep.factors` not a list, too many factors (>3), a factor not a non-empty string | `ValueError` | `_rating_step_config._validate_factors` | Eagerly, at config expand/compact |
| Rating entry row missing a required factor, or a `value`/`outputColumn` value conflict, or duplicate compact-map key | `ValueError` | `_rating_step_config._normalise_entry_rows` / `_insert_entry_value` / `_entry_value` | Eagerly, at config expand/compact |
| Banding `factors` (or a compact rule map) not structurally valid; duplicate categorical/breakpoint rule key; empty categorical rule key | `ValueError` | `_banding_config.py` various | Eagerly, at config expand/compact |

No exception raised by these helpers is caught and swallowed internally: every
raise propagates to the caller. Non-raising exceptional/config-gap behaviour is
not limited to structured logs: `normalise_banding_factors` turns a non-list `factors` value into an empty
no-op list (see Edge cases). Rating misses/skipped tables and missing ratebook
factor groups use the `rating_table_lookup_misses` /
`rating_table_skipped_incomplete` / `ratebook_entries_missing_factor_group`
WARNING-level logs.

## Testing

Backend tests live under `tests/` (no dedicated subdirectory for this component):

- **`tests/test_rating.py`** (~1840 lines, largest suite) — direct unit coverage of `_rating.py`: banding condition building, `_apply_rating_table` (incl. non-numeric defaults, duplicate entries, extra entry columns, schema-call-count/perf regression, large tables, all-null tables, boundary/negative/extreme float values, special-character factor names), `_combine_rating_columns` (incl. non-numeric columns, edge cases, multiply-with-zero, min/max mixed values), `_apply_banding` edge cases, sequential rating tables, dtype-preservation regressions (B1/B2), empty-string/int-typed factor values, null factor columns, and compact-config rating-step application end to end.
- **`tests/test_banding.py`** (~1240 lines) — continuous/categorical `_apply_banding`, `_build_node_fn` integration, banding decorator parsing and codegen, standalone-execution parity with the executor path, multi-factor banding, hardening/adversarial inputs, and the full `breakpoints` mode (ordering, closures, open-ended boundary).
- **`tests/test_rating_step.py`** (~1300 lines) — `RATING_STEP` executor node building, decorator parsing, codegen, and compact-sidecar round-trip integration.
- **`tests/test_rating_step_config_coverage.py`** — targeted coverage of `_rating_step_config.py` validation helpers: value/factor validation, entry-value conflict resolution, compact/expand round trips (single- and three-factor), and malformed-shape rejections.
- **`tests/test_banding_config_coverage.py`** — targeted coverage of `_banding_config.py`: map-value validation, compact rule map/row conversion, and malformed shape rejections.
- **`tests/test_rating_key_agreement.py`** (~1050 lines) — the dedicated pin for `normalise_rating_key` ⟷ `_rating_key_expr` agreement: engine key normalisation, Float32/cross-dtype agreement, dedup-on-canonical-keys (F084, see above), Decimal factor scale, sidecar key canonicalisation, unsupported temporal factor rejection, ratebook schema collection, and enrichment/end-to-end trace agreement with the engine.
- **`tests/test_rating_miss_fail_loud.py`** — the miss-policy contract: default (`error`) fails loud, no default + no miss stays silent, opt-in `onMissing: "neutral"`, and `_apply_rating_table`'s miss-guard wiring specifically.
- **`tests/test_trace_banding_lineage.py`** — integration tests asserting a banding-created output continues the same lineage chain as other trace-calculated fields (through prior banding, through a computed upstream input, and for breakpoint-matched boundaries).

Strategy is predominantly unit/property-style direct calls into the module functions (not full pipeline runs), with a smaller number of executor-integration and trace-integration tests confirming the shared primitives behave identically across entry points. `test_rating_key_agreement.py` in particular is written as a pinning/regression suite specifically to prevent the Python-mirror and Polars-expression forms of the canonical key from drifting apart, since that would be silently wrong in exactly the misleading way this codebase's error-handling conventions are designed to avoid (a trace agreeing with a join that actually disagreed).

The UI-adjacent config contract is owned by
`frontend/src/__tests__/editors/BandingEditor.test.tsx`, the suites under
`frontend/src/panels/editors/banding/__tests__/`,
`frontend/src/__tests__/editors/RatingStepEditor.test.tsx`, and the suites
under `frontend/src/panels/editors/rating/__tests__/`. They pin the same compact
factor/table shapes, breakpoint closure controls, factor-level ordering, one-/two-/
three-way table editing, combined outputs, and invalid/incomplete status display;
the corresponding production modules remain owned by the frontend editor spec.

The remaining fail-soft top-level banding behaviour is pinned directly:
`tests/test_rating.py::TestNormaliseBandingFactors.test_non_list_returns_empty`
covers the malformed top-level shape. Unknown operators and invalid thresholds
are fail-loud and share runtime/trace contract coverage in
`tests/test_trace_fidelity_contract.py`. The one area flagged as under-tested by
design (rather than by a missing test case) is the F084 dedup-ordering behaviour
in `_apply_rating_table`, which cannot be distinguished by any currently
constructible input (see [Edge cases](#edge-cases-and-invariants)).

## Polars backend contracts (0.6.0)

Remaining rating improvement work is tracked in the
[rating roadmap](../../roadmap/rating.md). Review-P07 has these implementation-level requirements:

- `_combine_rating_columns` / `_combine_rating_output` detect a per-row all-null participant
  set for `min` and `max` and raise `RatingExtremaUndefinedError(ExecutionError)` at eager or
  lazy materialisation. Mixed-null extrema retain Polars' skip-null behaviour. The exception
  carries the combined-output column and operation as stable fields and renders both in its
  message.
- The extrema guard is part of the materialisation transaction: if any row fails, the whole
  batch fails before publication or cache promotion, even when other rows are valid. No
  partial frame, output artifact, or cache entry may become visible. API error translation
  returns HTTP 422; background execution records stable `contract_error` status/code and the
  same output/operation fields.
- After lookup entry values are validated finite, remove `fill_nan`-based neutralisation from `add` and `multiply`; no combine operation may disguise a NaN as `0.0` or `1.0`. Preserve the specified null treatment for an explicit `onMissing: "neutral"` result.
- Before canonicalisation, join construction, or collection, `_apply_rating_table` validates
  each declared factor against the plan's resolved input schema. An absent factor raises
  `RatingFactorMissingError(SchemaMismatchError)` with stable table and factor fields, rather
  than being treated as an incomplete-table skip. API error translation returns HTTP 422 and
  background execution records `contract_error` with those fields.
- `_apply_rating_step_outputs` owns a single schema snapshot for the complete rating/combine plan and passes it to table and combine operations. Code must not call `collect_schema()` once per table or re-resolve schema after a plan-expanding join.
- FR22 may replace or optimise `_rating_miss_guard_expr` only behind a benchmark gate using representative hit-heavy and miss-heavy workloads. The replacement must preserve `RatingTableMissError` type, message fields and display cap, batch-time failure timing under lazy and streaming collection, and `onMissing: "neutral"` warning contents/counts exactly.

Regression tests must pin: all-null `min` and `max` (including configured base-value
semantics), mixed null/non-null extrema, a mixed valid/all-null batch, eager and lazy failure
before publish/cache side effects, exact HTTP/background mappings and stable fields, NaN
propagation/rejection rather than neutralisation, absent one-, two-, and three-factor inputs
before join/collection, schema-resolution call count for multi-table/combine plans, and
baseline-versus-optimised miss-guard error and warning parity. The 0.6 pre-1.0 migration note
documents both newly loud cases and their exception and transport contracts. Non-goals are changing miss
policy, silently supplying absent factors, expanding factor dtype support, or landing FR22
without benchmark evidence.
