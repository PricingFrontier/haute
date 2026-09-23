# Modelling roadmap

## Scope

The modelling node's GLM configuration surface: how a feature enters a GLM,
how each fit (term) is configured, how interactions declare their own
fits, how regularisation and solver settings reach RustyStats 0.9.0, and how
GLM results report inference validity. The offset contract is shared with
CatBoost, so its training and scoring baseline transform is in scope too.
Current behaviour is specified in
[the modelling specification](../modelling/low-level.md) and
[the modelling UI specification](../frontend-modelling-optimiser-ui/low-level.md).
CatBoost's Features pane keeps the current include/exclude cards and
monotonicity arrows unchanged.

The [model-family expansion plan](#model-family-expansion-proposed-22-september-2026-revised-23-september-2026)
also covers adding XGBoost, LightGBM, and InterpretML EBM throughout training,
evaluation, scoring, explanation, persistence, MLflow, and deployment. That
proposal is separate from the GLM design below; it does not claim the new
families are implemented or their change contracts approved.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-T00 | Planned | P1 | RustyStats 0.9.0 upgrade that keeps log-link exposure semantics bit-identical and pins the new theta and Tweedie-power behaviour. |
| MOD-T01 | Planned | P2 | GLM backend resolves model columns from terms and interactions, builds interactions without duplicated or re-typed main effects, ignores CatBoost-only levers, and drops the `all_factors` flag. |
| MOD-T02 | Planned | P2 | One GLM Features list where a feature enters the model only through its terms. |
| MOD-T03 | Planned | P2 | Interaction cards that pick two or more features and choose, per factor, the fit RustyStats 0.9.0 honours inside the interaction, including interaction-local splines. |
| MOD-T04 | Planned | P1 | GLM results report coefficients for every fit, show standard errors and p-values only when RustyStats marks inference valid, never fail delivery on non-finite statistics, and report smoothing and the regularisation actually applied. |
| MOD-T05 | Planned | P1 | Regularisation honours a fixed alpha, seeds and exposes cross-validation, refuses combinations RustyStats cannot fit honestly, offers robust standard errors and solver controls, and limits families and links to what RustyStats supports. |
| MOD-T06 | Planned | P1 | One offset meaning for GLM and CatBoost (a positive exposure multiplier under a log link), carried through training, saved models, and every scoring path. |
| MOD-T07 | Planned | P1 | A strict, dtype-aware GLM term contract and order-independent interaction resolution that never builds a design different from the configuration. |
| MOD-T08 | Planned | P2 | The GLM pane mirrors the backend contract, keeps every saved term and interaction visible and repairable, supports reference levels, and loses its duplicated code. |
| MOD-F03 | Proposed | P2 | Owner decision (the XGBoost slice is in place), then the complete LightGBM slice. |
| MOD-F04 | Proposed | P2 | Complete EBM slice with explicit round budgets on training rows, restricted persistence and native term explanations. |
| MOD-F05 | Proposed | P2 | Verify and publish the CPU release and its feature matrix. |
| MOD-F06 | Deferred | P3 | Add verified XGBoost and LightGBM GPU configurations after the CPU release. |

## Design (approved 16 September 2026; revised after Codex plan review and for RustyStats 0.9.0)

### Problem

The GLM Features pane has two sections that both decide model membership.
The Features list offers Include/Exclude and monotonicity arrows, but for a
GLM an included feature is not fitted unless it also appears in the Factors
list below or the `all_factors` flag is ticked. Monotonicity exists twice
(top-level `monotone_constraints` and the per-term `monotonicity`) and the
backend merges them. Interactions can only pair columns that already have a
main term and reuse that fit. Several latent defects sit in the same code:

- The Nat. spline row offers a monotonicity toggle that RustyStats rejects
  (`ns` has no `monotonicity` key).
- `all_factors` is absent from the config registry and the cache key list,
  so toggling it never changes the cached-result identity.
- `_build_interactions` passes `include_main: true` whenever any factor
  lacks a main term, and RustyStats then adds a main effect for **every**
  factor of that interaction. A probe with main term `x` and interaction
  `x × z` produced design columns `[Intercept, x, x, z, x:z]`, which is
  rank-deficient and fails the fit.
- A stale `exclude` entry on a GLM node silently removes native terms and
  whole interactions from the effective fit
  (`_effective_glm_params`), and a stale `monotone_constraints` entry on a
  removed term blocks training as an unknown feature
  (`_validate_monotone_constraints`).

### RustyStats 0.9.0 upgrade facts

The project moves from `rustystats>=0.7.0,<0.8` to `>=0.9.0,<0.10`
(numpy follows RustyStats' `numpy<2.4` pin down to 2.3.x). Verified on the
installed 0.9.0 wheel:

- **Offset semantics changed.** 0.7 log-transformed a string `offset` for
  log-link families, which is exactly what Haute's offset column means
  (`OFFSET_HELP`: a multiplier under a log link, additive under identity).
  0.9 takes `offset` verbatim on the link scale and adds `exposure=` for the
  raw positive rate denominator, log-transformed and reused as the
  exposure-weighted target-encoding denominator. Passing Haute's offset as
  `offset` on 0.9 would silently change every Poisson, Gamma, and Tweedie
  rate model. No existing test pins the multiplier semantics: the whole
  GLM-related suite passes on 0.9.0 except one theta test.
- **Unset Negative Binomial theta now raises** instead of silently fitting
  at `theta=1.0`; `theta="estimate"` profiles it on the plain GLM path.
  Haute's theta gate and dispersion estimator keep working; the test
  `test_unset_theta_is_the_silent_default` pins the old failover and fails.
- **Tweedie powers outside `1 < p < 2` are rejected** unless
  `allow_extended_tweedie=True`. Haute's slider offers 1.0 to 2.0 inclusive
  and its gate text calls 1 Poisson and 2 Gamma.
- `glm_dict` gains `input_transforms`; term `VALID_KEYS` and the expression
  grammar are unchanged from 0.7.

### What RustyStats 0.9.0 honours inside an interaction

Verified by building design matrices with `dict_to_parsed_formula` and
`InteractionBuilder.build_design_matrix_from_parsed`, and by a real
`glm_dict(...).fit().predict(...)`, on the installed 0.9.0 package:

- **Interaction-local splines are honoured** (new in 0.9). `bs(df=6)` inside
  an interaction with a `bs(df=4)` main term produced `c[T.b]:bs(x, k/6)`
  columns beside `bs(x, k/4)` main columns; `ns` behaves the same; a
  continuous × continuous card with two local splines produced the full
  tensor of basis products; fit and prediction on new data succeed.
- **Monotone splines inside an interaction are rejected by RustyStats**
  (`ms`, or `bs` with `monotonicity`): "Monotone fixed-df splines are not
  supported inside interactions".
- `linear` in an interaction forces a linear column even when the main term
  is a spline (`force_linear`).
- `categorical` in an interaction marks the column categorical globally: a
  numeric `x` with a linear main term and a categorical interaction slot had
  its main effect re-typed to one dummy per distinct value.
- `linear` on a string column fails inside the design-matrix builder with a
  float-conversion error.
- Support for `target_encoding` inside a product interaction depends on the
  other factor: categorical products fail (`ColumnNotFoundError: "TE(d)"
  not found`), while linear products can work. Joint target encoding is a
  separate supported mode and is exposed as described below.
- `include_main: true` still adds a main effect for every factor, so a
  partially overlapping interaction still duplicates an existing main term.
- Three or more factors, an expression term on an interacted column, and one
  column shared by two interactions all build correctly.

The interaction design below offers only combinations that build correctly
and rejects the rest before RustyStats sees them.

### Stored config contract (GLM)

#### Fit controls and encoded interactions (September 2026)

Native fits include `frequency_encoding`, keyed by the source
column, with no extra parameters. Expression and column ownership rules stay
the same.

- Main and interaction-local spline cards expose an explicit **Auto / Fixed**
  df selector. Auto removes `df` (and any explicit `knots`); no `"auto"`
  string or separate mode field is saved. A newly selected spline is Auto.
  Fixed writes a numeric df (initially 5, or enough for the current degree).
  Existing numeric df selects Fixed. Explicit knots also represent a fixed
  fit and replace the df input with a custom-knots indication.
- Advanced spline controls expose automatic basis size `k`, interior `knots`
  for fixed fits, and `boundary_knots`. `df`, `k`, and `knots` are mutually
  exclusive in builder writes: selecting Auto clears all three, selecting
  Fixed writes df and clears k/knots, setting k clears df/knots, and setting
  knots clears df/k. Clearing knots returns to Auto. Boundary knots survive
  mode changes. Lists must be finite, strictly increasing numbers; boundaries
  must contain exactly two values. Invalid drafts stay visible with an error
  and do not overwrite the saved value.
- Target encoding exposes **Auto / Fixed** prior weight. Auto removes
  `prior_weight`; an existing upstream `"auto"` also displays Auto. Fixed
  starts at 1 and accepts nonnegative numbers. The same control is used for
  joint target encoding. Advanced controls expose positive integer
  `n_permutations` (upstream default 4). Categorical terms expose optional
  `levels` as a JSON list of unique strings (quote numeric category labels
  exactly as represented by the column, such as `"1"` or `"1.0"`). Numeric
  JSON values are rejected because RustyStats compares string labels and
  would silently build incorrect indicators. Builder type changes preserve
  all supported parameters shared by the old and new types.
- Interaction cards add **Product / Target encoding / Frequency encoding**.
  Saved `encoding` is absent for Product, or `"target_encoding"` /
  `"frequency_encoding"`. Joint target encoding also accepts optional
  `prior_weight` and `n_permutations`. Changing mode clears product `specs`
  and encoding-only parameters so hidden settings cannot change the fit.
  Joint encodings combine raw factor values, with no per-factor fit controls;
  columns with target/frequency-encoded or monotone main terms remain eligible.
  Product interactions additionally support exactly one target-encoded
  non-numeric factor with all remaining factors fitted Linear. This applies
  to explicit slot fits and inherited native target encoding. Categorical,
  spline, frequency-encoded and additional target-encoded partners are
  unavailable. Missing slots may be filled later; subsequent feature and fit
  choices must respect the existing target-encoded factor. Saved incompatible
  choices stay visible with a warning, never silently rewritten.
  Target encoding in a Product always includes its target-encoded main effect,
  even when Include main effects is off; show this fact beside the controls.
  If the feature already has a native or named target encoding, reuse its
  settings and identify that term in the UI rather than offering independently
  adjustable settings. Conflicting saved slot settings are flagged and can be
  reset to the shared encoding. Otherwise offer the normal prior-weight and
  permutation controls. The adapter explicitly registers the required encoding
  in effective terms so both settings are honoured, using a free internal alias
  with `variable` when another native fit occupies the feature key. User terms
  and other native fits are preserved. Encoding settings for the same source
  must agree across all product interactions and existing encodings; mismatches
  fail before RustyStats. Reject numeric target-encoding slots, unsupported
  parameters and incompatible partner fits before library invocation.
  Categorical `levels` apply only to native terms; product
  categorical overrides must not offer or accept this ignored parameter.
- The adapter maps `encoding` to RustyStats' interaction-level boolean flag,
  forwarding target-encoding parameters without inventing defaults. It emits
  raw factor placeholders with type `linear` and `include_main=False`: the
  encoding branch reads raw column values, while categorical placeholders
  would re-type numeric main effects globally. Existing main effects are
  preserved; Include main effects adds only absent dtype-default terms.
  Product interactions keep their current materialisation and conflict checks.
  Duplicate interaction identity is (encoding mode, unordered factor set),
  allowing different modes over the same factors. Unknown modes, product
  overrides on joint encodings, and misplaced/invalid encoding parameters
  fail clearly before fitting, including in saved partial cards.
  Factor names reserved by RustyStats for interaction settings (`include_main`,
  `target_encoding`, `frequency_encoding`, `prior_weight`, `n_permutations`)
  are rejected rather than being overwritten or silently omitted.
- Acceptance checks cover Auto/Fixed persistence without sentinel values,
  mutually exclusive spline settings, retained unrelated fields, invalid
  array drafts, joint-mode transitions, encoded-main column eligibility,
  mode-aware duplicates, missing-main materialisation, and actual fitting,
  prediction and save/load for both joint encodings. The backend term-column
  resolver and parameter contract include frequency encoding. Main and local
  spline Auto/Fixed fits must demonstrably route smoothing differently.

No schema migration. Haute has no released users, so removed fields are
dropped outright and stale tests are updated.

- `terms` stays the RustyStats dict `{name: spec}`. A **native** spec
  (`linear`, `categorical`, `bs`, `ns`, `ms`, `target_encoding`,
  `frequency_encoding`) is keyed by
  the column it fits, so each column carries at most one native fit. An
  **expression** spec `{"type": "expression", "expr": ..., "monotonicity"?}`
  is keyed by a user-editable name that is not a column name; a column may
  carry any number of expression fits. Expressions follow the grammar in
  "Expression grammar" below (a subset of what RustyStats parses: `x ** n`,
  `x + y`, `x - y`, `x * y`, `x / y`, or a bare `x`). Every identifier must
  be a schema column of a continuous or integer dtype.
- Additional fits may also be named `target_encoding` or `frequency_encoding`
  specs with RustyStats' supported `variable` key pointing to the raw feature.
  A feature may have one target encoding and one frequency encoding alongside
  its native fit and expressions. Duplicate encodings of the same type/source
  are rejected, including duplicates between a native and named encoding.
  Named encoding keys must not shadow another upstream column. `variable` on
  other fit types, and the `interaction` redirect key, remain unsupported.
  Column resolution and model membership follow `variable`, not the alias.
  A column-key encoding that explicitly repeats its own source in `variable`
  remains a native term in the editor; adding a term never overwrites it.
  RustyStats 0.9's `required_columns` reports these aliases; Haute's native
  model loader resolves them through the saved encoding specs before scoring,
  retaining other required columns (including exposure/offset). The native
  RustyStats artifact and library are unchanged.
- The builder edits RustyStats' per-type keys: `linear` {monotonicity};
  `categorical` {levels}; `bs` {df, k, degree, monotonicity, knots,
  boundary_knots}; `ns` {df, k, knots, boundary_knots}; `ms` {df, k, degree,
  monotonicity, knots, boundary_knots, default monotonicity `increasing`};
  `target_encoding` {prior_weight, n_permutations, variable}; `frequency_encoding` {variable};
  `expression` {expr, monotonicity}. `categorical` additionally carries
  Haute's `reference` key, translated before RustyStats sees the term (see
  "Term parameter contract"). A type switch keeps supported shared
  parameters for the new type. Term types outside the eight above
  and the redirecting key `interaction` are rejected by column resolution.
  `variable` is accepted only for the two encoding types; other native keys
  must be the column RustyStats reads. Parameter values and combinations
  follow "Term parameter contract". A backend test fits each RustyStats key
  through the library and asserts RustyStats accepts it for that type.
- `interactions` entries are `{"factors": [...], "specs": {factor: spec},
  "include_main": bool, "encoding"?: mode}`. Product `specs` holds only explicit **overrides**, each
  one of `{"type": "linear"}`, `{"type": "categorical"}`, `{"type": "bs",
  "df"?, "k"?, "degree"?, "knots"?, "boundary_knots"?}`, `{"type": "ns",
  "df"?, "k"?, "knots"?, "boundary_knots"?}`, or `{"type":
  "target_encoding", "prior_weight"?, "n_permutations"?}`. A factor with no
  entry resolves as described in "Interaction resolution": an inheritable
  single main effect, else the dtype default of its column type (see
  "Column types"). Two or more filled
  factors are required; incomplete entries are skipped at train time, as
  today. Encoded modes and their parameters follow the fit-controls contract
  above and carry no product overrides.
- `all_factors` is removed from config, backend, validation, and docs.
- `exclude`, `feature_columns`, `monotone_constraints`, and
  `feature_weights` are CatBoost-only levers (one shared list). The GLM
  pane never writes them; `build_training_job_kwargs` passes none of them
  for GLM regardless of stored values; `GLMAlgorithm.fit` refuses a
  `monotone_constraints` or `feature_weights` argument;
  `_effective_glm_params` no longer narrows terms or interactions by
  `exclude`; the training route passes no sink exclusions for GLM on both
  `_execute_and_sink` call sites in `_training_lifecycle.py` (training and
  dispersion), and the feature-selection diagnostic treats GLM exclusions
  as empty; and `TrainingJob` construction rejects a GLM job that carries
  `monotone_constraints` with a message naming the per-term `monotonicity`
  key as the GLM lever.
- `offset` keeps its meaning. The adapter maps it to RustyStats `exposure=`
  when the effective link is `log` and to `offset=` otherwise. At fit and
  dispersion estimation the effective link is the explicit `link`, else
  `rustystats.formula.get_default_link(family)` (not exported at the package
  root on 0.9.0); at prediction it is the fitted model's resolved
  `model.link`, so the two paths cannot disagree. The offset contract shared
  with CatBoost, and the scoring accessors that read the fitted model's
  exposure or offset column, are defined in "Offsets across GLM and
  CatBoost".

### Model membership

A GLM feature is in the model exactly when it has at least one term or is a
filled factor of an interaction. The training gate message becomes "add a
term to at least one feature". "Fit all with defaults" materialises explicit
dtype-default native terms for every eligible column that lacks a native
term, leaving existing terms untouched; it replaces the `all_factors`
opt-in. Empty terms still block training, and RustyStats is never asked to
build terms itself: the direct-construction API refuses a GLM without terms
instead of generating one term per column.

### Backend column resolution (two phases)

Phase one is schema-free. `glm_model_columns(terms, interactions)` in
`src/haute/modelling/_rustystats.py` returns the ordered unique identifiers
the model reads: native term keys, named encoding sources, every identifier of each expression term
(after the expression passes the grammar above), and every filled
interaction factor. It raises `HauteValidationError` naming the term when an
expression fails the grammar, when an expression key collides with a
native term key or with one of its own identifiers, when a term type is
outside the eight supported types, when an encoding type is duplicated for
one source, or when a term carries `interaction` or an unsupported `variable`
redirect (anything except target/frequency encoding). This phase serves the projection demand computed before
graph execution (`_training_required_columns_by_node`) and the
evaluation-key guard in `TrainingJob` construction, neither of which has a
schema.

Phase two runs against the **exact unprojected** input schema. A new
`_training_preparation.resolve_training_input_schema(graph, node_id,
preamble_ns, source)` builds the modelling node's input through the shared
engine with `execute_lazy_graph(..., schema_only=True,
required_columns_by_node=None)` and returns
`lazy_outputs[node_id].collect_schema().names()`; this is the same
schema-only mode `chunking.py` and the assistant already rely on, so
Polars transforms that add, rename, or drop columns are reflected exactly.
RAM-estimate column metadata is never used for validation; it walks past
transforms to ancestor metadata and is sizing data only.
`validate_glm_model_columns(terms, interactions, schema)` raises when an
identifier or native key is not a schema column or when an expression key
names a schema column (a column key always means a native fit).
`_training_lifecycle` calls both, in that order, before `_estimate_ram` and
`_execute_and_sink` on the training path and on the dispersion path, so a
collision with an upstream column that projection would otherwise drop is
rejected before any execution. A schema that cannot be resolved is an
explicit 422 naming the failing node; there is no fallback to the projected
schema. The `TrainingJob` final feature contract and the dispersion worker
keep a defensive re-check against the loaded frame for the direct
construction API, each replacing the current `set(terms)` comparison.

### Interaction building

For Product mode, `_build_interactions(interactions, terms, dtype_classes)`
resolves each factor's spec by the order-independent rules in "Interaction
resolution" (override, else an inheritable single main effect, else the
dtype default), and then:

- rejects, naming the interaction and factor, an override type outside
  {`linear`, `categorical`, `bs`, `ns`, `target_encoding`}; any override carrying
  `monotonicity`, `levels`, or `reference`; a fit the column's dtype class
  does not allow; an inherited main effect that interactions cannot honour
  (monotone fits, level-restricted categoricals, frequency encoding) or a
  column with several main effects and no override; target encoding
  alongside any non-Linear partner; and two cards with the same factor
  set and encoding mode in any order;
- after every card is resolved, rejects a `categorical` slot on a column
  whose main effect is not categorical, a `linear`, `bs`, or `ns` slot on a
  column whose main effect is categorical, and a target-encoding slot on a
  column whose main effect is categorical, whether that main effect was
  configured or materialised;
- materialises a main effect for every factor that has no main effect when its
  cards' `include_main` is true and those cards resolve it to one spec,
  adding that spec to the terms dict handed to RustyStats exactly once per
  column; cards that resolve the column to different specs are rejected
  together; RustyStats always receives `include_main: False`, so no main
  effect is ever duplicated;
- registers the mandatory Product target-encoded main effect independently
  of `include_main`, preserving other native fits with a unique encoding alias
  when needed. Dispersion estimation projects raw encoding sources rather than
  internal alias names and uses the same resolved terms and interactions;
- passes an unconstrained spline main term through unchanged (0.9 builds an
  interaction-local spline with the same parameters) and passes the
  override spec verbatim otherwise.

Tests assert the resulting **design columns** through
`dict_to_parsed_formula` and `InteractionBuilder`, not merely the arguments
forwarded to RustyStats.

### GLM Features pane

`GLMTermsConfig.tsx` replaces both `CommonFeatureConfig` (for GLM) and
`GLMFactorConfig`. `CommonFeatureConfig` becomes CatBoost-only and loses its
`algorithm` prop and GLM-specific hint text.

- Header: "Features", "N of M in model", the existing search box, an
  "In model only" filter toggle, bulk "Fit all with defaults" and "Remove
  all terms" (the latter confirms once and clears `terms` and
  `interactions`), and the Builder/JSON mode toggle.
- Feature row: column name, dtype pill, a muted "Not in model", "Interaction
  only", or "In an expression" tag where applicable, and an Add term button.
  Terms are directly editable beneath their feature header, with a visible
  indent. Add term inserts the term in that row without navigating away or
  changing the search/filter. Other feature rows and interactions remain
  available in the same pane; there is no Configure or Back navigation.
- Inline term card (shared `TermCard.tsx`): every term has a visible Fit type
  selector, even when only one choice remains. Native choices are filtered
  by dtype: linear and splines for numeric columns; categorical and encodings
  for non-numeric columns. Explicit Auto / Fixed selectors for spline df
  and target-encoding prior weight, and Advanced parameters as listed above;
  for an expression term, labelled name and expression fields. Monotonicity uses the existing
  red-down/yellow-neutral/green-up arrows for `linear`, `bs`, and
  `expression`, and an increasing/decreasing pair for `ms`. Every sub-card
  has a remove button beside its controls. Cards have no separate "Main term"
  or "Expression" heading. Fields retain visible labels and 12px controls,
  with compact widths and padding in a wrapping row. The Advanced toggle sits
  inline with those controls; expanded settings appear beneath the row and
  retain unfinished drafts when collapsed. Narrow panes wrap controls without
  horizontal overflow.
  Expression name and expression each have a visible label. Additional terms
  never convert into linear, categorical, or spline fits through the type
  select. Fit menus follow the column's dtype class ("Column types"):
  continuous columns never offer categorical fits or target/frequency
  encoding, while integer columns (integer-coded rating factors) offer them
  beside the numeric fits.
  Continuous additional terms show Fit type: Expression beside the compact
  expression fields, even though it is the only choice. Integer, boolean and
  categorical features offer target and frequency encoding as additional
  fits. A saved encoding on a continuous column remains
  visible until explicitly changed or removed; rendering never rewrites it,
  and it does not make the other encoding available. Other fits invalid for
  the dtype and encodings used by another term are omitted from the choices,
  rather than shown disabled. A card's saved selection stays visible; removing
  or changing another term immediately refreshes the available choices. Switching an
  additional type retains its key, preserves only supported parameters, and
  writes/removes `variable` or `expr` atomically. Encoding cards use the same
  compact fit/prior/Advanced controls as native cards. Existing expression
  values are preserved.
- An expression term is listed under the first eligible column its
  expression names. Additional terms whose sources name no eligible column
  are listed in a trailing "Unresolved terms" group with a warning and removal
  controls. Expression name/value repairs persist and re-anchor the card;
  encoding source repairs use JSON. Unresolved encodings still show their
  saved fit type. Training fails loudly on unresolved terms.
- JSON mode is unchanged: the RustyStats `terms` dict, saved on blur.

### Editor transitions

Each transition names the exact config write; anything not listed writes
nothing.

- **Add term** on a row with no native term: `terms[col] =` dtype default
  (`{"type": "categorical"}` for string dtypes, `{"type": "linear"}`
  otherwise).
- **Add term** on a row with a native term: `terms[name] = {"type":
  "expression", "expr": "<col> ** 2"}` where `name` is `<col>_sq`, then
  `<col>_sq2`, `<col>_sq3`, … skipping names that exist in `terms` or equal
  an eligible column. An expression-only row is reached by adding the
  native term, adding the expression, then removing the native term.
  Numeric rows retain this expression default. Non-numeric rows add the first
  unused encoding (target, then frequency), keyed by a collision-free
  `<column>_te` / `<column>_fe` name with `variable: <column>`. When both encodings
  are present, Add term is disabled on a non-numeric row with a native term.
  Removing the original native term leaves additional fits editable under their
  source feature; the next Add term restores the dtype-default native fit.
- **Native type switch** to `T`: `terms[col] =` the current spec reduced to
  `{"type": T}` plus the supported keys for `T` that were present; switching to
  `ms` adds `"monotonicity": "increasing"` when absent.
- **Parameter edit** (df, degree, prior weight, monotonicity): writes that
  key; an emptied numeric field or the neutral arrow deletes the key.
- **Expression name edit** commits on blur: a name that is empty, equals an
  eligible column, or equals another term key is refused inline (the draft
  stays, the reason shows, config unchanged); otherwise the term is re-keyed.
- **Expression edit** commits on blur when it matches the grammar and every
  identifier is an eligible column; otherwise refused inline with the
  grammar shown. Changing the first identifier moves the sub-card under
  that column's row.
- **Remove term**: deletes `terms[key]`; `interactions` are untouched.
- **Fit all with defaults**: adds the dtype default for every eligible
  column with no native term.
- **Remove all terms**: after one confirm, writes `terms = {}` and
  `interactions = []`.

### Interactions

`GLMInteractionsConfig.tsx` renders beneath the Features list with the same
inline card language and modelling colours. Each card names its factors and
shows its labelled controls and validation warnings directly in the list.
Use the main-effect cards' compact input sizes, font, spacing and indented
rows: each factor's Feature and Fit type selectors sit together, followed by
its parameters and inline Advanced disclosure, wrapping on narrow panes.
The interaction's Fit type and target-encoding controls share a compact row.
Fit menus omit unavailable choices rather than disabling them. Interaction
modes are filtered by selected factor dtypes, encoded native main terms and
other interactions over the same factor set: joint encodings are offered for
non-numeric factors, Product filters according to supported per-factor fits, and modes
already used by another interaction are omitted. Empty slots allow choosing
a mode first; joint-mode feature menus then omit numeric columns. Keep an
existing saved selection visible, with the existing validation warnings or
an inline unavailable-fit warning where needed; rendering never rewrites it.
"Add interaction" appends `{"factors": ["", ""], "include_main": true}`
without navigating away. Each card offers Product, Target encoding, or
Frequency encoding. In Product mode each slot pairs a column select (any
eligible column not already in that card that can take a supported fit
alongside its other factors)
with a fit select offering the supported product fits:
**As main term** (only when the column has exactly one inheritable main
effect, per "Interaction resolution"; deletes `specs[col]`), **Linear**
(continuous and integer columns;
writes `specs[col] = {"type": "linear"}`; refused when the main effect is
categorical), **Categorical** (integer, boolean and categorical columns without
a main effect, or with a categorical main effect;
writes `specs[col] = {"type": "categorical"}`),
**B-spline** and **Nat. spline** (continuous and integer columns; write
`specs[col] = {"type": "bs"|"ns"}` plus df and, for `bs`, degree fields;
never a monotonicity control; refused when the main effect is categorical).
**Target enc.** is available for an integer, boolean or categorical factor
only while every other picked factor uses Linear and the factor's main effect
is not categorical; with such a factor selected, partner fit menus
offer only Linear (and As main when that main fit is Linear). Native or
named target encoding can be inherited under the same rule. A
frequency-encoded main effect cannot be inherited in a Product, but its raw
source can use an explicit target-encoding override with Linear partners.
A column whose main effect interactions cannot honour (a monotone spline, a
monotone linear or B-spline term, a categorical term with `levels` or
`reference`, frequency encoding, or several main-effect terms) must pick an
explicit slot fit; the slot names the reason until it does. A slot with no
main effect and no override selects its dtype default directly in Fit type,
without persisting an override. As main term names the inherited fit in the
option label, without a separate Effective fit field.
Picking a column writes `factors[i]` and deletes any `specs` entry for the
column it replaced. "+ feature" appends an empty slot; a slot can be
removed when the card has more than two. The "Include main effects"
checkbox appears only while at least one picked column has no native term,
with the help text "Adds a main effect for factors that have none; factors
with a term already keep it". A card with fewer than two picked columns
shows "Pick at least two features" and is ignored at train time. A card
whose factor set equals another card's shows "Duplicate interaction" and
training rejects it. Removing a card removes its entry.

### Test impact

- `specs/modelling/low-level.md`: the "Modelling-node algorithm config"
  bullet (GLM keys, `specs` overrides, `exclude` no longer narrowing GLM),
  the `monotone_constraints` bullet (CatBoost-only, GLM rejection), the
  offset sentence (exposure mapping by effective link), and the
  `_build_interactions` bullets (materialised main effects, rejected
  combinations, local splines, design-column tests).
- `specs/frontend-modelling-optimiser-ui/low-level.md`: the
  `CommonFeatureConfig`/`GLMFactorConfig` bullet, the Features-pane
  sentences in the numbered overview, and the Testing bullet that currently
  lists "both algorithms' common feature browser" and "confirmed
  explicit-factor dependency cleanup", which become the GLM terms pane,
  editor transitions, and interaction slot rules.
- `docs/building-models/nodes/model-training.md`: the `terms` and
  `interactions` rows (which list term types that do not exist).
- Named failing scenarios are listed under each package's Acceptance.

## Design amendments (17 September 2026 review)

The branch review verified each defect below against the installed
RustyStats 0.9.0 wheel by fitting real models. The contracts here refine the
design above and are implemented by MOD-T04 to MOD-T08.

### Result integrity

RustyStats 0.9 classifies every fit with `inference_status` and fills
`coef_table()` standard errors, z-values and p-values with NaN unless the
status is `valid_standard` (or `valid_robust`). Penalised smooth splines
(`unavailable`), monotonicity constraints (`constrained_boundary`), and every
regularised fit (`naive_after_regularization`, `naive_after_selection`,
`naive_after_cv_selection`) are therefore NaN, and the training response's
finite-JSON guard rejected those jobs as system faults.

- `glm_inference` is `{status, valid, standard_errors, reason}`. `status` is
  the fitted model's `inference_status`, or `singular_design` when RustyStats
  reports valid inference but a standard error is not finite. `valid` is
  true only for `valid_standard`. `standard_errors` is `model` or the robust
  type in use when valid, and null otherwise. `reason` is null when valid and
  otherwise one sentence per status: the ridge penalty shrinks coefficients;
  lasso or elastic net selects variables; the penalty was chosen by
  cross-validation; monotonicity constraints restrict the coefficients;
  automatically smoothed splines are penalised; covariance was not computed;
  standard errors are not finite because the design is close to singular.
- Every `glm_coefficients` row carries `feature` and a finite
  `coefficient`. `std_error`, `z_value`, `p_value`, and `significance` are
  present only when inference is valid and are null otherwise. A non-finite
  coefficient fails the `glm_coefficients` diagnostic in `diagnostics_errors`.
- `glm_relativities` exist only for log-link models; any other link yields
  an empty list, never `exp` of a non-log coefficient. `ci_lower` and
  `ci_upper` are null unless inference is valid. A relativity or confidence
  bound that is not finite (`exp` overflow, as for an unscaled term or a
  categorical level whose response is always zero) fails the
  `glm_relativities` diagnostic naming the terms; the job still succeeds.
- `glm_fit_statistics` contains finite numbers only: `deviance`,
  `null_deviance`, `n_obs`, `df_model`, `df_residual`, `iterations`,
  `converged`, `scale`, `log_likelihood` (omitted for quasi-likelihood
  families), `aic` and `bic` when RustyStats defines them (it returns none
  for quasi-likelihood families and for fits without valid inference, except
  penalised smooth fits, which use effective degrees of freedom), and
  `total_edf` and `gcv` for smooth fits. A non-finite value fails the
  `glm_fit_statistics` diagnostic.
- `glm_smooth_terms` lists each penalised smooth term as `{term, k, edf,
  lambda}`.
- `glm_regularization` is null for unpenalised fits and otherwise
  `{penalty, mode, alpha, l1_ratio, n_nonzero, cv_folds, cv_selection,
  cv_seed}` read from the fitted model, so `alpha` is the penalty actually
  used; the cross-validation fields are null in fixed mode. It is not
  derived from `regularization_path`, which 0.9 returns as a list (the old
  attribute read always reported a selected alpha of 0).
- The Coefficients pane shows the reason above the table, dashes in the
  statistic columns, no significance legend, and term order instead of
  p-value order when inference is invalid; it names robust standard errors
  when they are used. Relativities draw confidence whiskers only when bounds
  are present. The Summary shows smooth terms and the regularisation block.

### Regularisation and solver controls

- Config keys: `regularization` (`ridge`, `lasso`, `elastic_net`, or
  absent), `alpha` (absent or 0 selects the penalty by cross-validation; a
  positive number is a fixed penalty), `l1_ratio` (elastic net, 0 to 1),
  `cv_folds` (integer 2 to 20), `cv_selection` (`min` or `1se`), and
  `cv_seed` (non-negative integer).
- Cross-validation mode passes `regularization`, `cv=cv_folds`,
  `selection=cv_selection`, `cv_seed`, and the elastic-net `l1_ratio` to
  `fit()`. RustyStats draws unseeded folds, so an unseeded fit picked a
  different alpha on every run. Choosing a regularisation type in the pane
  writes `cv_folds: 5`, `cv_selection: "min"`, and `cv_seed: 42` when they
  are absent; the values stay visible and editable and survive type and mode
  changes. The objective gate refuses a cross-validation config missing any
  of them.
- Fixed mode passes `alpha` and `l1_ratio` (0 for ridge, 1 for lasso, the
  configured ratio for elastic net) and never `regularization`: RustyStats
  ignores `alpha` whenever `regularization` is passed, so a configured alpha
  of 0.5 trained at a cross-validated 59.5.
- Regularisation of either mode is refused before training when any
  penalised smooth spline is present (a `bs`, `ns`, or `ms` main term, or a
  `bs` or `ns` interaction slot, without `df` or `knots`), naming the terms:
  RustyStats raises for cross-validation and silently reuses a fixed alpha as
  the smoothing parameter.
- `max_iter` (integer 1 to 10000) and `tol` (number greater than 0 and less
  than 1) are optional and reach `fit()` only when set; absent keeps
  RustyStats' route-specific defaults.
- `robust_standard_errors` (`HC0`, `HC1`, `HC2`, or `HC3`) is optional. When
  set, the fit keeps its design matrix (`store_design_matrix=True`) and the
  coefficient table and relativity bounds use `bse_robust`,
  `tvalues_robust`, `pvalues_robust`, significance codes from the robust
  p-values, and `conf_int_robust`. It is refused with regularisation,
  monotonicity constraints (a `linear`, `expression`, or `bs` term with
  `monotonicity`), or penalised smooth splines, where RustyStats marks
  inference invalid. Robust statistics are computed on the fitted model
  before it is saved; a reloaded model keeps the stored table.

### Families, links, and dispersion parameters

- One table, `GLM_FAMILY_LINKS` in `haute.modelling._train_config`, drives
  backend validation, and the Target pane reads an identical TypeScript
  constant pinned by a contract test: gaussian identity or log; poisson log
  or identity; quasipoisson log or identity; binomial logit, log, or
  identity; quasibinomial logit, log, or identity; gamma log or identity;
  tweedie log or identity; negbinomial log or identity. The first link is
  canonical and equals `rustystats.formula.get_default_link`. RustyStats
  0.9.0 supports only identity, log, and logit, so `inverse`, `sqrt`,
  `probit`, `cloglog`, `inverse_squared`, and the `inverse_gaussian` family
  are removed. Quasibinomial defaults its metrics to AUC and log loss.
- Tweedie `var_power` must lie in [1, 2]; `allow_extended_tweedie=True` is
  passed only for exactly 1 or 2.
- `theta` must be a finite positive number. `theta`, the controls above, and
  every GLM key are declared in the modelling config type and the cache
  config classification, so a sidecar write keeps them (theta was dropped
  on save). The Negative Binomial help says RustyStats refuses to fit without
  an explicit theta and that Estimate profiles the likelihood on the node's
  training data.

### Offsets across GLM and CatBoost

- An offset column is a strictly positive exposure multiplier under a log
  link and an additive term otherwise, for both algorithms. GLM log link:
  `exposure=`. CatBoost `Poisson` and `Tweedie` losses: baseline
  `log(offset)`. `RMSE`, `MAE`, `Logloss`, and `CrossEntropy`: baseline
  verbatim. CatBoost applied every offset verbatim, contradicting the shared
  help that promises 2× exposure gives 2× the expected count.
- CatBoost models record the transform as `haute_offset_link` (`log` or
  `identity`) beside `haute_offset_column`; every predict, diagnostics,
  explainability, and scoring path applies the recorded transform, and a
  model recording an offset column without a transform is refused with a
  retrain message.
- Training refuses a log-link offset with null or non-positive values before
  fitting, naming the column and the row count; log-link scoring refuses the
  same values.
- A RustyStats model records its column on `_exposure_spec` (log link) or
  `_offset_spec`. One accessor serves the native loader's
  `ScoringModel.offset_column` and the scorer's offset guard, which read only
  `_offset_spec` and so lost every log-link GLM offset.

### Column types

Backend and pane share five dtype classes, pinned by a fixture of Polars
dtype names:

- continuous (floats, decimals): `linear`, `bs`, `ns`, `ms`, expression
  operands; default `linear`.
- integer (signed and unsigned integers): the continuous fits plus
  `categorical`, `target_encoding`, and `frequency_encoding`; default
  `linear`.
- boolean: `categorical`, `target_encoding`, `frequency_encoding`; default
  `categorical`.
- categorical (String, Categorical, Enum): `categorical`, `target_encoding`,
  `frequency_encoding`; default `categorical`.
- unsupported (dates, times, durations, lists, arrays, structs, binary,
  null, object): not eligible. The pane lists how many columns it hides, and
  validation refuses any term, operand, source, or factor of this class.

The backend used string dtypes alone as categorical while the pane used a
numeric check, so Boolean, Date, and Enum slots were shown as Categorical
and fitted as linear. Joint encodings accept integer, boolean, and
categorical factors. The route gate validates classes against the
unprojected input schema; the job and adapter validate them against the
training frame.

### Term parameter contract

- Every term has a string `type` and only its type's keys (the builder's
  per-type keys plus `reference` on `categorical`). An unknown key is refused
  by name.
- `monotonicity` is `increasing` or `decreasing`; RustyStats reads any other
  truthy value as decreasing.
- At most one of `df`, `k`, and `knots`; RustyStats silently prefers `k`
  over `df`.
- `df` and `k` are integers no larger than 20 and at least `degree + 1` for
  `bs` (degree 3 when unset), and at least 2 for `ns` and `ms`; below those
  minimums RustyStats silently widens the basis. `degree` is an integer from
  1 to 5. `knots` holds 1 to 20 finite, strictly increasing numbers.
  `boundary_knots` holds exactly two finite increasing numbers that enclose
  every knot.
- `prior_weight` is `auto` or a finite non-negative number; `n_permutations`
  is an integer from 1 to 100.
- `levels` is a non-empty list of unique strings; `reference` is a non-empty
  string; a term carries at most one of them. `reference` is translated when
  the model is fitted into `levels` holding every observed label (NumPy
  string form, nulls excluded) except the reference, so the reference and
  unseen levels share the intercept. A `reference` or listed level that the
  training data does not contain is refused with the observed labels, rather
  than producing an all-zero column that RustyStats reports as a singular
  matrix. Boolean labels are `True` and `False`.
- Interaction overrides accept the keys listed in the stored config
  contract with the same bounds. An interaction entry is a mapping with only
  its documented keys; `factors` is a list of strings (an empty string is an
  unfilled slot), `specs` maps picked factors to mappings, and
  `include_main` is a boolean.
- Role columns (target, weight, offset, fold, identifiers, and the
  evaluation group or date key) cannot be term columns, expression operands,
  encoding sources, or interaction factors.
- Messages that list available columns show at most 20 names and the total.

### Expression grammar

- An identifier is a Unicode letter or underscore followed by Unicode
  letters, Unicode numbers, or underscores. A number is ASCII digits with
  optional ASCII decimal digits.
- The forms are `identifier`, `identifier ** operand`, and
  `identifier op operand` with `op` one of `+ - * /` and `operand` an
  identifier or a number, with surrounding whitespace allowed.
- A right-hand identifier that Python's `float()` parses (`inf`,
  `infinity`, `nan` in any case) is refused, because RustyStats would read
  it as a number.
- The backend and the pane test the grammar against one shared fixture,
  `frontend/src/panels/modelling/__tests__/fixtures/glmExpressionGrammar.json`.
- A column whose name is not an identifier cannot appear in an expression;
  the pane offers no expression for it and explains why when Add term has
  nothing else to add. The grammar help says log and other transforms
  belong in an upstream Polars node.

### Interaction resolution

A column's main effects are its native term and every encoding term whose
`variable` names it. A product factor resolves in this order:

1. An override is used as written, after its type, parameters, and dtype
   class are validated.
2. A column with exactly one main effect inherits it when interactions can
   honour it: `linear` or `bs` without `monotonicity`, `ns`, `categorical`
   without `levels` or `reference`, or `target_encoding`. RustyStats rejects
   monotone fixed-df splines and silently drops monotonicity, levels, and
   references inside interactions, so `ms`, monotone `linear` or `bs`,
   level-restricted categoricals, and frequency encoding need an explicit
   slot fit, and the error names the reason.
3. A column with several main effects needs an explicit slot fit.
4. A column with no main effect uses its dtype default.

After every card is resolved, independent of card order: Product target
encoding keeps its single-encoded-factor and Linear-partner rule; Include
main effects materialises, for each column without a main effect, the one
spec its include-main cards agree on, and cards that disagree are rejected
together with guidance to add a main term; the mandatory target-encoded main
effect is registered with settings that agree across cards and existing
encodings; slots are checked against the effective main effects (categorical
only over categorical, linear and splines never over categorical, target
encoding never over categorical); and duplicates are checked per encoding
mode and factor set. Different `linear`, `bs`, or `ns` local fits for one
column across cards are allowed, because RustyStats builds each interaction's
local basis separately. An encoding alias counts as a main effect, so
Include main effects no longer adds a categorical main effect on top of a
named target encoding.

### Pane consistency

- Dtype classes drive native, additional, slot, and joint menus.
- A term naming an ineligible column (a role column, an unsupported dtype,
  or a column no longer upstream) and a malformed entry (no string `type`)
  are listed under Unresolved terms with the reason, the saved fit type, and
  removal; a malformed entry offers a type select that writes a valid spec.
  A malformed entry is never displayed as a linear term.
- Term and spec lookups use own-property checks, so a column named
  `constructor` or `toString` is an ordinary column.
- Interaction cards and slots keep stable keys for their lifetime in the
  editor, so removing one never moves drafts or disclosure state onto a
  neighbour.
- A malformed interaction entry renders an error card with a remove control.
  A saved factor that is no longer eligible stays selected as unavailable
  with a warning.
- Slot fit menus follow "Interaction resolution". Saved choices outside the
  menu stay visible with a warning.
- Feature rows are tagged "Main effect from Interaction N (fit)" when
  Include main effects materialises one, "Target encoding from Interaction
  N" when a Product target encoding registers one, and "Interaction only"
  only when neither applies. Conflicting materialisations are shown on the
  cards involved.
- Spline editors switch mode through one transition (Auto removes `df` and
  `knots`; Fixed writes `df` as the larger of 5 and `degree + 1` and removes
  `k` and `knots`). Numeric inputs keep a draft until a valid in-range value
  commits, so clearing a field never flips the mode or deletes a sibling
  setting.
- Categorical Advanced settings offer Reference level (blank means the first
  level in sorted order) and Levels, whose help says unlisted and unseen
  levels share the intercept; entering one clears the other.
- The Include main effects help is visible beside the checkbox. Refusal
  alerts are shown per field.
- The Target pane lists Quasi-Binomial, offers each family's links from the
  shared table, and bounds the variance power to 1 to 2. Regularisation shows
  Alpha, and in cross-validation mode folds, selection rule (minimum
  deviance or one standard error), and seed, with inline messages for the
  smooth-spline and robust-standard-error conflicts. An inline Solver
  disclosure holds maximum iterations, tolerance, and robust standard errors.
- One `modellingPanesFor(algorithm)` list drives the pane tabs and bodies.
- Cleanups: the GLM branch of `finalSelectedFeatureNames`, the unused
  `disabled` flag on fit options, the triplicated term-edit handlers, the
  duplicated expression-card markup, and the duplicated unique-name loops
  are removed, and per-row option computations are memoised.

### Route and preparation behaviour

- The evaluation preview demands only the target and the evaluation key, so
  an unfinished GLM term no longer fails the whole estimate response.
- The GLM schema gate resolves the input schema with dtypes inside an
  admitted `TRAINING_PREP` execution context, validates role columns and
  dtype classes, and the dispersion route reuses the preamble it compiled.
- One `is_glm_config` predicate serves the route helpers.
- A GLM without terms is refused when `TrainingJob` is constructed and by
  `GLMAlgorithm.fit`; the auto-term builder is removed.
- `GLMAlgorithm.glm_diagnostics` is removed: nothing called it, it passed
  `data=` where RustyStats expects `train_data=`, and a working call writes
  `analysis/diagnostics.json` into the server's working directory.
- The end-to-end GLM integration fixture uses a well-conditioned design; the
  previous spline plus squared expression on the same unscaled column
  overflowed `exp` on Linux CI.

## Planned improvements

Delivery order is `MOD-T00` → `MOD-T01` → `MOD-T02` → `MOD-T03`. The four
packages ship on one pull request; one Codex review and one Playwright run
happen after the fourth package, not per package.

### MOD-T00 — RustyStats 0.9.0 upgrade
**Why:** 0.9.0 honours interaction-local splines, which the terms design
needs, but it also redefines `offset`, raises on an unset Negative Binomial
theta, and rejects boundary Tweedie powers, and Haute's suite pins none of
the exposure semantics.

**Plan:** Pin `rustystats>=0.9.0,<0.10` in `pyproject.toml` and `uv.lock`.
In `_build_glm_builder_kwargs` and the GLM predict path of
`src/haute/modelling/_rustystats.py`, resolve the effective link (explicit
`link`, else `rustystats.formula.get_default_link(family)` at build time;
the fitted `model.link` at prediction time) and pass Haute's offset column
as `exposure=` for `log` and as `offset=` otherwise; keep the column in the
prediction frame either way. Pass `allow_extended_tweedie=True` in the
builder, because 0.9.0 rejects `var_power` of exactly 1.0 and 2.0 without
it and Haute's slider and gate text offer both. Replace
`test_unset_theta_is_the_silent_default` with a test that an unset theta
raises on 0.9.0 and update the theta gate text in `training_objective_issue`
and `trainingObjective.ts` from "would silently fit at theta=1.0" to
"RustyStats refuses to fit without it". Update the comment at
`_rustystats.py:447` and the offset sentence of `specs/modelling/low-level.md`.

**Acceptance:** Named scenarios:
`test_log_link_offset_predictions_equal_exp_of_log_exposure_plus_linear_predictor`
(Poisson with an exposure column and **no explicit `link`**, so the
canonical-link path is exercised: fitted predictions equal
`exp(log(e) + Xβ)` to 1e-9, and doubling `e` doubles the prediction);
`test_explicit_identity_link_on_poisson_treats_offset_additively` (the
explicit-link path); `test_identity_link_offset_is_additive` (Gaussian:
prediction shifts by exactly the offset);
`test_predict_uses_fitted_model_link_for_offset_mapping`;
`test_dispersion_estimate_uses_exposure_for_log_link` (the dispersion
builder receives `exposure=`, not `offset=`);
`test_exported_glm_script_keeps_exposure_semantics`;
`test_unset_negbinomial_theta_raises_on_rustystats`;
`test_tweedie_boundary_powers_1_and_2_fit_with_extended_tweedie_enabled`.
The full backend suite and the frontend unit suite pass on 0.9.0.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_rustystats.py` (`_build_glm_builder_kwargs`,
`GLMAlgorithm.predict`); `tests/test_rustystats_algorithm.py`
(`TestNegBinomialThetaThreading`, the exposure-as-offset fit at line 177);
`frontend/src/panels/modelling/OffsetFieldLabel.tsx` (`OFFSET_HELP`);
`.venv/Lib/site-packages/rustystats/formula.py` (`glm_dict` docstring,
`_process_offset`).

### MOD-T01 — GLM backend term contract
**Why:** The backend assumes every term key is a column, duplicates main
effects for partially overlapping interactions, ignores per-factor
interaction fits, lets CatBoost-only `exclude` and `monotone_constraints`
alter or block GLM fits, carries the unregistered `all_factors` flag, and
merges a second monotonicity source into terms.

**Plan:** Add `glm_model_columns` and `validate_glm_model_columns` to
`src/haute/modelling/_rustystats.py` and route
`_training_required_columns_by_node`, `_glm_training_term_columns`, the
`TrainingJob` evaluation-key guard and final feature contract, and the
dispersion worker's column load through them. Rewrite `_build_interactions`
per the interaction-building rules. In `_train_config.py` delete
`all_factors` from `GLM_CONFIG_KEYS` and `training_objective_issue` (whose
empty-terms message becomes "GLM config has no terms. Add a term to at least
one feature."), drop the `exclude` narrowing from `_effective_glm_params`, and
pass `exclude=[]` and `monotone_constraints=None` for GLM. Remove
`all_factors` from `_resolve_glm_terms`, remove the `fit` branch that copies
`monotone_constraints` into terms, and reject GLM jobs constructed with
`monotone_constraints`. Add `resolve_training_input_schema` to
`_training_preparation.py` (schema-only lazy build, no projection). In
`_training_lifecycle.py` pass no sink exclusions for GLM on the training
and dispersion `_execute_and_sink` calls, and call
`resolve_training_input_schema` then `validate_glm_model_columns` before
`_estimate_ram` on both paths, turning an unresolvable schema into a 422.
Add the `VALID_KEYS` subset test. Update the three spec bullets and the
two doc rows named under Test impact.

**Acceptance:** Named scenarios, each a failing test before the change:
`test_glm_model_columns_reads_expression_identifiers_and_interaction_factors`;
`test_glm_model_columns_rejects_unsupported_expression_grammar`;
`test_glm_model_columns_rejects_unknown_types_and_redirecting_keys`
(`variable` on a spline term, `interaction`, unknown term types);
`test_target_and_frequency_encoding_can_share_a_source`;
`test_encoding_alias_validates_source_instead_of_alias`;
`test_rejects_duplicate_encoding_for_one_source`;
`test_multiple_encodings_fit_save_and_score_raw_source`
(both encoding orders, raw-source-only scoring after save/load, including unseen categories);
`test_validate_glm_model_columns_rejects_unknown_column_and_column_keyed_expression`;
`test_resolve_training_input_schema_reflects_added_renamed_and_dropped_columns`
(source `x, y`; a Polars node adds `x2`, renames `y` to `y2`, and drops a
column; the resolved schema is exactly the transformed one and a GLM on
`x2` is accepted);
`test_training_route_rejects_expression_key_colliding_with_unprojected_column`
(upstream column `x_sq` unused by the model, expression key `x_sq`,
rejected before execution on both the training and dispersion routes);
`test_training_route_returns_422_when_input_schema_cannot_be_resolved`;
`test_required_columns_demand_includes_expression_and_interaction_only_columns`;
`test_build_interactions_materialises_missing_main_effects_once_and_never_duplicates`
(fixture: main `x` linear; cards `x × z` and `z × w`, both with
include_main; expected design columns exactly
`[Intercept, x, z, w, x:z, z:w]`);
`test_build_interactions_rejects_duplicate_factor_sets_in_any_order`;
`test_build_interactions_rejects_monotone_overrides_categorical_retype_and_linear_on_string`;
`test_build_interactions_rejects_inherited_monotone_spline_and_effective_target_encoding`;
`test_build_interactions_local_spline_override_produces_interaction_local_basis`
(main `bs(x, df=4)`, override `bs(df=6)`: design columns contain
`bs(x, k/4)` main columns and `c[T.*]:bs(x, k/6)` interaction columns);
`test_build_interactions_linear_override_on_spline_main_forces_linear_column`;
`test_glm_ignores_exclude_and_rejects_monotone_constraints`;
`test_training_and_dispersion_sinks_keep_excluded_glm_term_columns`;
`test_all_factors_is_gone` (grep-level assertion over `src/`, `tests/`,
`specs/`, `docs/`); and an end-to-end fit in
`tests/test_glm_integration.py` of a native term, an expression term keyed
by a non-column name, and an interaction with a materialised main effect
and a local spline, whose exported script trains identically.

**Dependencies:** MOD-T00; the interaction behaviours verified above.

**Evidence:** `src/haute/modelling/_rustystats.py`;
`src/haute/modelling/_train_config.py`;
`src/haute/routes/_training_preparation.py`;
`src/haute/routes/_training_worker.py`;
`src/haute/modelling/_training_job.py`; `tests/test_rustystats_algorithm.py`;
`tests/test_train_config_builder.py`; `tests/test_glm_integration.py`.

### MOD-T02 — GLM terms pane
**Why:** Two sections decide GLM membership, monotonicity is configured
twice, one column cannot carry more than one fit in the builder, and the
Nat. spline monotonicity toggle produces a fit-time error.

**Plan:** Add `frontend/src/panels/modelling/GLMTermsConfig.tsx`,
`TermCard.tsx`, and pure helpers in `glmTerms.ts` (dtype default spec,
per-type subset table, expression grammar check and identifier
extraction, expression anchoring, unique expression naming, model
membership, every editor transition as a pure config-to-config function).
Compose it as the GLM Features pane in `ModellingConfig.tsx`. Delete
`GLMFactorConfig.tsx` and `cleanupFeatureDependencies`; make
`CommonFeatureConfig` CatBoost-only; make `finalSelectedFeatureNames` return
columns with a term or a filled interaction factor for GLM; update the
`glm-factor-selection` message in `trainingObjective.ts`. Update the UI
spec sections named under Test impact.

**Acceptance:** Named scenarios in `glmTerms.test.ts` (one per editor
transition, asserting the exact config payload, including
`<col>_sq2` naming when `<col>_sq` exists and refusal of a name equal to a
column or another key) and in `GLMTermsConfig.test.tsx`: `shows Not in model
until Add term writes the dtype default`; `second Add term writes a uniquely
named ** 2 expression under the same row`; `native type select never offers
Expression and expression cards never offer native types`; `ns offers no
monotonicity and ms defaults to increasing`; `type switch keeps only the
subset for the new type`; `expression edit that breaks the grammar is
refused with the grammar shown`; `moving an expression's first identifier
re-anchors its card`; `Fit all with defaults adds only missing native
terms`; `Remove all terms confirms once and clears terms and interactions`;
`In model only and the count treat interaction-only columns as in the
model`; `JSON mode round-trips a non-column expression key and lists an
unresolvable one under Unresolved terms`; `the GLM pane never writes
exclude, monotone_constraints, or all_factors`. The CatBoost pane tests pass
unchanged apart from the removed GLM cases.

**Dependencies:** MOD-T01.

**Evidence:** `frontend/src/panels/modelling/CommonFeatureConfig.tsx`;
`frontend/src/panels/modelling/GLMTermsConfig.tsx`;
`frontend/src/panels/modelling/featureSelection.ts`;
`frontend/src/utils/trainingObjective.ts`;
`frontend/src/panels/__tests__/GLMComponents.test.tsx`;
`frontend/src/panels/modelling/__tests__/CommonFeatureConfig.test.tsx`.

### MOD-T03 — Interaction cards
**Why:** Interactions can only pair columns that already have a main term
and cannot choose a linear, categorical, or spline fit inside the
interaction, although RustyStats 0.9.0 honours all three.

**Plan:** Add `frontend/src/panels/modelling/GLMInteractionsConfig.tsx`
rendering beneath `GLMTermsConfig`, with slot rules and writes exactly as
in the Interactions section, reusing `TermCard` in a slot mode that offers
linear, categorical, B-spline, and Nat. spline with df and degree fields
and no monotonicity control, and slot-availability helpers in
`glmTerms.ts`. Update the interactions sentences of
`specs/frontend-modelling-optimiser-ui/low-level.md`.

**Acceptance:** Named scenarios in `GLMInteractionsConfig.test.tsx`: `Add
interaction appends two empty slots with include_main true`; `picking a
column writes factors[i] and no specs entry`; `As main term is offered only
for columns with a non-monotone native term`; `Linear and splines are
refused for string columns and categorical main terms`; `Categorical is
refused for non-categorical main terms`; `B-spline override writes type, df
and degree and never monotonicity`; `a monotone-spline main term forces an
explicit slot fit with the hint`; `choosing an override writes only
specs[col] and As main term deletes it`; `replacing a slot's column deletes
the old override`; `+ feature adds a third slot and a slot can be removed
only above two`; `Include main effects appears only while a picked column
has no native term`; `a card with one column shows the incomplete note`;
`the same column cannot be picked twice in one card`; `columns with a
target-encoding main term are not offered in slots`; `a card duplicating
another's factor set shows Duplicate interaction`; `removing a card removes
its entry`. A final Playwright pass through
`frontend/e2e/core-flows.spec.ts` configures a GLM with one native term,
one expression term, and one interaction using a local B-spline override
on a linear main term, trains it, and sees results.

**Dependencies:** MOD-T01, MOD-T02.

**Evidence:** `frontend/src/panels/modelling/glmTerms.ts`
(interaction slot rules); `src/haute/modelling/_rustystats.py`
(`_build_interactions`); `frontend/e2e/core-flows.spec.ts`.

### MOD-T04 — GLM result integrity
**Why:** Every penalised-spline, monotone, or regularised GLM failed at result
delivery because RustyStats 0.9 reports NaN standard errors for fits without
valid inference, an ill-conditioned fit can overflow `exp` in relativities,
non-log links received `exp` relativities through a fallback, the reported
selected alpha was always 0, and smoothing results were never shown.

**Plan:** Implement "Result integrity": `coefficients_table`,
`relativities`, and `fit_statistics` in `src/haute/modelling/_rustystats.py`
read `inference_status`, emit nullable inference fields, and raise a named
diagnostic error for non-finite values; add `glm_inference`,
`glm_smooth_terms`, and `glm_regularization` (replacing
`glm_regularization_path`) to the training result, worker response,
`TrainResponse`, MLflow log fields, frontend types, guards, factories, and
the Coefficients, Relativities, and Summary panes. Replace the overflowing
end-to-end fixture.

**Acceptance:** Named scenarios:
`test_monotone_glm_result_passes_the_finite_response_guard_with_null_inference`;
`test_auto_spline_glm_reports_smooth_terms_and_unavailable_inference`;
`test_cv_ridge_glm_reports_the_selected_alpha_and_folds`;
`test_relativity_overflow_is_a_diagnostic_error_not_a_job_failure`;
`test_identity_link_has_no_relativities`;
`test_singular_design_reports_invalid_inference`; and frontend tests that
the Coefficients pane shows the reason and dashes for invalid inference,
that Relativities omit missing bounds, and that the Summary lists smooth
terms and the regularisation block. The end-to-end integration test passes
on Linux CI.

**Dependencies:** MOD-T00, MOD-T01.

**Evidence:** `src/haute/modelling/_rustystats.py` (`coefficients_table`,
`relativities`, `fit_statistics`); `src/haute/modelling/_training_job.py`
(GLM diagnostics block); `src/haute/routes/_training_worker.py`
(`_assert_json_finite`); `frontend/src/panels/modelling/GLMCoefficientsTab.tsx`;
`tests/test_glm_integration.py`.

### MOD-T05 — Regularisation, solver, and family controls
**Why:** A configured alpha was silently replaced by a cross-validated one,
cross-validation was unseeded and not reproducible, regularisation with
automatic splines either raised inside RustyStats or silently became a
smoothing parameter, the link table offered links RustyStats 0.9.0 rejects,
Tweedie accepted any power once extended support was switched on, theta was
dropped when a config was saved, and robust standard errors and solver
controls were unavailable.

**Plan:** Implement "Regularisation and solver controls" and "Families,
links, and dispersion parameters": fixed and cross-validation fit kwargs in
`GLMAlgorithm.fit`; `cv_folds`, `cv_selection`, `cv_seed`, `max_iter`, `tol`,
and `robust_standard_errors` in `GLM_CONFIG_KEYS`, the modelling config
type, the cache classification, the objective gate, and the frontend gate;
`GLM_FAMILY_LINKS` shared by both route validators and the Target pane;
quasibinomial; the Tweedie range; and the Regularisation and Solver controls
in the Target pane.

**Acceptance:** Named scenarios:
`test_fixed_alpha_reaches_rustystats_without_cross_validation`;
`test_cross_validation_is_seeded_and_reproducible`;
`test_regularization_with_penalised_spline_is_refused`;
`test_robust_standard_errors_use_hc_statistics`;
`test_robust_standard_errors_with_regularization_are_refused`;
`test_unsupported_links_and_inverse_gaussian_are_refused`;
`test_quasibinomial_fits_with_logit_link`;
`test_tweedie_power_outside_one_to_two_is_refused`;
`test_theta_and_glm_controls_survive_a_sidecar_write`;
`test_family_link_table_matches_frontend_and_rustystats_default_links`; and
frontend tests for the regularisation, cross-validation, and solver controls
and their gate messages.

**Dependencies:** MOD-T04.

**Evidence:** `src/haute/modelling/_rustystats.py` (`GLMAlgorithm.fit`);
`src/haute/modelling/_train_config.py`; `src/haute/_types.py`
(`ModellingConfig`); `src/haute/_cache.py`;
`src/haute/routes/_training_preparation.py` and
`src/haute/routes/_training_evaluation.py` (`_VALID_GLM_LINKS`);
`frontend/src/panels/modelling/GLMRegularizationConfig.tsx`;
`frontend/src/panels/modelling/GLMTargetConfig.tsx`.

### MOD-T06 — Offsets across GLM and CatBoost
**Why:** Log-link GLMs record their offset on `_exposure_spec`, which the
native loader and scorer never read, so saved models lost their offset
contract; CatBoost applied offsets verbatim while the shared help promises a
multiplier; and non-positive exposure failed inside RustyStats with an
unclassified error.

**Plan:** Implement "Offsets across GLM and CatBoost": one RustyStats offset
accessor in `_mlflow_io.py` used by `_model_scorer.py`; the CatBoost
`haute_offset_link` metadata and one baseline transform used by training,
prediction, diagnostics, explainability, and scoring; and the positive
exposure check in training preparation and log-link scoring.

**Acceptance:** Named scenarios:
`test_loaded_log_link_glm_reports_its_exposure_column`;
`test_catboost_poisson_offset_is_a_multiplier`;
`test_catboost_rmse_offset_is_additive`;
`test_catboost_model_without_offset_link_metadata_is_refused`;
`test_non_positive_log_link_offset_is_refused_before_fitting`; and
`test_scoring_refuses_non_positive_log_link_exposure`.

**Dependencies:** MOD-T00.

**Evidence:** `src/haute/_mlflow_io.py` (`_load_rustystats_model`,
`_catboost_offset_column`); `src/haute/_model_scorer.py`
(`_model_offset_column`, `_catboost_baseline_pool`);
`src/haute/modelling/_algorithms.py` (`_build_pool`,
`CATBOOST_OFFSET_METADATA_KEY`); `src/haute/modelling/_training_job.py`;
`frontend/src/panels/modelling/OffsetFieldLabel.tsx`.

### MOD-T07 — GLM term contract and interaction resolution
**Why:** Inherited `levels` and monotonicity were silently dropped inside
interactions, a named target encoding was invisible to interaction
resolution and produced a rank-deficient design, target encoding over a
categorical main effect was exactly collinear, the fitted main effect
depended on interaction-card order, backend and pane classified Boolean,
Date, and Enum columns differently, invalid parameters were silently
reinterpreted by RustyStats, terms on role columns passed the route gate,
malformed interaction entries were split into characters or escaped as
server errors, and the expression grammar disagreed between the backend and
the pane for non-ASCII names.

**Plan:** Implement "Column types", "Term parameter contract", "Expression
grammar", and "Interaction resolution" in `src/haute/modelling/_glm_terms.py`
and `_build_interactions`; translate `reference` and check observed levels
at fit; pass dtype classes from the training frame and the unprojected
schema; refuse role columns; share the CatBoost-only lever list and one
`is_glm_config` predicate; remove the auto-term builder and
`glm_diagnostics`; and implement the route items of "Route and preparation
behaviour".

**Acceptance:** Named scenarios:
`test_inherited_levels_or_monotonicity_require_an_explicit_slot_fit`;
`test_named_target_encoding_counts_as_a_main_effect`;
`test_target_encoding_slot_over_categorical_main_is_refused`;
`test_materialised_main_effect_is_independent_of_card_order`;
`test_different_local_splines_for_one_column_across_cards_are_allowed`;
`test_dtype_classes_drive_defaults_and_allowed_fits`;
`test_integer_column_can_be_categorical_and_target_encoded`;
`test_unsupported_dtype_terms_are_refused`;
`test_term_parameters_outside_the_contract_are_refused`;
`test_reference_level_translates_to_levels_and_refuses_unobserved_labels`;
`test_role_columns_cannot_be_terms_or_factors`;
`test_malformed_interaction_entries_are_validation_errors`;
`test_expression_grammar_matches_the_shared_fixture`;
`test_glm_refuses_catboost_only_levers_and_empty_terms`;
`test_evaluation_preview_ignores_unfinished_terms`; and
`test_dispersion_frame_includes_an_interaction_only_materialised_column`.

**Dependencies:** MOD-T01.

**Evidence:** `src/haute/modelling/_glm_terms.py`;
`src/haute/modelling/_rustystats.py` (`_build_interactions`,
`_resolve_interaction_factor_spec`); `src/haute/modelling/_training_job.py`
(`_derive_features`); `src/haute/routes/_training_lifecycle.py`
(`evaluation_preview`, `_validate_glm_input_schema`);
`src/haute/routes/_training_worker.py`.

### MOD-T08 — GLM pane consistency
**Why:** The pane hid terms on role or missing columns, crashed on malformed
interaction entries, treated prototype-named columns as existing terms,
moved drafts between cards after a removal, labelled materialised main
effects "Interaction only", flipped spline mode while a number was edited,
offered fits the backend refused, had no reference-level control, and
carried duplicated and dead code.

**Plan:** Implement "Pane consistency" in `glmTerms.ts`, `TermCard.tsx`,
`GLMTermsConfig.tsx`, `GLMInteractionsConfig.tsx`, `NodePanel.tsx`,
`ModellingConfig.tsx`, and `featureSelection.ts`, mirroring MOD-T07's dtype
classes, parameter bounds, grammar fixture, and resolution rules as pure
helpers with exact-payload tests.

**Acceptance:** Named scenarios in `glmTerms.test.ts`,
`GLMTermsConfig.test.tsx`, `GLMInteractionsConfig.test.tsx`, and
`TermCard.test.tsx`: unresolved role, missing, unsupported, and malformed
terms are listed with reasons and removable; a `constructor` column behaves
as an ordinary column; removing an interaction keeps the remaining card's
draft; a malformed interaction renders an error card; an unavailable saved
factor stays selected with a warning; slot menus match the resolution
rules; materialised main-effect tags; clearing a spline df field keeps Fixed
mode and sibling settings; reference level and levels are exclusive; integer
columns offer categorical and encoding fits; non-identifier columns offer no
expression; and the expression grammar fixture passes. The browser flow
trains a GLM with an automatic spline and sees its coefficients.

**Dependencies:** MOD-T05, MOD-T07.

**Evidence:** `frontend/src/panels/modelling/glmTerms.ts`;
`frontend/src/panels/modelling/TermCard.tsx`;
`frontend/src/panels/modelling/GLMTermsConfig.tsx`;
`frontend/src/panels/modelling/GLMInteractionsConfig.tsx`;
`frontend/src/panels/NodePanel.tsx`; `frontend/e2e/core-flows.spec.ts`.

## Model-family expansion (proposed 22 September 2026; revised 23 September 2026)

### Outcome and scope

Add three first-class modelling choices: **XGBoost**, **LightGBM**, and
**Explainable Boosting Machine (EBM), using InterpretML**. Each must work from
configuration through training, evaluation, cancellation, saved results,
model export, MLflow candidate logging, Model Score, trace, and deployment.
A selector entry or a successful in-memory fit alone does not complete a family.

Keep `catboost` and `glm` as the existing algorithm IDs. Add `xgboost`,
`lightgbm`, and `ebm`; retain `rustystats` as the GLM's scoring flavor.
EBM is a separate additive model family, not an additional RustyStats GLM
family or a GLM term encoding.

This plan is written against `main` at `c6fbd160` (PR #228 merged), which
contains the GLM terms work and the evaluation and training-configuration
changes the first draft described as in progress. The [engine probes](mod-f00-engine-probes.md) (23 September
2026) settled the pre-implementation gates, and the owning specifications now
hold approved change contracts for every package below; those contracts, not
this plan, are the implementation authority.

The packages use the `MOD-F` prefix. `MOD-M05` and `MOD-M09` are retired
package IDs that the modelling specification still cites for the CatBoost
contiguity benchmark and the monotonicity decision; package IDs are stable,
so this plan does not reuse the `MOD-M` series.

### Decisions (23 September 2026)

The owner accepted these recommendations after the first Claude and Codex
reviews:

1. **EBM never stops early; its fits use an explicit round budget.**
   The first recommendation was to stop EBM early on Haute's validation rows
   through an explicit `bags` assignment, gated on a probe that validation
   targets influence nothing but stopping. The [engine probes](mod-f00-engine-probes.md) failed that gate for
   every objective (`rmse`, `poisson_deviance`, `gamma_deviance`,
   `tweedie_deviance`, `log_loss`): with stopping disabled, perturbing only
   validation targets left interaction selection and term scores unchanged but
   moved the intercept and every prediction. The agreed fallback therefore
   applies throughout: EBM selection fits use training-partition rows only and
   the final development refit uses development rows, always with
   `outer_bags=1` and an explicit `max_rounds`; no early stopping is offered in
   the first release.
2. **One binary-classification contract for every family, CatBoost included.**
   The positive-class mapping, threshold and label rule in
   [Data and prediction contracts](#data-and-prediction-contracts) replace
   CatBoost's implicit class ordering. Haute has no users to migrate, so no
   compatibility path is kept.
3. **LightGBM starts after the XGBoost slice is complete.** MOD-F03 is gated on
   the XGBoost slice's acceptance. At that point the owner confirms whether LightGBM
   remains in the first release or follows it; the shared seams must not
   assume it.
4. **Packages are vertical family slices.** Each family package delivers its
   own adapter, tuning and refit policy, artifacts, MLflow, codegen,
   deployment, UI, and explanations together. The shared seams (algorithm
   descriptors, the version-2 feature contract with its model identity, the
   binary-classification rule, the shared MLflow pyfunc, thread allotment and
   fit evidence) are in place on `main`; see the modelling specification's
   "Model families" section.
5. **XGBoost is capped below 3.3 to keep Python 3.11.** XGBoost 3.3 and later
   require Python 3.12, while Haute supports 3.11 to 3.13 and its Databricks
   Model Serving deployment builds Python 3.11.11. Capping at 3.2 keeps XGBoost
   on every supported Python; disabling it on 3.11 was rejected because that
   would remove it from the Databricks serving path. The cap lifts when Haute
   drops Python 3.11.
6. **Extend existing concepts; do not build parallel ones.** The loss is the
   existing `loss_function`/`variance_power` vocabulary, not a new
   `objective` field; per-model prediction metadata extends the existing
   feature contract, not a new sidecar artifact; tuning keeps its existing
   user-authored search spaces; tree refits keep the existing
   `validation_weighted_tree_count` rule; prediction parity uses the existing
   `_prediction_tolerance`; and the GLM MLflow pyfunc generalises into the one
   wrapper for native models. A new concept enters the plan only where no
   existing one carries the meaning.

### What the repository already provides

| Existing surface | Reuse and specific extension required |
|---|---|
| `src/haute/modelling/_algorithms.py` | `BaseAlgorithm`, `FitResult`, and `ALGORITHM_REGISTRY` exist. The interface includes fit, prediction, importance, and save, but does not describe all capabilities. |
| `src/haute/modelling/_training_job.py` | The lifecycle, partitioned input, evaluation, diagnostic batches, and artifact ownership are reusable. `_train_model` currently treats every non-GLM as CatBoost (`is_glm` at line 1984 decides whether a CatBoost `loss_function` is injected, and the non-GLM branch builds CatBoost pools). `_save_model` falls back to a `.model` suffix for an unknown algorithm (line 2524), so a family registered before its suffix would save under a wrong name without error. Both must change before registering another algorithm. |
| `src/haute/modelling/_train_config.py` | Shared GUI/script validation and kwargs assembly exist. Objective gates, metrics, feature controls, and offset-link resolution currently distinguish GLM from CatBoost. |
| `src/haute/routes/_training_preparation.py` | Column demand, snapshot seeding, materialisation, and resource admission exist. Explicit CatBoost projection and VRAM assumptions need capability-aware dispatch. |
| `src/haute/routes/_training_artifacts.py` | Parent-side publication validates the worker manifest. Its validator (line 204) accepts only the exact model/feature-contract/evaluation set with an optional complete tuning set. Because new prediction metadata extends the feature contract rather than adding an artifact kind, this validator and `TrainingArtifactSet` stay unchanged. |
| `src/haute/modelling/_evaluation.py`, `src/haute/modelling/_tuning.py` | Reproducible evaluation plans and bounded Optuna tuning exist. Search spaces are user-authored conditional categorical choices (`suggest_parameters`) with no engine knowledge; the only CatBoost coupling is the version-1 gate (`_tuning.py:301`) and the refit, which writes `validation_weighted_tree_count` into CatBoost's `iterations` key and records `final_tree_count`. Tuning requires single or cross-validation. CatBoost's `thread_count` appears only in tuning's reserved-parameter list; there is no thread allotment today. |
| `src/haute/modelling/_feature_contract.py` | Every saved model already publishes a hashed contract with ordered features, feature types, categorical features and ordered levels (including null), target name/type, task and offset column; scoring, MLflow logging and caches already consume it. It is the home for the new per-model prediction metadata. |
| `src/haute/modelling/_algorithms.py` loss vocabulary | `REGRESSION_LOSSES` (`RMSE`, `MAE`, `Poisson`, `Tweedie`), `CLASSIFICATION_LOSSES` (`Logloss`, `CrossEntropy`), `variance_power`, and `resolve_loss_function` already form an engine-neutral user-facing loss vocabulary that `trainingObjective.ts` validates; only its translation targets CatBoost. |
| `src/haute/_model_explainability.py` `prediction_tolerance`, `src/haute/modelling/_native_pyfunc.py` | The shared prediction-parity tolerance (`max(1e-6, 1e-6 * abs(v))`, with a named float32 bound) and the shared MLflow pyfunc over a model-plus-contract package. |
| `src/haute/_model_flavors.py`, `src/haute/_mlflow_io.py`, `src/haute/_model_scorer.py` | Explicit flavors, loaders, input preparation, batch/eager scoring, feature checks, and caches exist. Native discovery and dispatch currently support `.cbm` and `.rsglm`, alongside generic pyfunc models. `_positive_class_proba_vector` owns probability-shape semantics; there is no explicit positive-class setting anywhere in `src/`. |
| `src/haute/modelling/_model_export.py`, `src/haute/modelling/_candidate_run.py`, `src/haute/modelling/_mlflow_log.py` | Native suffix selection, owned artifacts, training identity, and common candidate logging exist. Suffixes, signature logging, evidence files, and identity fields need extension. |
| `src/haute/_model_explainability.py` | CatBoost SHAP and RustyStats contributions already reconcile contributions against predictions. New engines must meet the same standard. |
| `frontend/src/panels/ModellingConfig.tsx` and `frontend/src/panels/modelling/` | Gateway, panes, feature cards, target controls, JSON parameters, results, and export flows exist. Several unions, pane lists, objective gates, and suffix maps contain exactly two algorithms. |
| `src/haute/deploy/` | Shared scoring and artifact bundling exist; local native artifacts and deployment dependency generation require explicit support for the new formats. |

Two existing contracts especially constrain this work: classification scoring
emits a class prediction plus `<output_col>_proba` for the binary positive-class
probability; multiclass probabilities are explicitly rejected. External
pickle/joblib loading uses exact vetted globals/classes in
`src/haute/_sandbox.py`, not a trusted-package prefix. The current allowlist
already names some XGBoost/LightGBM sklearn classes; the native adapters below
use boosters, not those wrappers, so that entry is not evidence of support.

### Recommended first-release capability matrix

The following is the proposed supported subset, not a catalogue of every
upstream library feature. A capability appears in the UI only after its
complete lifecycle is covered.

| Capability | XGBoost | LightGBM | EBM |
|---|---|---|---|
| CPU regression | Squared error, absolute error, Poisson, Gamma, Tweedie | Squared error, absolute error, Poisson, Gamma, Tweedie | RMSE, Poisson, Gamma, Tweedie |
| Classification | Binary logistic | Binary logistic | Binary log loss |
| Mixed numeric/categorical features | Native categorical tree training with fixed encoding | Native categorical tree training with fixed encoding | Native nominal/continuous terms |
| Sample weights | Yes | Yes | Yes |
| Regression offset/exposure | Explicit raw-margin baseline | Explicit initial score plus scoring-time baseline | Explicit initial score at fit and predict |
| Numeric monotone constraints | Supported combinations only | Supported combinations only | Main-effect constraints; exclude interactions involving constrained features |
| Shared evaluation and bounded tuning | Yes | Yes | Yes |
| Early stopping on Haute's validation partition | Yes | Yes | No; explicit `max_rounds` on training rows only (decision 1) |
| Local explanations | Native tree contributions | Native tree contributions | Exact additive term contributions |
| Distinctive results | Gain importance and contribution summary | Gain/split importance and contribution summary | Shape functions, term importance, pairwise interaction surfaces |
| GPU in initial release | No | No | No GPU option |

Multiclass/multioutput, ranking, survival, custom Python objectives/callbacks,
distributed training, warm starts, DART/alternative boosters, arbitrary imported
model formats, automatic cross-family model selection, EBM model editing,
differential privacy, and ONNX are outside the first release. They must be
rejected when requested through raw config as well as absent from controls.
Existing CatBoost and RustyStats capabilities remain as currently specified,
except that CatBoost adopts the shared binary-classification contract.

All three new engines are ordinary installed choices, consistent with the
existing core CatBoost/RustyStats dependencies. The [engine probes](mod-f00-engine-probes.md) fixed the
distributions: `xgboost-cpu>=3.2,<3.3` on Linux and Windows (5.6 MB on Linux,
with no NVIDIA dependency, where the full `xgboost` wheel is 131.7 MB and pulls
`nvidia-nccl-cu12`) and `xgboost>=3.2,<3.3` on macOS, which has no CPU-only
wheel; `lightgbm` 4.7; and `interpret-core` 0.7.8. The two XGBoost
distributions install the same import package, so an environment must never
hold both. The macOS XGBoost and LightGBM wheels bundle no OpenMP runtime and
load `@rpath/libomp.dylib`, so macOS installs, the macOS CI job and any macOS
image need Homebrew `libomp`. InterpretML documents `interpret-core` as
sufficient for EBM fitting, prediction, serialization, and explanations.
MOD-F06 decides how GPU builds are obtained alongside the CPU distribution.
[InterpretML deployment guide](https://interpret.ml/docs/deployment-guide.html),
[XGBoost installation](https://xgboost.readthedocs.io/en/stable/install.html)

### Configuration and extension design

Extend the existing registry with a small typed algorithm descriptor. It should
identify the implementation, supported tasks and Haute losses, allowed feature
controls, raw-`params` allowlist, native round key, device support,
stopping/refit policy, diagnostic methods, serialization format and suffix,
and runtime requirements. Keep import-heavy engine implementations lazy. Do not introduce
a dynamically loaded plugin system, duplicate training orchestrator, or
universal UI schema framework. Model-file suffixes come from the descriptor;
an algorithm without one raises instead of saving as `.model`.

Use descriptors to drive validation and narrow dispatch in existing owners.
Deliver capability metadata to the frontend as a generated, checked-in
fixture rather than a new route: a backend test serializes the descriptors and
fails when the committed frontend fixture differs, and frontend code types its
unions from that fixture. The engines are core dependencies, so an import
failure is an installation defect: training fails with an explanatory error
naming the missing package and never quietly selects CatBoost instead.

Keep shared top-level roles and controls: target, task, weight, offset,
feature selection, evaluation, metrics, and tuning. The loss is the existing
`loss_function` setting and its Haute vocabulary, shared by every tree and EBM
family, and the UI never shows
library jargon. Add `Gamma` to `REGRESSION_LOSSES` (and its frontend
validation); CatBoost's own `Gamma` support is confirmed or the name is
rejected for CatBoost through its descriptor. Each adapter translates the Haute
name to its native objective privately, extending `resolve_loss_function`'s
role rather than adding a parallel field. GLM keeps its `family`/`link`
contract. Every loss resolves to one typed description (target domain,
prediction link, metric defaults, baseline semantics) used by gates, metrics
and offsets. An explicit loss remains required for the new families; a
library default must not choose the meaning of a model.

Keep `variance_power` explicit for Tweedie and validate `1 < p < 2` for these
new engines. The adapter-internal translation is:

| Haute `loss_function` | XGBoost | LightGBM | EBM |
|---|---|---|---|
| `RMSE` | `reg:squarederror` | `regression` | `rmse` |
| `MAE` | `reg:absoluteerror` | `regression_l1` | Outside initial subset |
| `Poisson` | `count:poisson` | `poisson` | `poisson_deviance` |
| `Gamma` | `reg:gamma` | `gamma` | `gamma_deviance` |
| `Tweedie` + `variance_power` | `reg:tweedie` + `tweedie_variance_power` | `tweedie` + `tweedie_variance_power` | `tweedie_deviance:variance_power=...` |
| `Logloss` | `binary:logistic` | `binary` | `log_loss` |

`CrossEntropy` (probabilistic targets) stays CatBoost-only and each new
descriptor rejects it. Names and supported values must be checked against the
versions pinned by the engine probes.
[XGBoost parameters](https://xgboost.readthedocs.io/en/stable/parameter.html),
[LightGBM parameters](https://lightgbm.readthedocs.io/en/stable/Parameters.html),
[EBM regressor API](https://interpret.ml/docs/python/api/ExplainableBoostingRegressor.html),
[EBM classifier API](https://interpret.ml/docs/python/api/ExplainableBoostingClassifier.html)

Resolve reported metric defaults from the loss meaning: RMSE/MAE for their
matching error losses, Poisson/Tweedie deviance with the configured power,
Gamma deviance for Gamma, and AUC/log loss for binary classification. Add
weighted Gamma deviance to the existing metric registry and frontend options
with explicit target/prediction domain checks. Preserve the current Lorenz
Gini definition; it is not automatically the classification `2 * AUC - 1`.
Users can choose additional compatible metrics without changing the fit loss.

Raw `params` must have a validated, documented allowlist per family. Reject
unknown keys and conflicting aliases, including keys reserved by Haute:
native objective and loss keys, feature names/order, categorical encoding, device/thread limits,
random seeds, callbacks, validation data, bag assignment, and baseline arrays.
Engine warnings about ignored parameters must not become successful runs.
Store configured and effective parameters separately in candidate evidence so
native defaults and resolved budgets can be reproduced.

Preserve CatBoost's current feature-weight meaning only for CatBoost.
XGBoost sampling weights and LightGBM split penalties are different controls;
do not reuse `feature_weights` under the same label for them. GLM terms remain
GLM-only. EBM interactions and feature types are their own validated config,
with pairwise interactions specified by feature names and resolved to indices
after feature order is fixed.

### Data and prediction contracts

The decisive invariant is that training evaluation, reloaded artifacts,
GUI preview, batch scoring, generated scripts, MLflow serving, and deployment
produce the same values for the same model and rows within the tolerance in
item 7. Use one prediction adapter per engine for these paths.

1. **Column roles and order.** Extend existing demand planning to project only
   features plus target, weight, offset, split/group/time and ID roles where
   needed. Role columns never enter model features accidentally. Resolve an
   ordered feature list once; persist any native safe-name mapping if a library
   restricts feature-name characters. Detect collisions rather than renaming
   ambiguously. Specify accepted Boolean, numeric, String, Categorical and Enum
   inputs; reject dates, nested types, unsupported decimals and infinities with
   instructions to transform them upstream. Integer-as-category requires an
   explicit upstream cast to a supported categorical dtype; never infer it
   from cardinality.
2. **Categoricals.** Fit inferred vocabularies/encodings using each training
   partition only. An explicit authored categorical domain is schema, not a
   learned full-dataset vocabulary. Persist domain order and null semantics;
   never reconstruct category codes independently in each score batch. Retain
   Haute's explicit domain validation. Initially reject values outside the
   fitted/declared domain with the feature and offending values identified;
   never silently turn an unseen category into a missing value. Validation-fold
   failures must explain that users can declare the domain upstream or change
   the split. Native missing values use a separate representation, with no
   collision between null and a literal string such as `"__missing__"`.
3. **Target and weight gates.** Validate the objective's response domain before
   launching a native fit. Validate finite nonnegative weights with positive
   total mass; enforce existing null-target policy and report removed rows.
   Apply rules identically in HTTP, direct `TrainingJob`, and script export.
   Preserve the current distinction between a regression proportion response
   and discrete classification labels.
4. **Binary classes (all families, CatBoost included).** Require exactly two
   training classes; persist their typed mapping and positive class. Boolean
   and 0/1 targets use `true`/`1` as positive; any other two-label target
   requires an explicit `positive_class` in config. Reject unknown labels,
   single-class fits, and multiclass input early. Metrics consume encoded
   labels and positive-class probabilities. Model Score emits the original-label
   `prediction` and the positive-class `prediction_proba`; the label is derived
   from the probability, never from a separate native class call:
   `prediction` is the positive class exactly when `prediction_proba > 0.5`,
   so a probability of exactly `0.5` yields the negative class (matching
   argmax over `[negative, positive]`). Keep raw margins, probabilities, and
   labels distinct internally.
5. **Offsets.** For supported regression objectives, preserve Haute's meaning:
   identity-link offsets add on the raw scale; log-link offsets are strictly
   positive exposure multipliers transformed with `log`
   (`_algorithms.offset_baseline`). Persist the column, link and transform.
   Train, validate, explain, save/load and score with it exactly once.
   Missing, null, non-finite or nonpositive log exposures fail. Initially
   reject classification offsets for the new families; log-odds offsets need a
   separate user-facing contract. A weight is never substituted for exposure,
   nor is a target silently divided by it.
6. **Finite outputs and batches.** Check row count, order, prediction shape,
   probability bounds and finite results; empty batches return correctly typed
   empty outputs without calling native predictors. Preserve collision checks
   for output names and original input columns. Avoid mutating cached models
   during predictions; prove supported concurrent scoring or serialize access
   explicitly if an engine requires it.
7. **Numeric tolerance.** Compare every path against the in-memory evaluation
   prediction of the same fitted model, as float64. Response-scale predictions
   and probabilities use the existing `_prediction_tolerance`
   (`max(1e-6, 1e-6 * |b|)`), which the CatBoost and RustyStats explanation
   checks already apply; move it to a shared home rather than restating it.
   The one new bound is for raw margins reconstructed from XGBoost
   contributions, which are accumulated in float32:
   `|a - b| <= 1e-5 * max(1, |b|)`, taken as a named parameter of the same
   helper. Binary labels must
   match exactly; parity fixtures exclude rows whose probability lies within
   `1e-6` of `0.5`. A save/reload in the same process must be bit-identical.
   The native library's own `predict` on the same rows is the independent
   oracle for the same bounds.

XGBoost supports categorical recoding, but its documentation still requires
care around unseen categories. LightGBM's category codes have integer/range
requirements and its native predictor can treat unseen values as missing.
Haute's explicit contract must run before these library behaviors.
[XGBoost categorical data](https://xgboost.readthedocs.io/en/stable/tutorials/categorical.html),
[LightGBM categorical data](https://lightgbm.readthedocs.io/en/stable/Advanced-Topics.html)

### Training adapters, evaluation, and tuning

Keep partitioning, evaluation plans, job state, artifacts and diagnostics in
the existing orchestration. Move native data construction behind an adapter
preparation/fit boundary: it receives projected train/validation inputs and
returns an owned prepared representation. Release a raw training frame after
creating its native representation and before materialising validation where
the engine permits it. Retain CatBoost's current allocation order rather than
flattening every engine into a memory-heavy generic pandas fit.

Use XGBoost's native `DMatrix`/histogram training and LightGBM's native `Dataset`
and training API for direct control over baselines, callbacks and saved boosters.
Use InterpretML's regressor/classifier APIs for EBM. Prototype ordinary CPU
datasets first; adopt quantile matrices or direct Arrow/Polars paths only after
they preserve the same contract and demonstrate a material memory benefit.

For XGBoost, supply `base_margin` during training and prediction when an offset
is configured. Select the best boosting range explicitly or save a model
trimmed to it; do not depend on sklearn wrapper behavior after loading a native
booster. The native and sklearn prediction APIs differ in how they use early
stopping. For LightGBM, supply training/validation `init_score`, and specify the
selected iteration count for prediction and persistence. Its scoring adapter
must add the row baseline to the raw tree score before the inverse link;
verify this with and without serialization to prevent omission or double use.
[XGBoost prediction](https://xgboost.readthedocs.io/en/stable/prediction.html),
[XGBoost base margins](https://xgboost.readthedocs.io/en/stable/tutorials/intercept.html),
[LightGBM prediction and persistence](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.Booster.html)

Preserve the existing distinction between model-selection evidence and the
untouched final test. All families consume the same saved evaluation plan,
including temporal/grouped/fold constraints. Category inference, EBM binning,
interaction selection, parameter selection and stopping must not inspect the
final test. Native validation metrics and Haute's weighted selection metric
must be labelled separately if they differ; persist metric direction and the
stopping criterion. Undefined fold metrics, including AUC on one class, need
the existing explicit failure/evidence behavior, never a fabricated score.

EBM (decision 1) never stops early. Every EBM selection fit, including tuning
trials, uses training-partition rows only; the final development refit uses the
development rows; no EBM fit sees final-test rows. Every passed row is in bag `1`,
with
`outer_bags=1`, `early_stopping_rounds=0` and an explicit `max_rounds`
configured or tuned as an ordinary hyperparameter; the refit reuses the
winning `max_rounds`, so selection evaluates exactly the budget the final model
receives. Record EBM's native `best_iteration_` exactly as reported and label it
as term-update steps: in 0.7.8 it counts executed term updates, while
`max_rounds` counts rounds that each contain several term updates, so it is
neither a tree count nor a round budget and is never converted into one.
Offsets reach EBM through `init_score` at fit and predict.
[EBM regressor API](https://interpret.ml/docs/python/api/ExplainableBoostingRegressor.html),
[EBM boosting implementation](https://github.com/interpretml/interpret/blob/v0.7.8/python/interpret-core/interpret/glassbox/_ebm/_boost.py)

**No-validation behavior.** When the evaluation plan has no validation
partition, every family trains once on the training partition with no early
stopping: trees use the configured round count as a ceiling, and EBM fits with
every passed row in bag `1`, `early_stopping_rounds=0` and its configured
`max_rounds`. The candidate evidence records the configured ceiling and the
actual fitted rounds read from the native model separately, with a stopping
reason of `none` when they are equal and `native_exhaustion` when the engine
stopped early because no further split satisfied its constraints (LightGBM can
do this with constant features or restrictive leaf sizes; see
[its boosting loop](https://github.com/microsoft/LightGBM/blob/v4.6.0/src/boosting/gbdt.cpp)).
Native exhaustion is never reported as validation stopping. The same
configured/actual/reason triple is recorded when validation stopping applies,
with reason `validation`. Tuning keeps its existing requirement of single or
cross-validation and rejects a plan without it. This matches CatBoost's current
behavior, which builds no evaluation pool when `n_validation == 0`.

Reuse the existing tuning machinery unchanged in shape: search spaces stay
user-authored conditional categorical choices resolved by
`suggest_parameters`. Replace the CatBoost-only gate with the family
descriptor: each searched name must be in that family's raw-`params`
allowlist and not a reserved key, so no per-family search-space definitions
are added. EBM studies may search `max_rounds` like any other parameter. The
loss, task, category policy, evaluation split and offset semantics remain
fixed for a study. Persist seed, sampler version, effective
trial parameters, fit counts and failures. Trials run sequentially inside the
worker. Enforce total fits, rounds and resource limits before starting work.

Tree refits reuse the existing rule. `validation_weighted_tree_count` takes
zero-based best iterations and adds one itself (`_tuning.py:565`), so every
adapter passes a zero-based index into the unchanged helper: XGBoost's
`best_iteration` as reported (already zero-based), LightGBM's `best_iteration`
minus one (it is already a one-based count), and CatBoost's as today. No
adapter normalises with `+1` before the call. XGBoost and LightGBM record
`final_tree_count` exactly as CatBoost does; the only generalisation is that
the descriptor names the native round key the count is written to
(`iterations`, `num_boost_round`) in place of the hard-coded `iterations`
check. Verify each convention in the pinned API, and add a fixture per tree
family in which validation selects exactly three rounds and the refit budget
is exactly three. EBM is the one exception:
it refits on the development rows with the winning settings, every row in bag
`1`, no early stopping, and the winning explicit `max_rounds`, so its tuning
report omits `final_tree_count` (made optional for descriptors whose refit
policy is a fixed budget) and never derives a budget from `best_iteration_`.
Final-test rows never choose a refit budget. Library model
continuation and LightGBM's separate native leaf-refit API are not Haute's
final retraining.

### Artifacts, model loading, and deployment

Prefer native inference artifacts for tree models: XGBoost UBJSON (`.ubj`) and
LightGBM's saved model text under an unambiguous Haute suffix (`.lgbm`). XGBoost
documents stable model serialization separately from Python memory snapshots;
use model serialization for durable artifacts.
[XGBoost model IO](https://xgboost.readthedocs.io/en/stable/tutorials/saving_model.html)

`.ebm` is a joblib serialization
loaded through Haute's existing restricted loader. The [engine probes](mod-f00-engine-probes.md) passed this
gate: a pinned 0.7.8 model references only `ExplainableBoostingRegressor` or
`ExplainableBoostingClassifier` plus joblib and NumPy scaffolding already
allowed, restores state through scikit-learn's `BaseEstimator` hooks, and
reloads bit-identically once those two exact classes are allowlisted, while a
crafted payload stays blocked. Audit reconstruction methods and fitted state, remove transient
callback/process references, and validate loaded estimator type, dimensions,
finite scores, term indices, bins, links and task before inference. Do not add
an `interpret.*` wildcard or call unrestricted `pickle.load`/`joblib.load`.
InterpretML documents pickle for full-object persistence; JSON export alone
does not establish a supported load-and-score round trip. If the restricted
route fails the security/portability probe, stop and resolve a data-only format
design before EBM implementation proceeds. Do not quietly ship an unsafe loader
or an independently implemented EBM scorer.
[InterpretML serialization guidance](https://interpret.ml/docs/faq.html)

Do not add a second metadata file. Extend the existing feature contract, which
every saved model already publishes and every scoring, MLflow and cache path
already reads, with the fields the new families need: `algorithm` (the
flavor, which also fixes the artifact format); the Haute loss and link; the
typed class mapping and positive class for classification; the
original-to-native feature-name mapping where a library restricts names; and
producer/engine versions. Feature order, categorical levels and their order,
null handling, target, task and offset column are already there, and the
categorical codes each engine sees derive from those stored levels, so no
separate encoding record exists. Offset link and scoring budget are read from
the native model, which stores its objective and trimmed/selected rounds, the
same way `_model_offset_link` reads CatBoost and RustyStats models today;
the adapter verifies that the model's objective agrees with the contract's
loss and fails otherwise. The new fields enter the canonical payload, so
`contract_hash` and every cache keyed on it cover them. CatBoost and GLM
contracts gain the same fields (CatBoost needs the class mapping for the shared
binary contract); no historical contract shape is kept.

The model file and its contract remain the existing two-member model set in
`CandidateRun`, worker delivery, publication, Save Model, MLflow logging,
download, and deployment, so `_training_artifacts.py` and
`TrainingArtifactSet` need no new artifact kind. Write atomically, reject
partial/mismatched sets, and fail with an actionable error when a model's
contract is missing or names a different algorithm or loss than the model. Match the repository's canonical-only policy: preserve the
currently supported CatBoost/RustyStats representations and add one canonical
format per new family, without historical-format adapters or guessing unknown
formats.

Extend `MODEL_FILE_SUFFIXES` (from the descriptors), API literals, frontend
suffixes, file pickers, artifact discovery, loaders, explainability dispatch
and score configuration together. Distinguish known native artifacts from
explicit generic MLflow pyfunc models. Resolve multiple candidate artifacts
deterministically from metadata/config or fail as ambiguous; an unknown suffix
must not choose a different algorithm accidentally.

Extend MLflow candidate logging and signatures using the same feature and
prediction contract. Native MLflow XGBoost/LightGBM flavors are usable only if
their scoring entry point retains Haute's categories, positive-class and offset
semantics. The shared `_native_pyfunc.py` wrapper is the one Haute
pyfunc for native model files: it loads the model plus its contract, dispatches
on the contract's `algorithm` to the same prediction adapter the local scorer
uses, and serves every family, CatBoost included. MLflow's CatBoost flavor
(used by `_mlflow_log.py:350` today) serves only `cb_model.predict()`, so it
cannot apply Haute's class mapping or return the positive-class probability
the shared binary contract requires. CatBoost therefore moves to the same
wrapper, keeping its native `.cbm` file as the wrapped artifact. Loaders must recover the native adapter for local
scoring/explanations; do not embed trained objects in closure pickles. Registration/promotion remains external to
training, consistent with the existing candidate-run contract.

Generated training scripts must still go through `_train_config.py` and
`TrainingJob`, so GUI and script config/hash/effective-parameter handling agree.
Model Score code generation must select the same artifact and adapter. Extend
native artifact bundling and generated requirements for the existing container
and Databricks paths; include each model's contract, the exact package distribution per
platform, and relevant OpenMP/native runtime prerequisites. Prove CPU
inference in a clean environment with no training-process state. This does not
expand the set of deployment platforms that Haute currently implements.

### User experience, diagnostics, and explanations

Add gateway cards with concise differences and supported tasks as each family
completes. Reuse the feature, target, evaluation, training and export layout.
Family-specific parameter controls should start with a short useful set and
retain validated advanced JSON. Display effective thread/round limits. The
existing loss control is shared by every family, offering only the losses the
selected family's descriptor supports. Hide or explain unavailable controls, while the backend
independently rejects them. EBM gets clear main-effect and pairwise-interaction
controls, and no GPU switch or GLM standard-error settings. Classification
targets whose labels are not Boolean or 0/1 show a positive-class selector for
every family.

The model type is chosen once, when the node is created, and cannot be changed
afterwards (owner decision, 23 September 2026); a different family is a new
node, so no family-switching state, stale-result carry-over or cross-family
parameter drafts are built. Extend training identity, result restoration and
cache keys for every new semantic field. Unresolved columns, parameters and
saved values stay visible and repairable.

Reuse common metric/validation charts where their statistical meaning applies.
For trees, expose the importance method and units: gain, split count and SHAP
summary are different quantities. Zero-importance/unused features must remain
accounted for. Retain explicit errors for failed optional diagnostics and
distinguish unsupported diagnostics from errors; do not return fabricated
empty success data. Keep SHAP/PDP sampling deterministic and bounded, and route
their predictions through the shared adapter including offsets and categories.

Use native XGBoost `pred_contribs` and LightGBM `pred_contrib` for local tree
contributions. For EBM, return native term scores and intercept, preserving an
interaction as one multi-feature term. Do not relabel EBM terms as SHAP, spread
interaction importance arbitrarily over individual features, or display GLM
p-values/AIC as if they applied. Persist/display shape-function bins, category
labels, missing-value bins, term weights, and pairwise surfaces. With
`outer_bags=1` there is no bag-to-bag variation to show.

Every explanation must reconstruct the raw margin, including the correct
baseline, and its inverse link must reproduce the served response/probability
within the item-7 tolerance. For log/logit models, contributions add in
log/log-odds space; show response scale values separately. Keep the baseline
separate or already included in the intercept according to the engine's
documented output, never twice. Extend trace method identifiers, error metadata
and `ModelScoreDetail.tsx` together, including binary explanations against the
probability rather than the class label. EBM's additive form is the basis of
its explanation, not a post-hoc approximation.
[EBM model structure](https://interpret.ml/docs/ebm.html),
[EBM term contributions](https://interpret.ml/docs/python/api/ExplainableBoostingClassifier.html)

### Resource use, packaging, and operational behavior

Stay inside the existing worker/job framework. Reuse cancellation fencing,
timeouts, superseded-job checks, artifact cleanup and last-good-result ownership.
Test cancellation during native fit as well as between phases. Native callbacks
should emit bounded progress updates and honour cancellation where supported;
worker termination remains necessary when native calls cannot cooperate.
EBM progress is stages/rounds, not a fabricated single tree iteration.

Thread use is new infrastructure, not an extension: today only tuning's
reserved-parameter list mentioned CatBoost's `thread_count`. The shared seams
now resolve one worker thread allotment per training job from
`HAUTE_TRAINING_THREADS` (default: the logical CPU count) and record it in
candidate evidence. Every adapter, CatBoost included, passes it as its native thread
count (`thread_count`, `nthread`, `num_threads`); EBM uses `n_jobs=1` and its
native thread setting, so the existing process-tree cancellation contract is
preserved. Tuning trials run sequentially, so a trial uses the whole
allotment. Thread keys stay reserved in raw `params`. Never allow a default
`n_jobs=-1` per fold/trial to oversubscribe the machine.

Reuse execution-context memory checks, but add measured family-specific
estimates for native datasets, category conversions, histogram/bin storage
and interaction arrays. CatBoost's pool/VRAM formula must not be reused as an
estimate for another library. Unknown estimates are surfaced as unknown, not
replaced with an invented safe row limit. Never silently downsample.

The engine probes resolved the pinned engines with Haute's dependencies on
Python 3.11 to 3.13 and ran them on Windows and Linux (NumPy 2.3, pandas 2.3,
scikit-learn 1.9); macOS runtime evidence comes from the existing macOS CI job
once the dependency change lands. Lock exact versions with each slice's
dependency change and record package licenses and notices as part of normal
distribution work. Keep model caches bounded and score from immutable loaded
state, recording engine version in model cards and candidate metadata. Verify
repeated fits, loads and failed runs release memory.

### Verification and release acceptance

Use the repository's red-green workflow: specs first, smallest failing test,
implementation, targeted module checks, then cross-stack verification for the
boundaries changed. Extend existing test modules and parametrizations where
they express the shared contract; add small engine-specific tests for native
semantics. Real tiny fitted models are required for persistence, categories,
baselines and contribution reconciliation; mocks alone cannot prove them.

| Acceptance area | Minimum decisive evidence |
|---|---|
| Config and capabilities | Every supported task/loss reaches its engine with the expected native objective; missing/unsupported losses, conflicting reserved params, unsupported devices/controls and invalid target domains fail consistently in frontend, HTTP and scripts; the checked-in capability fixture equals the serialized descriptors. |
| Shared preparation | Correct ordered feature projection and role exclusions; numeric/null and categorical/null fixtures; declared and inferred domains; different category encounter orders across score batches; unseen category and unsupported dtype failures. |
| Binary contract | For every family including CatBoost: Boolean, 0/1 and two-string targets; missing `positive_class` on a two-string target fails; a single-class or three-class target fails before fitting; a fixture probability of exactly `0.5` scores the negative class; labels always equal `prediction_proba > 0.5`. |
| Native fit | Tiny weighted regression, binary classification and positive-link regression for each family; checks that weights affect fit, offsets affect fit and predictions, seeds are passed, the thread allotment reaches the engine, and each allowed objective has a native smoke case. |
| Scoring parity | For every family including CatBoost, in-memory evaluation equals save/reload (bit-identical), local Model Score, eager/batch, exported script, the shared MLflow pyfunc wrapper and deployed scorer on the same fixture within the item-7 tolerance; binary labels and positive probabilities both checked; the native library's `predict` is the independent oracle. |
| Stopping and refit | A fit that stops early scores with the selected range before/after save; zero/one-based boundaries covered, with an XGBoost and a LightGBM fixture whose validation selects exactly three rounds refitting with exactly three; configured ceiling, actual fitted rounds and stopping reason (`none`, `validation`, `native_exhaustion`) recorded separately, including a LightGBM no-valid-split fixture (constant features or a leaf-size limit the data cannot meet) that records `native_exhaustion` rather than the configured rounds; EBM never stops early; EBM selection fits receive only training-partition rows, and the final development refit receives development rows (never final-test rows) and uses the winning explicit `max_rounds`, with a multi-term fixture confirming no budget is derived from `best_iteration_`. |
| Evaluation leakage | No final-test rows in fit/binning/category inference/interaction selection/tuning/stopping; no EBM selection fit receives validation rows and no EBM fit receives final-test rows; temporal/grouped plans respected; fold-specific categories and missing classes handled explicitly. |
| Explanations | Bias plus contributions equals raw prediction, inverse link equals served prediction, with/without exposure and after reload, within the item-7 tolerance; EBM pairwise terms remain intact and shapes agree with native term outputs. |
| Artifacts and trust | The extended contract survives publication, Save Model, MLflow and a restored handle; a missing contract, or one naming a different algorithm or loss than its model, fails; a changed contract field changes `contract_hash` and invalidates caches; ambiguous artifacts fail; an algorithm with no descriptor suffix raises instead of saving `.model`; EBM restricted loader permits intended models and rejects unapproved globals/crafted payloads. Hashes detect integrity drift, not authenticity. |
| Lifecycle and UI | Train/cancel/retry/save/reload/score per family, restored runs, late worker events, and last successful artifacts surviving a failed/cancelled replacement. |
| Resource and package | Projected wide-data input, bounded threads/fits/rounds, repeated-load memory check, clean CPU install/score in CI with the chosen distributions, and representative high-cardinality/interaction memory measurements. |

Relevant current backend homes include `tests/test_train_config_builder.py`,
`tests/test_target_task_gate.py`, `tests/test_training_catboost_projection.py`,
`tests/test_training_memory_safety.py`, `tests/test_training_worker_protocol.py`,
`tests/test_training_evaluation.py`, `tests/test_training_tuning.py`,
`tests/test_training_contract_per_model.py`,
`tests/test_modelling_train_score_contract.py`, `tests/test_mlflow_io.py`,
`tests/test_model_explainability.py`, `tests/test_model_export.py`,
`tests/test_modelling_export.py`, and `tests/test_model_score_codegen.py`.
Keep CatBoost-specific tests for its optimized path; add shared tests alongside
them rather than simply renaming assertions to cover unrelated engines.
Extend the frontend modelling/editor tests and one parameterized lifecycle
browser scenario per new family, with a separate EBM interaction-view case.

During implementation run a single new test, then its affected module and
touched-file ruff/format checks; run backend typing or frontend typecheck/lint
when their contracts change. Reserve full compatibility, coverage, mutation,
performance, build and browser gates for CI. For this document-only change,
roadmap/link accuracy and a diff review are sufficient; no model training suite
is required.

Release requires the capability matrix to be accurate, every shipped family's
complete CPU lifecycle green, current CatBoost/RustyStats contracts unchanged
apart from the shared binary contract, and unsupported features rejected
clearly. Update user guides, assistant authoring guidance/examples, model
cards, install/deploy docs and the model-node index. Fold implemented change
contracts into present-tense owning specs, update module ownership for new
files, and remove completed roadmap packages under the normal repository
lifecycle. Do not make unfinished choices selectable in a release.

### Delivery order and release gates

Order: the XGBoost slice is in place; next the LightGBM
decision and slice (MOD-F03), the EBM slice (MOD-F04), and the release check
(MOD-F05). Each slice ships save/reload/score, MLflow, codegen, deployment, UI
and explanations for its family; no serving work is postponed to the end.
Shared evaluation, persistence and frontend owners have one writer at a time,
so slices run sequentially.

The root owns requirement decisions, architecture, test oracles, diff review
and completion. Bounded workers may implement a specified adapter or execute
predefined checks; they must preserve the existing contracts and may not
independently redesign them.

The [engine probes](mod-f00-engine-probes.md) settled the four pre-implementation gates: versions and
distributions pass with the XGBoost cap; EBM persistence passes; EBM
`bags`-driven stopping fails for every objective, so EBM uses the training-only
policy; and native baseline, category and stopping behavior passes with the
adapter rules above. GPU, richer classification, EBM early stopping and EBM
outer bagging are subsequent features requiring their own contracts; they are
not hidden prerequisites for the CPU release.

## Model-family expansion work packages

### MOD-F03 — Decide on and deliver the complete LightGBM slice

**Why:** LightGBM's category encodings, native initial scores and model
persistence cannot be inferred from the XGBoost or CatBoost implementation, and
it largely overlaps XGBoost's capability, so its first-release place is an
owner decision (decision 3).

**Plan:** With the XGBoost slice accepted, the owner confirms whether LightGBM is in
the first release. If so, add its CPU GBDT adapter, canonical parameter and
alias validation, Dataset ownership, contract-derived category codes,
weight/init-score handling, native callbacks, selected-round save/predict,
gain/split/contribution outputs, allowlist and round key for the shared
tuning and refit, memory estimate,
and the full serving and UI path the XGBoost slice established.

**Acceptance:** Every acceptance-table row passes for LightGBM; initial scores
are included exactly once through a reloaded model; unseen categories follow
Haute's policy; conflicting parameter aliases fail visibly.

**Dependencies:** The owner's decision; the XGBoost slice it builds on is on `main`.

**Evidence:** `src/haute/modelling/_training_job.py`;
`src/haute/modelling/_train_config.py`; `src/haute/_model_scorer.py`;
`tests/test_training_contract_per_model.py`.

### MOD-F04 — Deliver the complete EBM slice and its term representation

**Why:** EBM uses additive terms, with different persistence, stopping,
explanation and validation requirements from a tree ensemble.

**Plan:** Add an InterpretML adapter using explicit nominal/continuous feature
types, sample weights, regression initial scores, training rows only,
`outer_bags=1`, no early stopping, an explicit `max_rounds` for every fit, and
bounded main/pairwise terms. Add the `interpret-core` dependency. Persist as a
`.ebm` joblib file through the restricted loader with the two EBM classes
allowlisted, report
native `best_iteration_` without inventing a tree count, and add its allowlist
(with `max_rounds` searchable) and fixed-budget refit policy. Add the serving path, gateway card, main-effect/interaction
controls, shape-function and pairwise-surface views, and native term
explanations in trace.

**Acceptance:** Every acceptance-table row passes for EBM; numeric/mixed and
binary fits round-trip through the restricted loader; intercept/terms/offset
reconstruct served predictions; interaction membership and constrained-feature
exclusions are validated; selection fits see only training-partition rows and
the final development refit sees development rows, never final-test rows.

**Dependencies:** None; the serving and UI seams are proven by the XGBoost slice.

**Evidence:** `src/haute/_sandbox.py`; `src/haute/modelling/_result_types.py`;
`src/haute/modelling/_training_job.py`; `src/haute/_model_explainability.py`.

### MOD-F05 — Verify and publish the CPU release

**Why:** Passing slice tests does not establish supported-platform
installation or a truthful user-facing feature matrix.

**Plan:** Run the authoritative CI gates; update dependency/build metadata,
guides, assistant examples, workflow coverage inventory and owning specs.
Benchmark representative wide and categorical inputs plus EBM interaction
sizes. Review the actual diffs and test evidence against every acceptance row
before release.

**Acceptance:** All shipped CPU model paths pass, including current
CatBoost/RustyStats regressions; documented features match exposed controls;
package/runtime requirements and EBM format limits are recorded; no incomplete
family is presented as production-ready.

**Dependencies:** MOD-F04, and MOD-F03 if the owner kept LightGBM in
the release.

**Evidence:** `tests/workflow_coverage.toml`; `.github/workflows/ci.yml`;
`frontend/e2e/core-flows.spec.ts`;
`docs/building-models/nodes/model-training.md`;
`docs/building-models/nodes/model-score.md`;
`src/haute/assistant/assets/authoring_guide.md`.

### MOD-F06 — Add separately verified GPU capabilities

**Why:** GPU availability and training behavior depend on engine, package,
platform, build and hardware; the current CatBoost toggle is not portable.

**Plan:** After CPU delivery, define a separate capability matrix and probes
for XGBoost CUDA (including how the GPU build is installed alongside the
`xgboost-cpu` distribution) and LightGBM's supported GPU backends. Add
actual device checks, engine-specific memory estimates, supported
objective/constraint combinations, progress/cancellation evidence, numerical
tolerances, and CPU serving parity. Fail an explicit unavailable GPU request
without retrying on CPU. Keep EBM CPU unless a separately verified
implementation provides another capability.

**Acceptance:** Only combinations backed by a real GPU CI or recorded hardware
probe become selectable; artifacts score correctly on CPU deployments; no
memory, device, objective or stopping mismatch is concealed by a fallback.

**Dependencies:** MOD-F05 and access to the advertised GPU environments.

**Evidence:** `src/haute/routes/_training_preparation.py`;
`src/haute/_host_memory.py`; `src/haute/_ram_estimate.py`;
`tests/test_training_memory_safety.py`.
