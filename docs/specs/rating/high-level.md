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
- Combining multiple rating-table outputs (and an optional numeric base value)
  with `multiply` / `add` / `min` / `max`.
- Config normalisation: expanding compact JSON-sidecar shapes (nested factor-value
  maps for rating tables, key/value maps for categorical and breakpoints banding
  rules) into the canonical row-array shape used at execution time, and compacting
  the reverse direction for persistence.
- The canonical string form of a rating-table factor key (`normalise_rating_key`)
  and its Polars-expression twin, shared with trace enrichment and the optimiser's
  ratebook apply path so every consumer agrees on what a lookup match is.

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
  > NOTE: an unrecognised operator is currently ignored rather than rejected.
  > A rule with no remaining valid operator/value pair contributes no branch;
  > if every rule is skipped, the factor is a pass-through and does not create
  > its configured output column.
- `categorical` rules are an exact-match remap from input value to assignment.
- `breakpoints` rules are converted internally into `continuous` rules: an
  ordered list of numeric boundaries with labels, closed on the right by default
  (`(lower, upper]`) or on the left when `rightClosed: false`. At most one
  boundary may be open-ended (empty), and it anchors the final unbounded range.
- Float columns have NaN/Infinity sanitised to null before rule matching, so
  they always fall to the default rather than matching an arbitrary rule.
- A factor with no column, no output column, or no rules is a documented no-op
  (the frame passes through unchanged for that factor).

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
- Table lookup keys are compared as strings via a canonical form: booleans
  become `"true"`/`"false"`, finite int-like floats inside the Int64 range
  collapse to their integer digit string (`25.0` → `"25"`), other floats use
  Polars' own float→string cast, and everything else is `str(value)`. This
  lets a numeric factor column match string-keyed table entries deterministically.
  `Date`/`Datetime` factor columns are rejected outright — not supported.
- Multiple entries sharing the same factor key keep the *last* one (matches
  the intent of "later edits win" in the entry list).
- After the table stage, `combinedOutputs` combine named table output columns
  (optionally with a fixed numeric `baseValue`) using `multiply`, `add`,
  `min`, or `max`. A table that never materialised its output column (because
  it was incomplete — see Failure model) is omitted from combining, not
  silently referenced as null.
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
  each materialised batch containing misses. Banding has two separate
  fail-soft behaviours documented above/below: unknown operators are ignored,
  and a malformed non-list top-level `factors` value normalises to an empty
  no-op list.
- **One canonical key form, shared everywhere.** `normalise_rating_key` (the
  Python mirror) and `_rating_key_expr` (its Polars-expression twin, applied
  to both join sides) are the single source of truth for "does this input
  value match this table entry?" Trace enrichment and the optimiser's
  ratebook-apply path import the same function rather than reimplementing key
  comparison, so a trace's matched/default flag can never disagree with what
  the actual join did. Agreement between the two forms is pinned by a
  dedicated regression suite, `tests/test_rating_key_agreement.py` — see
  [Testing](low-level.md#testing).
- **Compact sidecar shapes for human-editable JSON.** Rating-table entries
  persist as nested factor→factor→value maps (not flat row arrays) so a
  hand-edited sidecar reads like a lookup table; categorical/breakpoints
  banding rules persist as flat key→value maps for the same reason. Both
  directions (`expand_*_from_sidecar` / `compact_*_for_sidecar`) are
  round-trip symmetric — see [Edge cases](low-level.md#edge-cases-and-invariants).
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
  `expand_banding_config_from_sidecar` / `expand_rating_step_config_from_sidecar`
  on load and the `compact_*_for_sidecar` counterparts on save.
- **[codegen](../codegen/high-level.md)** — emits `apply_banding_from_config(...)`
  / `apply_rating_step_from_config(...)` calls into generated standalone
  pipeline scripts, and `_code_extraction.py` locates the boundary between
  the generated table/combine scaffold and any user-authored post-processing
  code in a rating step.
- **[tracing](../tracing/high-level.md)** — `_trace_enrichment.py` imports
  `normalise_rating_key` and `normalise_rating_tables`/`normalise_banding_factors`
  to build the structured `rating_step`/`banding` trace detail payloads shown
  in the Calculation and Nodes tabs; it does not reimplement lookup or rule
  matching.
- **modelling / optimiser** — the optimiser's ratebook-apply path is a
  downstream consumer, not a peer: it constructs synthetic rating-table specs
  from a saved artifact's factor tables and feeds them through the same
  `_apply_rating_table` primitive so optimised relativities are applied with
  identical join/miss semantics to any other rating table.

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
  `combinedOutputs[].baseValue`, a duplicate `combinedOutputs[].outputColumn`):
  raise `ValueError` eagerly, before the frame is touched.
- **Unsupported factor dtype** (`Date`/`Datetime`): raises `ValueError` naming
  the table and factor.
- **Incomplete table or factor** (no factors, no entries, no output column, no
  rules): this is a *documented no-op*, not a failure — the frame passes
  through unchanged for that table/factor. When a rating table has an
  `outputColumn` configured but is otherwise incomplete, the skip is logged at
  WARNING (`rating_table_skipped_incomplete`) so the gap is observable instead
  of a silently-missing column reaching a combined output.
  > NOTE: the public config-driven path rejects a populated entry row that
  > lacks any declared factor during `_rating_step_config` normalisation.
  > The lower-level `_apply_rating_table` primitive still returns unchanged
  > when a factor is absent from every already-normalised entry; when reached
  > through `_apply_rating_step_outputs`, that skip is visible only through the
  > WARNING log.

## Polars backend contracts (0.6.0)

Implementation is governed by the [Polars backend remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md). The following rating changes are approved before code work begins:

- A `min` or `max` combined output whose participating values are all null for any row
  raises `RatingExtremaUndefinedError(ExecutionError)` at runtime materialisation. The
  error identifies the combined-output column and operation; it never quietly returns
  null or an invented neutral value. Discovery of one invalid row fails the whole eager
  or lazy materialisation batch atomically before output publication or cache promotion,
  including a batch containing both valid rows and all-null rows. Partial output is never
  published.
- The transport contract maps `RatingExtremaUndefinedError` to HTTP 422 for synchronous
  requests and to `contract_error` for background execution. This is a data-dependent
  execution failure, not an eager configuration-validation failure.
- Once finite lookup-entry values have been validated, rating must remove the dead/masking `fill_nan` neutralisation from combine semantics. Null behaviour remains the documented miss-policy behaviour; NaN must not be silently converted into an arithmetic neutral value.
- A table whose declared input factor is absent from the input frame raises
  `RatingFactorMissingError(SchemaMismatchError)`, identifying the table and factor. The
  factor is checked against the once-resolved input schema before join construction or
  collection; it is not an incomplete-table no-op. The transport contract maps this error
  to HTTP 422 and background `contract_error`.
- A rating/combine plan resolves its input schema once and shares that resolved schema through every table and combined-output operation in that plan.
- Optimising the miss guard (FR22) is conditional on a representative benchmark showing a material benefit. Any such change must retain lazy/streaming materialisation timing, exact `RatingTableMissError` type and message content, warning behaviour for `onMissing: "neutral"`, and missing-key/row-count reporting.

Required tests cover all-null `min`/`max` rows, mixed-null extrema, mixed valid/all-null
batches, eager/lazy atomicity before publication and cache promotion, HTTP/background error
mapping, NaN rejection/non-masking after entry validation, absent factors for every supported
factor arity, one schema-resolution call across multi-table/combine plans, and error/warning
parity for any benchmark-gated miss-guard change. The 0.6 pre-1.0 release notes must call out
that all-null extrema and absent factors now fail loudly and name their exception and transport
contracts. Non-goals:
changing documented neutral-miss semantics, adding unsupported temporal factor support, or
implementing a speculative miss-guard optimisation without benchmark evidence.
