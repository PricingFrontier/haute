# Modelling roadmap

## Scope

The modelling node's GLM configuration surface: how a feature enters a GLM,
how each fit (term) is configured, and how interactions declare their own
fits, on RustyStats 0.9.0. Current behaviour is specified in
[the modelling specification](../modelling/low-level.md) and
[the modelling UI specification](../frontend-modelling-optimiser-ui/low-level.md).
CatBoost configuration is out of scope; its Features pane keeps the current
include/exclude cards and monotonicity arrows unchanged.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-T00 | Planned | P1 | RustyStats 0.9.0 upgrade that keeps log-link exposure semantics bit-identical and pins the new theta and Tweedie-power behaviour. |
| MOD-T01 | Planned | P2 | GLM backend resolves model columns from terms and interactions, builds interactions without duplicated or re-typed main effects, ignores CatBoost-only levers, and drops the `all_factors` flag. |
| MOD-T02 | Planned | P2 | One GLM Features list where a feature enters the model only through its terms. |
| MOD-T03 | Planned | P2 | Interaction cards that pick two or more features and choose, per factor, the fit RustyStats 0.9.0 honours inside the interaction, including interaction-local splines. |

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
  carry any number of expression fits. The RustyStats expression grammar
  is exactly: `x ** n`, `x + y`, `x - y`, `x * y`, `x / y` (where `y` is a
  column or a number), or a bare `x`. Every identifier must be a schema
  column.
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
  `expression` {expr, monotonicity}. A type switch keeps supported shared
  parameters for the new type. Term types outside the eight above
  and the redirecting key `interaction` are rejected by column resolution.
  `variable` is accepted only for the two encoding types; other native keys
  must be the column RustyStats reads. A backend test asserts each subset is contained in RustyStats'
  `VALID_KEYS` for that type.
- `interactions` entries are `{"factors": [...], "specs": {factor: spec},
  "include_main": bool, "encoding"?: mode}`. Product `specs` holds only explicit **overrides**, each
  one of `{"type": "linear"}`, `{"type": "categorical"}`, `{"type": "bs",
  "df"?, "degree"?}`, or `{"type": "ns", "df"?}`. A factor with no entry
  follows its main term, or the dtype default when it has no main term
  (categorical for string dtypes, linear otherwise). Two or more filled
  factors are required; incomplete entries are skipped at train time, as
  today. Encoded modes and their parameters follow the fit-controls contract
  above and carry no product overrides.
- `all_factors` is removed from config, backend, validation, and docs.
- `exclude` and `monotone_constraints` become CatBoost-only levers. The GLM
  pane never writes them; `build_training_job_kwargs` passes `exclude=[]`
  and `monotone_constraints=None` for GLM regardless of stored values;
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
  `model.link`, so the two paths cannot disagree.

### Model membership

A GLM feature is in the model exactly when it has at least one term or is a
filled factor of an interaction. The training gate message becomes "add a
term to at least one feature". "Fit all with defaults" materialises explicit
dtype-default native terms for every eligible column that lacks a native
term, leaving existing terms untouched; it replaces the `all_factors`
opt-in and keeps the silent-failover gate because empty terms still block
training.

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

For Product mode, `_build_interactions(interactions, terms, cat_features)` resolves each
factor's spec as override, else native main term, else dtype default, and
then:

- rejects, naming the interaction and factor, an override type outside
  {`linear`, `categorical`, `bs`, `ns`, `target_encoding`}; any override carrying
  `monotonicity`; a `categorical` override on a factor whose main term
  exists and is not categorical; a `linear`, `bs`, or `ns` override on a
  string column or on a factor whose main term is categorical; an effective
  `frequency_encoding` fit; target encoding on a numeric column or alongside
  any non-Linear partner; an effective `ms` fit or a `bs` main term carrying
  `monotonicity` that is inherited into an interaction (RustyStats rejects
  both, so the slot must override to a plain spline or linear); conflicting
  overrides for one column across cards; and two cards with the same factor
  set and encoding mode in any order;
- materialises a main effect for every factor that has no main term when its
  card's `include_main` is true, adding the resolved spec to the terms dict
  handed to RustyStats exactly once per column, and always passes
  `include_main: False` to RustyStats, so no main effect is ever duplicated;
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
  select. Numeric dtypes are treated as continuous in the feature builder:
  neither native nor additional selectors offer target/frequency encoding.
  Their additional terms show Fit type: Expression beside the compact expression
  fields, even though it is the only choice. Non-numeric features offer target
  and frequency encoding as additional fits. A saved numeric encoding remains
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
**As main term** (only when the column has a native term that is not a
monotone spline; deletes `specs[col]`), **Linear** (numeric columns only;
writes `specs[col] = {"type": "linear"}`; refused when the main term is
categorical), **Categorical** (non-numeric columns without a native term,
or columns with an existing categorical main term;
writes `specs[col] = {"type": "categorical"}`),
**B-spline** and **Nat. spline** (numeric columns only; write
`specs[col] = {"type": "bs"|"ns"}` plus df and, for `bs`, degree fields;
never a monotonicity control; refused when the main term is categorical).
**Target enc.** is available for a non-numeric factor only while every other
picked factor uses Linear; with such a factor selected, partner fit menus
offer only Linear (and As main when that main fit is Linear). Native target
encoding can be inherited under the same rule. A frequency-encoded native
term cannot be inherited in a Product, but its raw non-numeric source can
use an explicit target-encoding override with Linear partners.
A column whose main term is a monotone spline, or a B-spline with
monotonicity, must pick an explicit slot fit; the slot shows "Monotone
splines cannot be used inside interactions" until it does. A slot with no
native term and no override selects its dtype default directly in Fit type,
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
one feature — an empty term set would silently auto-build a term for every
column."), drop the `exclude` narrowing from `_effective_glm_params`, and
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
