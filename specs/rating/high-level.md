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

- Breakpoints (ordered boundary list) and categorical (value remap) banding
  rule evaluation. These are the only two banding types; a factor names one.
- One-, two-, and three-factor rating-table lookups, including default-value
  fill and a loud/quiet miss policy.
- Combining multiple rating-table outputs with a required fixed finite numeric
  base value using `multiply` / `add` / `min` / `max`.
- Config normalisation: in memory a rating table's entries are one canonical
  ordered row array; its sidecar writes them as a grid (below). A breakpoint
  banding factor's sidecar writes its bands as a list, one `{"upper", "label"}`
  a band in order, the open-ended band with no `upper` last (READ-27): an edge is a
  number where the number reads back to the text it was typed as (`4`, `6.5`),
  otherwise its text, a date or a date with a time among them; whether a band
  includes its upper edge stays the factor's `right_closed`, and a `default` of
  null is left out. A sidecar still writing breakpoints as a map from edge to
  label is refused, naming the list form. Categorical banding keeps its
  value-to-group map, identity entries included, since an unlisted value falls to
  the default.
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

**Banding** (`apply_banding_from_config` / `apply_banding_factors`):

- Each banding "factor" reads one input column and writes one output column,
  and its `banding` is `breakpoints` or `categorical`. Any other value, or none,
  is rejected; there is no default type. (An operator/threshold `continuous`
  type existed and is removed: the editor never offered it.)
- `breakpoints` rules are an ordered list of boundaries with labels, closed on
  the right by default (`(lower, upper]`) or on the left when `right_closed:
  false`. At most one boundary may be open-ended (empty), and it anchors the
  final unbounded range. A boundary is a number (read by the one grammar for a
  typed number, specs/frontend-shared high-level "Typed numbers": `1,000` is a
  thousand and `40,5` is refused, never 405), a date (`YYYY-MM-DD`), or a
  date and time (`YYYY-MM-DD HH:MM`, optionally `:SS` and a fraction, `T` or a
  space between; no UTC offset), and every bounded breakpoint of one factor is
  the same kind. Numbers band a numeric column. Dates band a Date column, or a
  Datetime column by its calendar date in the column's own time zone, so
  "up to 2024-02-29" includes the whole of that day. Dates and times band a
  Datetime column by its wall-clock time in the column's own time zone. Any
  other pairing (numbers on a date column, times on a Date column, dates on a
  number column) fails when the node runs, naming the output and the column;
  an unreadable boundary or a factor mixing kinds fails validation. Internally each breakpoint becomes an
  interval rule (up to two operator/value pairs, `< <= > >=`); intervals are
  evaluated in order and the first matching one wins (`when/then` chain
  semantics), the rest falling to an explicit `default`. Runtime banding and
  trace enrichment consume the same interval-eligibility parser and the same
  immutable supported-operator contract, so a trace cannot credit a band the
  engine skipped.
- `categorical` rules remap a value of the column to its assignment. A rule's
  value is read as a value of the column's type and matched in the one
  dtype-faithful key form a Rating Step entry's level is (below), so on a
  Boolean column `true`, `"true"` and `1` all band the column's `true`, and a
  categorical rule and a rating entry written alike match the same rows. A
  rule's value the column cannot hold (`"maybe"` on a Boolean column, `"abc"`
  or `2.5` on an integer one) is refused when the node runs, naming the output
  column, the column and the value. Two rules for one value of the column's
  type (`"1"` and `"1.0"` on a Float64 column) are refused, never resolved by
  one of them. On a column of Null type no rule can match, and every row takes
  the default.
- **Band names.** A band's name (a breakpoint's label, a categorical rule's
  assignment, a factor's `default`) is text, and only a missing or empty name is
  no name. A number or a Boolean given as a band name (`0`, `0.0`, `false` in a
  settings file written by hand) is refused, as is a name that begins or ends
  with a space, which the Rating Step's level list shows trimmed and so would
  never match; each refusal names the factor's output column and the band, when
  the settings are loaded, linted, saved and run. The Banding editor trims a band
  name as it is entered (typed, pasted or set as the default), so a name it
  saves is the name the Rating Step lists.
- Float columns of every width (`Float16`, `Float32`, `Float64`: the `float` column
  kind) have NaN/Infinity sanitised to null before rule matching, so they always
  fall to the default rather than matching an arbitrary rule.
- A draft factor with no column, no output column, or no rules is a documented
  no-op (the frame passes through unchanged for that factor). A breakpoint's
  boundary of `0` is a boundary: only a missing or empty boundary is open-ended. The node's column
  contract leaves a draft factor out too, so a factor that has its columns but no
  rules yet previews as a pass-through rather than failing the output-column
  check for a column it does not create. Once a non-empty
  rule list is authored, at least one rule must have a usable key/condition and
  assignment; an all-unusable rule set is rejected rather than silently
  omitting the configured output column.
- Active factors write distinct output columns. Two active factors naming one
  `output_column` are refused, naming the column and both factors (by position
  and input column), when the config is validated and again when the node
  runs, in the executor and a standalone run alike, rather than the later band
  silently replacing the earlier. A draft factor sharing an active factor's
  output column does not count, since it writes nothing.

**Rating** (`apply_rating_step_from_config` / `_apply_rating_step_outputs`):

- Each table declares one to three `factors` (input column names), an
  `output_column`, and `entries` (rows of factor values plus a numeric `value`).
  A row keeps each factor's level under the factor's name and its rate under
  `value`, so no factor may be named `value`: the config codec refuses such a
  table wherever it comes from (an editor save, a sidecar read, the assistant),
  naming the factor and asking for the column to be renamed upstream, and the
  editor refuses to add one with the same advice. A column whose name only
  contains `value`, or differs from it in case, is an ordinary factor.
  A left join matches input rows to table entries on the canonicalised factor
  key (see below).
- A **miss** — an input row whose factor combination has no matching entry —
  never stops the run: a live quote must still be priced when a level is not
  in a table (decided 13 November 2026). A table with a `default_value`
  (**On miss** **Use default**) fills every miss with it silently; a table
  without one (**On miss** **Leave empty**) leaves the output null and logs
  each materialised batch containing misses at WARNING (`multiply`/`add`
  replace that null with their neutral element during a multi-column combine;
  `min`/`max` skip it when another value exists). There is no other miss
  setting and no `on_missing` key. A `default_value` that is not a finite
  number is refused by the config codec (`rating_default_value`), so a config
  holding one can be neither saved, read nor deployed, and again when a table's
  lookup is built from a raw table; each refusal names the table, since
  ignoring it would leave its misses empty unseen.
- A table the editor creates (the Rating Step node default and **Add table**)
  has no `default_value`, so it starts on **Leave empty**. Under the table the
  editor shows **On miss** (**Leave empty** / **Use default**) and **Default**,
  which is editable only on **Use default**: choosing **Use default** writes
  `default_value` `1`, and choosing **Leave empty**, or clearing **Default**,
  removes it. **Default** is the shared number field (specs/frontend-shared
  high-level, "Typed numbers"): `1,5` is refused at the field in words, in any
  browser language, never saved as 15, and the Default is written as a number.
  Whether a table uses a default is the engine's own rule (a non-blank
  `default_value`, a number or text the grammar reads as one), so the editor
  never says **Leave empty** of a table the engine fills. Every edit leaves an absent
  `default_value` absent.
- Table lookup keys are compared as strings through the factor column's
  originating Polars dtype. Table-entry values are first coerced through that
  dtype, then both the entry and input values use the same Polars expression:
  booleans become `"true"`/`"false"`; finite int-like floats inside the Int64
  range collapse to their integer digit string (`25.0` → `"25"`); other floats
  use the originating width's own Polars string form; and exact, categorical,
  string, decimal, and temporal values use Polars' cast for their declared
  dtype. A Duration entry string is the ISO-8601 duration Polars displays
  (`PT1.5S`, `-P1DT2H`), read exactly into the column's time unit and never
  rounded. Null stays null and therefore never matches; a factor column of Null
  type matches no entry, so every row takes the default. Supported factor dtypes
  are Float32/64, signed and unsigned integers, Boolean, String,
  Categorical/Enum, Decimal, Date, Datetime, Time, Duration, and Null.
- **An entry's level is a value of its factor column's type, or is refused.**
  Each level is read as it was written (a list holding `"true"` and `0` is not
  first made all text) and as the column's type holds it: `true`/`false`,
  `"true"`/`"false"`, `1`/`0` or `"1"`/`"0"` for a Boolean; a whole number for an integer
  type (`2.5` is no value of one); text that parses as a date or time for a
  temporal type (a number, a temporal type's physical count, is not one); and
  Polars' cast for the rest. A level that is not a value of the type (`"NE"` on
  an integer column, `"True"` on a Boolean one, `"x"` on a Datetime one) is
  refused before the lookup is built, naming the table's output column, the
  factor, the first such levels and the column's type, in Haute's words with no
  Polars expression or hint. The same reading is the one categorical banding,
  trace enrichment and the optimiser's ratebook keys take
  (`typed_level_keys`), so no surface matches a level another refuses.
- Lookup keys are materialised into collision-free temporary columns for the
  join. The source factor columns are never overwritten and therefore never
  need a lossy string-to-original-dtype revert.
- Multiple entries sharing the same factor key are refused (below). An entry
  whose key is null (a factor column of Null type) can match no row and so
  collides with no other; entries identical as written are refused whatever the
  column's type.
- After the table stage, `combined_outputs` combine every materialised table
  output column of the step with a required fixed finite numeric `base_value`
  using `multiply`, `add`, `min`, or `max`. A table that never materialised its
  output column (because it was incomplete — see Failure model) is omitted from
  combining, not silently referenced as null.
- Every table output column and combined-output column is unique across the
  rating step. Duplicate `tables[].output_column` values are rejected with the
  later table index and duplicated column named; the runtime combine boundary
  independently rejects duplicate participant columns so a bypassed
  normaliser cannot square or double-count a surviving factor.
- A table output or combined-output column replaces a same-named column
  already in the input frame, by design (apart from a combined output's own
  participants, which are refused above). Banding output columns replace a
  same-named input column the same way.
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
  `apply_rating_step_from_config` are the standalone twins of the
  executor's node builders: a saved pipeline file's Banding and Rating Step
  decorators call them, so running the file reproduces GUI preview behaviour
  exactly — see
  [execution-engine](../execution-engine/high-level.md).
- **Fail loud on ambiguous or silently-lossy config**, per project convention:
  NaN/Infinity/null table entries raise rather than corrupting downstream
  pricing arithmetic; a `default_value` that is not a finite number raises
  rather than being ignored; more than one open-ended breakpoint raises rather
  than silently keeping only the last; duplicate breakpoint boundaries raise
  rather than producing an empty interval. A rating-table miss is the
  deliberate exception: it is data, not config, and a run (a live quote above
  all) must still produce a price, so a miss without a default leaves its
  output empty and is logged for each materialised batch containing misses.
  Banding rejects unknown operators
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
- **A rating sidecar writes each table as a grid (READ-26).** `factors` lists
  the table's factors in order (up to three); with one factor there is no
  `columns` and each line of `rows` is a level and its rate; with more,
  `columns` lists the last factor's levels and each line of `rows` holds one
  combination of the other factors, its levels first, then one rate per
  column. A cell with no entry is `null`, and a lookup there misses and takes
  the table's default or stays empty; a rate of 0 is a rate. Levels keep their JSON type,
  so `25` and `"25"` stay distinct (JSON object keys would erase that, so the
  grid uses arrays). `default_value` is written as the editor holds it, a
  number; one a file holds as text is read by the one grammar for a typed
  number (`haute._typed_numbers.setting_number`), as a relativity held as text
  is; an entry
  holding a key other than its levels and its rate is refused at save, naming
  it, since the grid has no place for it,
  and `factor_dtypes`, the outputs and the other settings stay beside the grid,
  each line of which is one line of the file, so changing one rate changes one
  line. `_rating_step_config.compact_rating_config_for_sidecar` writes the grid
  and `expand_rating_config_from_sidecar` reads it back into the entries the
  editor and the engine work with; a sidecar still in the row-array form is
  refused, naming the grid. Table order, factor order, scalar
  identity, outputs, defaults, miss policy and `factor_dtypes` survive a save and
  load; entries come back in the grid's row-major order.
- **Two entries for the same levels are refused, never resolved.** A rating
  table holding two entries whose levels are equal once converted to the factor
  columns' types is refused by the shared lookup every run path uses, naming the
  table and the levels (`RatingDuplicateEntriesError`): no path keeps one of
  them, a config built in code included, and a duplicate that appears only after
  an upstream type change is refused at that run. Without duplicates the left
  join cannot fan out rows. The save refuses entries whose levels are identical
  as written and, where the table records `factor_dtypes` for every factor,
  entries equal under those types; the editor shows the save's refusal.
- **One banding node holds many factors, rather than one node per factor or
  separate numeric/categorical node types.** A single pipeline step often
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
  `apply_banding_factors` and `_apply_rating_step_outputs` directly, and
  `_apply_ratebook` (optimiser scoring) reuses `_apply_rating_table` and
  `_combine_rating_columns` to apply a saved ratebook as a rating lookup.
- **[pipeline-config](../pipeline-config/high-level.md)** — `_config_io.py`
  routes `BANDING`/`RATING_STEP` sidecar JSON through
  `expand_banding_config_from_sidecar` and `normalise_rating_step_config`
  on load. Rating-step sidecars use the same canonical normaliser on save.
- **[codegen](../codegen/high-level.md)** — emits Banding and Rating Step
  nodes as declarations (or, with post-processing code, as a `df` hook) whose
  decorators apply the sidecar through `apply_banding_from_config` /
  `apply_rating_step_from_config` in a standalone run
  ([pipeline-config](../pipeline-config/high-level.md)); `_code_extraction.py`
  recovers a rating step's post-processing code from the hook body.
- **[tracing](../tracing/high-level.md)** — `_trace_enrichment.py` imports
  `normalise_rating_key` and `normalise_rating_tables`/`normalise_banding_factors`
  to build the structured `rating_step`/`banding` trace detail payloads shown
  in the Calculation and Nodes tabs. Rating enrichment also receives the exact
  factor dtypes from the consumed parent frame; it does not reimplement lookup
  or rule matching. Banding enrichment reads a categorical factor's claims from
  the engine's own `banding_categorical_claims` with the consumed column's
  dtype, and a column's lineage reuses the step's enriched banding detail
  rather than matching again without the dtypes.
- **[server-api](../server-api/high-level.md)** — `routes/_rating_levels.py`
  publishes the levels of a Rating Step's raw factor columns over the node's
  shared data point, keyed by the same `_rating_key_expr` the lookup joins on,
  so a level offered in the editor is a level a run matches. It reads that
  module's key expression rather than rendering values its own way.
- **modelling / optimiser** — the optimiser's ratebook-apply path is a
  downstream consumer, not a peer: it constructs synthetic rating-table specs
  from a saved artifact's factor tables and ordered `factor_dtypes` descriptors,
  verifies those descriptors against the apply frame, and feeds the tables
  through the same `_apply_rating_table` primitive so optimised relativities are
  applied with identical join/miss semantics to any other rating table.

## Failure model

- **Rating-table miss, no `default_value`:** no exception — a miss never stops
  a run. Each batch with misses is logged at WARNING with table, output column,
  miss count, and up to 10 distinct missing keys, by a row-local scan wired into
  the lazy plan so it reports exactly when the plan runs, batch by batch under
  streaming. The output stays null for those rows (multiply/add fold it to the
  operation's neutral element downstream).
- **Unusable `default_value`** (present, not blank, and not a finite number):
  `ConfigSettingError` from the config codec when the config is validated
  (saved, read, deployed or built), naming the table's position and the value,
  and from the lookup builder for a raw table, naming its output column.
- **Malformed table entries** (non-finite banding rule value/boundary, NaN/Infinity
  or null rating entry `value` — an entry without `value` when another entry
  has one counts as null, wherever it sits in the table — a Duration entry key
  that names no exact duration of the column's time unit, more than one open-ended breakpoint, an
  open-ended breakpoint with no bounded anchor, a duplicate breakpoint
  boundary, an unsupported combine operation, a non-finite/missing
  `combined_outputs[].base_value`, a duplicate table/combined output column, or
  two active banding factors with one output column):
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
- **Incomplete table or factor.** A rating table with no `output_column`, and a
  banding factor with no column, output column or rules, is a *documented
  no-op*: the frame passes through unchanged for it. A rating table with an
  output column produces it or is refused: one with a Default and no entries
  produces it from the Default (every row misses, and the Default fills every
  miss); one with no factors, or with no entries and no Default, is refused
  (`ConfigSettingError`, setting `tables`) naming the table and what it lacks,
  with a fix, rather than left out of a column its node's contract promises.
  The editor marks a table with no entries only when it has no default. An
  entry list may omit factors only while it is empty; a populated entry list
  with no declared factors, with no entry holding a rate, or with no entry
  holding some factor's level is malformed and raises `ValueError`.
  A non-empty banding rule list whose rules are all unusable is malformed and
  raises instead of taking this no-op path.
- **Malformed collection shape:** a non-list banding `factors` value, a
  non-list rating-table `factors` or `entries` value, or populated rating
  entries without a non-empty factor list raises `ValueError` before the frame
  is touched. Executor-built nodes and generated standalone pipeline code use
  the same normalisation contract.
