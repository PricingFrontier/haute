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
