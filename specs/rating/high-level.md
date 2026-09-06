# Rating — High-Level Specification

## Purpose

Actuarial pricing pipelines need two related but distinct transforms on a Polars frame:
**banding** (discretising a continuous column into ranges, or grouping a categorical
column into fewer levels) and **rating** (looking up one or more table-driven
multiplicative/additive factors keyed on those bands, and combining them into a
technical premium). This component is the pure-logic core for both — it takes a
frame and a JSON-shaped config and returns the frame with new columns, with no
dependency on the execution graph, the GUI, or trace machinery. It exists so that
banding and rating behave identically whether they run inside the interactive
GUI executor, a saved standalone `pipeline.run()` script, or the optimiser's
what-if scoring path.

## Scope

In scope:

- Continuous (operator/threshold), categorical (value remap), and breakpoints
  (ordered boundary list) banding rule evaluation.
- One-, two-, and three-factor rating-table lookups, including default-value
  fill and a loud/quiet miss policy.
- Combining multiple rating-table outputs with a required fixed finite numeric
  base value using `multiply` / `add` / `min` / `max`.
- Config normalisation: rating-table entries persist only in one canonical
  ordered row-array shape. Categorical and breakpoints banding rules retain
  their compact key/value-map sidecar shape.
- The dtype-faithful canonical string form of a rating-table factor key
  (`normalise_rating_key(value, dtype)`) and its Polars-expression twin, shared
  with trace enrichment and the optimiser's ratebook save/apply path so every
  consumer agrees on what a lookup match is. The originating dtype argument is
  mandatory: a Python scalar is never treated as sufficient evidence of a
  Float32, categorical, decimal, or temporal source dtype.

Out of scope (owned by neighbouring components):

- Turning a graph node into a callable pipeline step — see
  [execution-engine](../execution-engine/high-level.md) (`_builders._build_banding`,
  `_build_rating_step`).
- Decorator parsing (`@pipeline.banding(...)`, `@pipeline.rating_step(...)`) and
  generated-code emission — see [pipeline-config](../pipeline-config/high-level.md)
  and [codegen](../codegen/high-level.md).
- Rendering rating/banding lookups as trace detail payloads — see
  [tracing](../tracing/high-level.md) (`_trace_enrichment.enrich_rating_step`,
  `enrich_banding`).
- Sidecar file layout and path resolution for banding and rating-step config
  files (`config/banding/*.json`, `config/rating_step/*.json`, resolved via
  `_config_io.py`) — see [pipeline-config](../pipeline-config/high-level.md).
- Applying a saved optimiser ratebook as a rating-table lookup — owned by the
  optimiser (`_builders._apply_ratebook`), which is a *consumer* of this
  component's `_apply_rating_table` / `_combine_rating_columns` primitives, not
  part of this component.

## Behaviour

**Banding** (`apply_banding_from_config` / `_apply_banding_factors`):

- Each banding "factor" reads one input column and writes one output column.
- `continuous` rules define a range via up to two operator/value pairs
  (`op1`/`val1`, `op2`/`val2` — one of `< <= > >= = ==`); rules are evaluated
  in order and the first matching rule wins (`when/then` chain semantics), the
  rest fall to an explicit `default`.
  An unrecognised operator is rejected before a banding expression is
  published. Runtime banding and trace enrichment consume the same rule
  eligibility parser as well as the same immutable supported-operator
  contract, so a trace cannot credit a rule the engine skipped.
- `categorical` rules are an exact-match remap from input value to assignment.
- `breakpoints` rules are converted internally into `continuous` rules: an
  ordered list of numeric boundaries with labels, closed on the right by default
  (`(lower, upper]`) or on the left when `rightClosed: false`. At most one
  boundary may be open-ended (empty), and it anchors the final unbounded range.
- Float columns have NaN/Infinity sanitised to null before rule matching, so
  they always fall to the default rather than matching an arbitrary rule.
- A draft factor with no column, no output column, or no rules is a documented
  no-op (the frame passes through unchanged for that factor). Once a non-empty
  rule list is authored, at least one rule must have a usable key/condition and
  assignment; an all-unusable rule set is rejected rather than silently
  omitting the configured output column.

**Rating** (`apply_rating_step_from_config` / `_apply_rating_step_outputs`):

- Each table declares one to three `factors` (input column names), an
  `outputColumn`, and `entries` (rows of factor values plus a numeric `value`).
  A left join matches input rows to table entries on the canonicalised factor
  key (see below).
- A **miss** — an input row whose factor combination has no matching entry —
  is resolved by, in order of precedence: (1) a usable numeric `defaultValue`
  fills every miss silently; (2) `onMissing: "neutral"` leaves the output
  null and logs each materialised batch containing misses at WARNING
  (`multiply`/`add` replace that null with their neutral element during a
  multi-column combine; `min`/`max` skip it when another value exists);
  (3) the default policy, `onMissing: "error"`, raises
  `RatingTableMissError` naming the table, the missing key(s), and the
  affected row count.
- Table lookup keys are compared as strings through the factor column's
  originating Polars dtype. Table-entry values are first coerced through that
  dtype, then both the entry and input values use the same Polars expression:
  booleans become `"true"`/`"false"`; finite int-like floats inside the Int64
  range collapse to their integer digit string (`25.0` → `"25"`); other floats
  use the originating width's own Polars string form; and exact, categorical,
  string, decimal, and temporal values use Polars' cast for their declared
  dtype. Null stays null and therefore never matches. Supported factor dtypes
  are Float32/64, signed and unsigned integers, Boolean, String,
  Categorical/Enum, Decimal, Date, Datetime, Time, Duration, and Null.
- Lookup keys are materialised into collision-free temporary columns for the
  join. The source factor columns are never overwritten and therefore never
  need a lossy string-to-original-dtype revert.
- Multiple entries sharing the same factor key keep the *last* one (matches
  the intent of "later edits win" in the entry list).
- After the table stage, `combinedOutputs` combine every materialised table
  output column of the step with a required fixed finite numeric `baseValue`
  using `multiply`, `add`, `min`, or `max`. A table that never materialised its
  output column (because it was incomplete — see Failure model) is omitted from
  combining, not silently referenced as null.
- Every table output column and combined-output column is unique across the
  rating step. Duplicate `tables[].outputColumn` values are rejected with the
  later table index and duplicated column named; the runtime combine boundary
  independently rejects duplicate participant columns so a bypassed
  normaliser cannot square or double-count a surviving factor.
- A rating table or combined-output definition with a structural problem
  (unsupported operation, non-finite base value, duplicate output column,
  NaN/Infinity/null entry values) fails loudly at config-normalisation or
  materialisation time rather than being silently coerced.

## Design rationale

- **Two frame-transform primitives, one execution semantics.** Both banding
  and rating are pure `frame -> frame` functions over declarative JSON config,
  independent of *how* the config was authored (GUI node vs. hand-written
  `pipeline.banding(...)` decorator) or *where* it executes (interactive
  preview vs. a saved standalone script). `apply_banding_from_config` /
  `apply_rating_step_from_config` are the generated-code twins of the
  executor's node builders specifically so a saved pipeline file reproduces
  GUI preview behaviour exactly — see
  [execution-engine](../execution-engine/high-level.md).
- **Fail loud on ambiguous or silently-lossy config**, per project convention:
  a rating-table miss with no default raises rather than quietly rating at a
  neutral factor; NaN/Infinity/null table entries raise rather than
  corrupting downstream pricing arithmetic; more than one open-ended
  breakpoint raises rather than silently keeping only the last; duplicate
  breakpoint boundaries raise rather than producing an empty interval.
  `onMissing: "neutral"` is the explicit rating-miss opt-out, and it logs
  each materialised batch containing misses. Banding rejects unknown operators
  and any non-list top-level `factors` value. Rating rejects non-list factor or
  entry containers and any populated entry list without declared factors at
  both config and execution boundaries. Missing, `null`, or genuinely empty
  collections remain the intentional draft/no-op forms described below.
- **One dtype-faithful key form, shared everywhere.**
  `normalise_rating_key(value, dtype)` evaluates the same `_rating_key_expr`
  contract that the frame lookup uses, but does so with an eager scalar/Series
  path rather than constructing and evaluating a one-row DataFrame expression
  for every value. Every caller must supply the originating dtype; there is no
  inference default. Trace enrichment supplies the exact consumed parent-frame
  dtype. Saved ratebook artifacts carry an ordered `factor_dtypes` descriptor
  for every factor table, and optimiser save/apply rejects missing metadata or
  a save/apply dtype mismatch before constructing a lookup. This prevents a
  dtype boundary from becoming an accepted neutral miss. Agreement is pinned by
  one real-Polars fixture matrix shared by runtime, trace, and optimiser tests.
- **Lossless canonical rating sidecars.** Rating-table entries persist as
  ordered row arrays rather than JSON object-key maps. JSON object keys erase
  the distinction between values such as numeric `25.0` and the string label
  `"25.0"`, so nested maps are not a lossless canonical format. Table order,
  factor order, entry order,
  scalar identity, outputs, defaults, miss policy, and optional
  `factorDtypes` metadata survive a canonical save/load round trip.
- **Deduplicate before joining, not after.** A rating table's factor keys are
  deduplicated (`keep="last"`) before the join, so a left join can never fan
  out rows even if the config accidentally has two entries for the same
  factor combination.
- **One banding node holds many factors, rather than one node per factor or
  separate continuous/categorical node types.** A single pipeline step often
  bands several columns at once (age, vehicle age, property type, ...);
  one-node-per-factor and separate node types per banding kind were both
  rejected for the same graph-clutter reason as the rating-table case below.
  Rules are also kept inline in the sidecar/decorator rather than loaded from
  a CSV file, so the GUI can edit them directly and they stay
  version-controlled alongside the rest of the pipeline rather than in a
  separate artifact.
- **One rating-step node holds many tables, rather than one node per table.**
  A production rating structure commonly needs a dozen or more factor
  tables; a one-node-per-table layout was rejected because it would clutter
  the graph canvas at that scale. Keeping related tables inside a single
  `rating_step` node's `tables` list keeps the graph navigable, and is why
  the frontend rating editor (see
  [frontend-node-editors](../frontend-node-editors/low-level.md)) has its
  own search/filter/status UI for a single node's table set rather than
  relying on the canvas to organise many small nodes.

## Interactions

- **[execution-engine](../execution-engine/high-level.md)** — `_builders.py`
  registers `BANDING` and `RATING_STEP` node builders that call
  `_apply_banding_factors` and `_apply_rating_step_outputs` directly, and
  `_apply_ratebook` (optimiser scoring) reuses `_apply_rating_table` and
  `_combine_rating_columns` to apply a saved ratebook as a rating lookup.
- **[pipeline-config](../pipeline-config/high-level.md)** — `_config_io.py`
  routes `BANDING`/`RATING_STEP` sidecar JSON through
  `expand_banding_config_from_sidecar` and `normalise_rating_step_config`
  on load. Rating-step sidecars use the same canonical normaliser on save.
- **[codegen](../codegen/high-level.md)** — emits `apply_banding_from_config(...)`
  / `apply_rating_step_from_config(...)` calls into generated standalone
  pipeline scripts, and `_code_extraction.py` locates the boundary between
  the generated table/combine scaffold and any user-authored post-processing
  code in a rating step.
- **[tracing](../tracing/high-level.md)** — `_trace_enrichment.py` imports
  `normalise_rating_key` and `normalise_rating_tables`/`normalise_banding_factors`
  to build the structured `rating_step`/`banding` trace detail payloads shown
  in the Calculation and Nodes tabs. Rating enrichment also receives the exact
  factor dtypes from the consumed parent frame; it does not reimplement lookup
  or rule matching.
- **modelling / optimiser** — the optimiser's ratebook-apply path is a
  downstream consumer, not a peer: it constructs synthetic rating-table specs
  from a saved artifact's factor tables and ordered `factor_dtypes` descriptors,
  verifies those descriptors against the apply frame, and feeds the tables
  through the same `_apply_rating_table` primitive so optimised relativities are
  applied with identical join/miss semantics to any other rating table.

## Failure model

- **Rating-table miss, no usable default, `onMissing: "error"` (default):**
  raises `RatingTableMissError` at frame materialisation (not at config-build
  time — it is wired into the lazy plan via `map_batches` so it fires exactly
  when the plan runs, batch by batch under streaming). The message names the
  table, up to 10 distinct missing keys, and that batch's row/miss counts.
- **Rating-table miss, `onMissing: "neutral"`:** no exception; each batch with
  misses is logged at WARNING with table, output column, miss count, and missing keys.
  The output stays null for those rows (multiply/add fold it to the operation's
  neutral element downstream).
- **Malformed table entries** (non-finite banding rule value/boundary, NaN/Infinity
  or null rating entry `value`, more than one open-ended breakpoint, an
  open-ended breakpoint with no bounded anchor, a duplicate breakpoint
  boundary, an unsupported combine operation, a non-finite/missing
  `combinedOutputs[].baseValue`, or a duplicate table/combined output column):
  raise `ValueError` eagerly, before the frame is touched.
- **All-null `min`/`max` participants:** raises
  `RatingExtremaUndefinedError(ExecutionError)` at materialisation, naming the
  combined-output column and operation. One invalid row fails the whole eager
  or lazy batch before publication or cache promotion. Public transport maps
  it to HTTP 422 and background `contract_error`.
- **Declared input factor absent from the frame:** raises
  `RatingFactorMissingError(SchemaMismatchError)` before lookup construction,
  even when the table has no entries or otherwise qualifies as an incomplete
  draft. The error carries stable table/factor fields and maps to HTTP 422 or
  background `contract_error`.
- **Unsupported factor dtype** (nested/container, binary, object, or unknown
  dtype): raises `ValueError` naming the table, factor, and dtype before lookup
  construction. The message also names the supported scalar dtype families and
  tells the user to cast the factor upstream.
- **Saved ratebook dtype contract missing or mismatched:** raises
  `RatingFactorDtypeContractError(SchemaMismatchError)` before lookup
  construction, identifying the table, factor, saved descriptor (or its
  absence), and apply dtype. Legacy ratebook artifacts without
  `factor_dtypes` must be re-solved and re-saved; they are not applied with
  guesswork.
- **Incomplete table or factor** (a completely empty rating-table draft, an
  empty entry list, no output column, or an entirely empty banding rule list):
  this is a *documented no-op*, not a failure — the frame passes through
  unchanged for that table/factor. An entry list may omit factors only while it
  is empty; a populated entry list with no declared factors is malformed and
  raises `ValueError`. When a rating table has an
  `outputColumn` configured but is otherwise incomplete, the skip is logged at
  WARNING (`rating_table_skipped_incomplete`) so the gap is observable instead
  of a silently-missing column reaching a combined output.
  A non-empty banding rule list whose rules are all unusable is malformed and
  raises instead of taking this no-op path.
- **Malformed collection shape:** a non-list banding `factors` value, a
  non-list rating-table `factors` or `entries` value, or populated rating
  entries without a non-empty factor list raises `ValueError` before the frame
  is touched. Executor-built nodes and generated standalone pipeline code use
  the same normalisation contract.
