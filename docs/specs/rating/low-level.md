# Rating — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_rating.py` | Pure-logic frame transforms: banding rule evaluation (`_apply_banding`, `_banding_condition`, `_breakpoints_to_rules`) and the banding-factor loop (`_apply_banding_factors`), rating-table lookup (`_apply_rating_table`), combining (`_combine_rating_columns`, `_combine_rating_output`), exact dtype descriptor round-tripping plus the canonical factor-key form (`rating_dtype_descriptor`, `rating_dtype_from_descriptor`, `normalise_rating_key`, `_rating_key_expr`), the rating-step loop (`_apply_rating_step_outputs`) and the two generated-code entry points (`apply_banding_from_config`, `apply_rating_step_from_config`). |
| `src/haute/_rating_step_config.py` | Rating-table config normalisation: canonical ordered row-array validation and optional `factorDtypes` descriptor validation/preservation. |
| `src/haute/_banding_config.py` | Banding config normalisation: compact key/value-map ⟷ canonical row-array conversion for `categorical`/`breakpoints` rules (`expand_banding_config_from_sidecar`, `compact_banding_config_for_sidecar`, `normalise_banding_rules`, `normalise_banding_factors`). |

## Key types and data structures

- **Banding factor** (`dict`): `{"banding": "continuous"|"categorical"|"breakpoints", "column": str, "outputColumn": str, "rules": [...] | {...}, "default": Any, "rightClosed"?: bool}`. `rules` is a list of row dicts in the canonical in-memory shape; `categorical`/`breakpoints` sidecars persist it as a `{key: value}` map instead.
  - `continuous` rule row: `{"op1"?, "val1"?, "op2"?, "val2"?, "assignment": str}`.
  - `categorical` rule row: `{"value": str, "assignment": str}` (sidecar map: `{value: assignment}`).
  - `breakpoints` rule row: `{"boundary": str, "label": str}` — empty `boundary` marks the open-ended tail (sidecar map: `{boundary: label}`).
- **Rating table** (`dict`): `{"factors": list[str] (1-3 cols), "factorDtypes"?: dict[str, dtype-descriptor], "outputColumn": str, "entries": list[dict], "defaultValue"?: str|number, "onMissing"?: "error"|"neutral"}`. `entries` is an ordered row array with one JSON scalar per factor plus numeric `"value"`. Invariant: `len(factors) <= _MAX_RATING_FACTORS` (3), enforced in `_rating_step_config._validate_factors`.
- **Combined output** (`dict`): `{"outputColumn": str, "operation": "multiply"|"add"|"min"|"max", "baseValue": float}`.
- **`RatingTableMissError(ValueError)`** — raised at frame materialisation, not at config-build time, by `_apply_rating_miss_guard`'s `map_batches` callback.
- **Rating dtype descriptor** (`dict`): `{"kind": <name>}` where `<name>` is
  exactly one of `Int8`, `Int16`, `Int32`, `Int64`, `Int128`, `UInt8`,
  `UInt16`, `UInt32`, `UInt64`, `Float32`, `Float64`, `Boolean`, `String`,
  `Categorical`, `Enum`, `Decimal`, `Date`, `Datetime`, `Time`, `Duration`,
  or `Null`. Datetime also stores `timeUnit` and nullable `timeZone`;
  Duration stores `timeUnit`; Decimal stores nullable `precision` and integer
  `scale`; Enum stores its ordered string `categories`.
  Optimiser artifacts store `factor_dtypes[table]` as an ordered list of
  `{"column": name, "dtype": descriptor}` records.
- Rating sidecar invariant: canonical row arrays survive JSON encode/decode and
  expand/compact without changing table/factor/entry order or scalar identity.
  Legacy object-key maps cannot meet that invariant and are therefore never
  emitted.

## Control flow

**Banding** — `apply_banding_from_config(lf, config, base_dir=None)`:
1. Resolve `config` (dict, or load a JSON sidecar via `_config_io.load_node_config`).
2. `_normalise_banding_factors(config)` → `_banding_config.normalise_banding_factors` → expands sidecar rule maps to row-array shape via `expand_banding_config_from_sidecar`.
3. `_apply_banding_factors(lf, factors)` loops factors in order, calling `_apply_banding` per factor; each factor's output column is added via `lf.with_columns(...)`, so later factors can already see earlier factors' output columns.
4. Inside `_apply_banding`: `breakpoints` rules are converted to `continuous` rules first (`_breakpoints_to_rules`); float input columns are NaN/Infinity-sanitised to null (`when(is_nan|is_infinite).then(null).otherwise(col)` — built as a *local* expression, never aliased back onto the source column, so it cannot corrupt other nodes' view of that column); then a `pl.when/then` chain is built rule-by-rule (`_banding_condition` consumes the shared continuous-rule parser and turns each usable `op1/val1[,op2/val2]` pair into a boolean expression, ANDed together) and finished with `.otherwise(default)`.
   Operators are resolved through the exported immutable
   `SUPPORTED_BANDING_OPERATORS` contract. An unknown operator raises before a
   `when` branch or output frame is published; trace enrichment imports the
   shared parser and therefore cannot interpret a broader rule set. A non-empty
   authored rule list that produces no usable categorical mapping or continuous
   branch raises `ValueError` rather than returning the input frame unchanged.
5. `categorical` bypasses the when/then chain entirely and uses `col.cast(Utf8).replace_strict(remap, default=...)`.

**Rating** — `apply_rating_step_from_config(lf, config, base_dir=None)`:
1. Resolve `config` (dict or sidecar path), same pattern as banding.
2. `normalise_rating_tables(config)` (from `_rating_step_config.py`) validates
   canonical row arrays and rejects duplicate non-empty table output columns,
   naming the later table index and duplicated column.
3. `_normalise_combined_outputs(config)` validates `combinedOutputs`.
4. `_apply_rating_step_outputs(lf, tables, combined_outputs)`:
   a. Coerce `pl.DataFrame` input to `.lazy()`.
   b. Collect the frame schema **once** up front into a local `dict`, then thread it through every table call (`input_schema=schema`) instead of re-collecting after each table — this keeps schema resolution `O(n)` instead of `O(n²)` in the number of tables, since each `_apply_rating_table` call would otherwise re-run `collect_schema()` on a lazy plan that has grown by one join.
   c. For each table: call `_apply_rating_table`; if it actually materialised an output column (`_rating_table_skip_reason(table) is None`), register the output column name and its `Float64` dtype in the local schema dict for subsequent tables/combines to see; otherwise log `rating_table_skipped_incomplete` at WARNING with the specific skip reason and omit it from combining.
   d. For each combined output: call `_combine_rating_output(lf, out_cols, operation, output_col, base_value)`.
5. `_apply_rating_table(lf, table, input_schema=...)` (per table):
   a. Return `lf` unchanged if `factors` or `outputColumn` is empty. Otherwise
      validate every declared factor against the once-resolved input schema
      before treating empty/incomplete entries as a documented no-op.
   b. Return unchanged for an empty `entries` list only after that factor
      validation; then parse `defaultValue`: tolerate non-numeric/non-finite
      strings (treated as "no usable default", noted in the eventual miss error
      rather than silently ignored).
   c. Build a `pl.DataFrame(entries)`; return unchanged if it has no `"value"` column; reject (raise `ValueError`) any NaN/Infinity or null `value` entries.
   d. Select only `[*factors, "value"]` from entries (drops any extra keys the sidecar/GUI may have left in an entry dict); return unchanged if any factor is absent from the entries' columns.
   e. Resolve and validate each originating input dtype. Coerce the corresponding
      lookup-entry column through that dtype, then evaluate `_rating_key_expr`
      into a collision-free temporary key column on both lookup and input sides.
   f. Deduplicate the lookup on the temporary canonical keys with
      `keep="last"` and rename `value` to a collision-free internal value name.
   g. Left join on the temporary keys (`how="left", maintain_order="left"`).
      Source factor columns are untouched throughout.
   h. If no usable default, wire in `_apply_rating_miss_guard` as a lazy-frame
      batch barrier over the temporary keys and internal value. Projection,
      predicate, and slice pushdown stop at the barrier, so validation cannot be
      pruned when a caller selects a different output column; diagnostics still
      show the exact keys used by the join.
   i. Materialise/fill `outputColumn`, then drop every temporary key/value column.
6. `_apply_rating_miss_guard` wraps the joined lazy frame in
   `map_batches(_check, projection_pushdown=False, predicate_pushdown=False,
   slice_pushdown=False, streamable=True)`. The callback returns each batch
   unchanged after validating misses and relabels temporary keys with the public
   factor names in diagnostics.
7. `_combine_rating_output` (per combined output): if `baseValue` is `None`, delegates straight to `_combine_rating_columns`; otherwise adds a uniquely-named literal column (`__haute_rating_base_{output}__`, prefixed with more `_` until it doesn't collide with an existing column or the table-output list) holding `baseValue`, prepends it to the columns list, combines, then drops the scratch column.
8. `_combine_rating_columns`: first reject duplicate participant column names
   and an output name that would overwrite one of its participants.
   Single-column input is a plain alias/rename (no arithmetic). Multi-column:
   `add` folds nulls to `0.0` per column before summing; `multiply` folds nulls
   to `1.0` before multiplying; NaN is never neutralised. `min`/`max` use
   `pl.min_horizontal`/`pl.max_horizontal` (which skip nulls natively) and wrap
   the output expression in a materialisation-time all-null guard.

## Edge cases and invariants

- **Typed-key dedup ordering (F084):** lookup entry scalars are first coerced
  through the originating factor dtype, then canonical keys are generated, and
  only then does `lookup.unique(..., keep="last")` run. Representational aliases
  in that dtype (for example Float64 entry strings `"25.0"` and `"25.00"`)
  therefore form one duplicate group; the last authored row wins without
  fanning out the input.
- **Float width is part of the key contract:** `_rating_key_expr` formats a
  Float32 in Float32 and a Float64 in Float64. The Python mirror reconstructs
  the scalar through the required originating dtype and applies the same
  coercion/formatting rules, so JSON/Python widening cannot change the trace
  key. `normalise_rating_key` has no dtype-inference default; trace,
  persistence, and optimiser helpers must resolve and pass a concrete dtype
  before calling it.
- **Scalar mirror cost is bounded:** `normalise_rating_key` may use eager
  Polars Series coercion/string formatting to retain exact engine semantics,
  but must not construct a one-row DataFrame and evaluate multiple expressions
  per scalar. A representative performance regression compares it with that
  historical DataFrame-expression reference while the shared dtype matrix
  remains the semantic oracle.
- **Int-like float collapse is range-bounded:** `normalise_rating_key`/`_rating_key_expr` only collapse a finite int-like float to its integer digit string when it is inside `[-(2**63), 2**63)` (the `Int64` range), because the cast to `Int64` on the engine side (`strict=False`) is only exact/lossless there. Outside that range (e.g. `1e300`), both sides fall through to Polars' `Utf8` cast instead, deliberately consistently.
- **`Decimal` factor columns** are strictly cast through the column's declared
  precision/scale before key generation. Equivalent authored forms such as
  `"25.5"` and `"25.50"` therefore become the same scale-2 value and key;
  values outside the declared decimal contract fail during lookup construction.
- **String factor levels never collapse:** a String value that happens to spell
  an int-like float (for example `"25.0"`) remains that exact label. A numeric
  `25.0` is distinct in the canonical row-array sidecar and is coerced through
  the runtime factor dtype before key generation.
- **Temporal keys are supported:** Date, Datetime (including unit/timezone),
  Time, and Duration use Polars' declared-dtype string form. Lookup entries are
  cast through that dtype first, so an ISO sidecar scalar and an input temporal
  scalar agree or fail loudly during the cast.
- **Ratebook dtype metadata is mandatory:** `factor_dtypes` is part of every
  newly saved ratebook artifact. `_apply_ratebook` validates ordered factor
  names and exact descriptors before calling `_apply_rating_table`; it neither
  guesses for legacy artifacts nor coerces an apply column to the saved dtype.
- **Duplicate breakpoint boundaries / multiple open-ended breakpoints / a sole open-ended breakpoint with no bounded anchor** all raise `ValueError` in `_breakpoints_to_rules` rather than silently producing an empty interval or dropping data (see high-level Failure model).
- **Unknown continuous-band operators fail loudly:** `_banding_condition`
  raises for any operator absent from `SUPPORTED_BANDING_OPERATORS`; neither
  eager/lazy execution nor trace enrichment may silently skip or reinterpret it.
- **Authored banding rules must materialise a branch:** an empty rule list is a
  representable draft/no-op, but a non-empty list whose continuous rules have no
  usable operator/value pair or whose categorical rules have no usable
  value/assignment pair raises `ValueError` naming the output column.
- **Rating output columns are globally unique within one step.**
  `_rating_step_config` rejects duplicate `tables[].outputColumn` values before
  execution, `_normalise_combined_outputs` rejects collisions with table or
  combined outputs, and `_combine_rating_columns` rejects duplicate
  participants or an output name that would overwrite a participant.
- **B15 entry-column pollution guard:** `_apply_rating_table` selects only `[*factors, "value"]` from the entries `DataFrame` before joining, so stray extra keys left in an entry dict (e.g. leftover UI state) never leak into the main frame as spurious columns.
- **B14 fan-out guard:** the lookup side is deduplicated on its final typed temporary keys with `keep="last"` before the join, so aliases in the originating factor dtype can never fan out (multiply) rows in the output — the last-authored entry wins, matching trace enrichment's own reverse-walk resolution of "the winning row" for the same duplicate-key case.
- **Bug #1/#2 (naming collision):** lookup keys and values use internal names reserved against every input, entry, and output column (starting from `__haute_rating_key_{n}__` and `__haute_lookup_val__`, then prefixing `_` until free), so user columns named `"value"` or like an internal stem remain untouched.
- **Empty-config no-ops are load-bearing, not incidental:** a banding factor with no `column`/`outputColumn`/`rules`, or a rating table with no `factors`/`entries`/`outputColumn`, is a *documented* passthrough (see Failure model) — both the executor's GUI node builder and the generated-code entry point route through the exact same `_apply_banding_factors`/`_apply_rating_step_outputs` functions, so an empty/incomplete config behaves identically in preview and in a saved standalone script.
- **`normalise_banding_factors` degrades gracefully on a non-list `factors` key**, returning `[]` rather than raising — this differs from the rating-table side, where a non-list `tables` raises `ValueError` (`normalise_rating_step_config`). This asymmetry is intentional but not called out in either module's docstring.
  > NOTE: because `normalise_banding_factors` on a malformed (non-list) `factors` value silently returns an empty list instead of raising, a corrupted banding sidecar can silently execute as a no-op node rather than surfacing a config error — inconsistent with the "fail loud" pattern used everywhere else in this component.

## Error handling

| Condition | Exception | Where raised | Where it surfaces |
|---|---|---|---|
| Rating-table miss, no default, `onMissing: "error"` | `RatingTableMissError` (subclass of `ValueError`) | `_apply_rating_miss_guard._check`, inside `map_batches` | At `.collect()`/materialisation of the lazy plan — propagates up through whichever caller (executor preview, sink write, codegen'd script) triggers execution |
| Non-numeric/non-finite banding rule value or breakpoint boundary | `ValueError` | `_banding_condition`, `_breakpoints_to_rules` | Eagerly, during `_apply_banding` — before any frame materialisation |
| >1 open-ended breakpoint, or a sole open-ended breakpoint with no bounded anchor, or a duplicate breakpoint boundary | `ValueError` | `_breakpoints_to_rules` | Eagerly |
| Rating table entries contain NaN/Infinity `value` | `ValueError` | `_apply_rating_table` | Eagerly, before the join |
| Rating table entries contain a null `value` | `ValueError` | `_apply_rating_table` | Eagerly, before the join |
| Rating factor has an unsupported nested/binary/object/unknown dtype | `ValueError` naming the table/factor/dtype, supported scalar families, and upstream-cast remediation | rating dtype validation in `_apply_rating_table` | Eagerly, before the join |
| Saved ratebook lacks factor dtype metadata or apply dtype differs | `RatingFactorDtypeContractError` (`SchemaMismatchError`) | `_apply_ratebook` | Eagerly, before lookup construction; public contract adapters map it to HTTP 422/background `contract_error` |
| Unsupported `onMissing` value | `ValueError` | `_normalise_on_missing` | Eagerly |
| Unsupported combine `operation` | `ValueError` | `_normalise_combine_operation` | Eagerly, from both `_combine_rating_columns` and `_normalise_combined_outputs` |
| Duplicate non-empty `tables[].outputColumn` | `ValueError` naming the later table index and column | `_rating_step_config` output validation | Eagerly, at config normalisation |
| Duplicate participant column, or a combined output that would overwrite one of its participant columns | `ValueError` | `_combine_rating_columns` | Eagerly, before constructing arithmetic expressions |
| `combinedOutputs` item missing/non-finite `baseValue`, missing/duplicate `outputColumn`, or non-list `combinedOutputs` | `ValueError` | `_normalise_combined_outputs` | Eagerly, at config normalisation |
| `ratingStep.factors` not a list, too many factors (>3), a factor not a non-empty string, or a duplicate factor | `ValueError` | `_rating_step_config._validate_factors` | Eagerly, at config expand/compact |
| Rating entry row missing a required factor, has a non-JSON factor scalar, or lacks literal `value` | `ValueError` | `_rating_step_config` normalisation helpers | Eagerly, at config validation |
| Banding `factors` (or a compact rule map) not structurally valid; duplicate categorical/breakpoint rule key; empty categorical rule key | `ValueError` | `_banding_config.py` various | Eagerly, at config expand/compact |
| Non-empty banding rule list has no usable mapping/condition and assignment | `ValueError` | `_apply_banding` | Eagerly, before publishing an output expression |
| Every participating `min`/`max` value is null for any row | `RatingExtremaUndefinedError` (`ExecutionError`) with output/operation fields | `_rating_extrema_expr`, at materialisation | Public adapters map to HTTP 422 or background `contract_error`; the batch publishes no partial output |
| Declared rating factor absent from the input schema, including an entry-less table | `RatingFactorMissingError` (`SchemaMismatchError`) with table/factor fields | `_apply_rating_table`, before lookup construction | Public adapters map to HTTP 422 or background `contract_error` |

No exception raised by these helpers is caught and swallowed internally: every
raise propagates to the caller. Non-raising exceptional/config-gap behaviour is
not limited to structured logs: `normalise_banding_factors` turns a non-list `factors` value into an empty
no-op list (see Edge cases). Rating misses and skipped tables use the
`rating_table_lookup_misses` / `rating_table_skipped_incomplete` WARNING-level logs.

## Testing

Backend tests live under `tests/` (no dedicated subdirectory for this component):

- **`tests/test_rating.py`** (~1840 lines, largest suite) — direct unit coverage of `_rating.py`: banding condition building, `_apply_rating_table` (incl. non-numeric defaults, duplicate entries, extra entry columns, schema-call-count/perf regression, large tables, all-null tables, boundary/negative/extreme float values, special-character factor names), `_combine_rating_columns` (incl. non-numeric columns, edge cases, multiply-with-zero, min/max mixed values), `_apply_banding` edge cases, sequential rating tables, dtype-preservation regressions (B1/B2), empty-string/int-typed factor values, null factor columns, and canonical row-array rating-step application end to end.
- **`tests/test_banding.py`** (~1240 lines) — continuous/categorical `_apply_banding`, `_build_node_fn` integration, banding decorator parsing and codegen, standalone-execution parity with the executor path, multi-factor banding, hardening/adversarial inputs, and the full `breakpoints` mode (ordering, closures, open-ended boundary).
- **`tests/test_rating_step.py`** (~1300 lines) — `RATING_STEP` executor node building, decorator parsing, codegen, and canonical-sidecar round-trip integration.
- **`tests/test_rating_step_config_coverage.py`** — targeted and property coverage
  of `_rating_step_config.py`: every zero/one/two/three-factor accepted canonical
  shape, scalar identity and metadata/order preservation, JSON round trips, and
  malformed-shape rejection.
- **`tests/test_banding_config_coverage.py`** — targeted coverage of `_banding_config.py`: map-value validation, compact rule map/row conversion, and malformed shape rejections.
- **`tests/test_rating_dtype_contract.py`** and
  **`tests/fixtures/rating_key_cases.py`** — the shared real-Polars matrix for
  `normalise_rating_key(value, dtype)` and `_rating_key_expr` across every
  supported primitive/categorical/decimal/temporal dtype, runtime lookup,
  trace enrichment, ratebook persistence/apply, null/non-finite values,
  malformed metadata, and exact dtype mismatch.
- **`tests/test_rating_key_agreement.py`** — focused historical and
  adversarial key regressions, including typed alias deduplication and source
  factor-column preservation.
- **`tests/test_optimiser_ratebook_apply_agreement.py`** — engine/explainer and
  real-solver save/apply agreement, including composite factors and
  price-contour-emitted level canonicalisation.
- **`tests/performance/test_rating_miss_guard_perf.py`** — representative
  miss-guard workload/evidence matrix and semantic oracle. It records timings
  but imposes no hardware-specific pass/fail latency threshold.
- **`tests/performance/test_rating_key_normalisation_perf.py`** — representative
  scalar-key benchmark comparing the production Series path with the historical
  one-row DataFrame-expression reference, with the shared dtype matrix checked
  for semantic equivalence before timings are accepted.
- **`tests/test_rating_miss_fail_loud.py`** — the miss-policy contract: default (`error`) fails loud, no default + no miss stays silent, opt-in `onMissing: "neutral"`, and `_apply_rating_table`'s miss-guard wiring specifically.
- **`tests/test_trace_banding_lineage.py`** — integration tests asserting a banding-created output continues the same lineage chain as other trace-calculated fields (through prior banding, through a computed upstream input, and for breakpoint-matched boundaries).

Strategy is predominantly unit/property-style direct calls into the module functions (not full pipeline runs), with a smaller number of executor-integration and trace-integration tests confirming the shared primitives behave identically across entry points. `test_rating_key_agreement.py` in particular is written as a pinning/regression suite specifically to prevent the Python-mirror and Polars-expression forms of the canonical key from drifting apart, since that would be silently wrong in exactly the misleading way this codebase's error-handling conventions are designed to avoid (a trace agreeing with a join that actually disagreed).

The UI-adjacent config contract is owned by
`frontend/src/__tests__/editors/BandingEditor.test.tsx`, the suites under
`frontend/src/panels/editors/banding/__tests__/`,
`frontend/src/__tests__/editors/RatingStepEditor.test.tsx`, and the suites
under `frontend/src/panels/editors/rating/__tests__/`. They pin the same canonical
factor/table shapes, breakpoint closure controls, factor-level ordering, one-/two-/
three-way table editing, combined outputs, and invalid/incomplete status display;
the corresponding production modules remain owned by the frontend editor spec.

The remaining fail-soft top-level banding behaviour is pinned directly:
`tests/test_rating.py::TestNormaliseBandingFactors.test_non_list_returns_empty`
covers the malformed top-level shape. Unknown operators and invalid thresholds
are fail-loud and share runtime/trace contract coverage in
`tests/test_trace_fidelity_contract.py`. F084 ordering is observable and pinned:
the lookup first coerces `"25.0"` and `"25.00"` through a Float64 factor dtype,
then deduplicates their identical final key with `keep="last"`.
