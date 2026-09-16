# Modelling roadmap

## Scope

The modelling node's GLM configuration surface: how a feature enters a GLM,
how each fit (term) is configured, and how interactions declare their own
fits. Current behaviour is specified in
[the modelling specification](../modelling/low-level.md) and
[the modelling UI specification](../frontend-modelling-optimiser-ui/low-level.md).
CatBoost configuration is out of scope; its Features pane keeps the current
include/exclude cards and monotonicity arrows unchanged.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-T01 | Planned | P2 | GLM backend resolves model columns from terms and interactions, builds interactions without duplicated or re-typed main effects, ignores CatBoost-only levers, and drops the `all_factors` flag. |
| MOD-T02 | Planned | P2 | One GLM Features list where a feature enters the model only through its terms. |
| MOD-T03 | Planned | P2 | Interaction cards that pick two or more features and choose, per factor, the fit RustyStats will actually honour inside the interaction. |

## Design (approved 16 September 2026; revised after Codex plan review)

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

### What RustyStats 0.7 honours inside an interaction

Verified by building design matrices with `dict_to_parsed_formula` and
`InteractionBuilder.build_design_matrix_from_parsed` on the installed
package:

- A factor's spline expansion comes from its **main** term. A spline spec
  inside an interaction is parsed and discarded: `bs(df=6)` in the
  interaction with a `bs(df=4)` main term used `df=4`; without a main spline
  the factor entered the interaction linearly.
- `linear` in an interaction forces a linear column even when the main term
  is a spline (`force_linear`).
- `categorical` in an interaction marks the column categorical globally: a
  numeric `x` with a linear main term and a categorical interaction slot had
  its main effect re-typed to one dummy per distinct value.
- `linear` on a string column fails inside the design-matrix builder with a
  float-conversion error.
- `target_encoding` inside a product interaction fails on the real fit path
  (`ColumnNotFoundError: "TE(d)" not found`) whether or not a main TE term
  exists.
- Three or more factors, an expression term on an interacted column, and one
  column shared by two interactions all build correctly.

The interaction design below offers only combinations that build correctly
and rejects the rest before RustyStats sees them.

### Stored config contract (GLM)

No schema migration. Haute has no released users, so removed fields are
dropped outright and stale tests are updated.

- `terms` stays the RustyStats dict `{name: spec}`. A **native** spec
  (`linear`, `categorical`, `bs`, `ns`, `ms`, `target_encoding`) is keyed by
  the column it fits, so each column carries at most one native fit. An
  **expression** spec `{"type": "expression", "expr": ..., "monotonicity"?}`
  is keyed by a user-editable name that is not a column name; a column may
  carry any number of expression fits. The RustyStats 0.7 expression grammar
  is exactly: `x ** n`, `x + y`, `x - y`, `x * y`, `x / y` (where `y` is a
  column or a number), or a bare `x`. Every identifier must be a schema
  column.
- The builder edits an intentional **subset** of RustyStats' per-type keys:
  `linear` {monotonicity}; `categorical` {}; `bs` {df, degree,
  monotonicity}; `ns` {df}; `ms` {df, degree, monotonicity, default
  `increasing`}; `target_encoding` {prior_weight}; `expression` {expr,
  monotonicity}. Keys outside the subset that RustyStats accepts (`k`,
  `knots`, `boundary_knots`, `levels`, `n_permutations`) can be supplied
  through JSON mode and survive builder edits to other fields; a type switch
  keeps only the subset for the new type. Term types outside the seven above
  and the redirecting keys `variable` and `interaction` are rejected by
  column resolution, because a native key must be the column RustyStats
  reads. A backend test asserts each subset is contained in RustyStats'
  `VALID_KEYS` for that type.
- `interactions` entries are `{"factors": [...], "specs": {factor: spec},
  "include_main": bool}`. `specs` holds only explicit **overrides**, each
  `{"type": "linear"}` or `{"type": "categorical"}`. A factor with no entry
  follows its main term, or the dtype default when it has no main term
  (categorical for string dtypes, linear otherwise). Two or more filled
  factors are required; incomplete entries are skipped at train time, as
  today.
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
the model reads: native term keys, every identifier of each expression term
(after the expression passes the grammar above), and every filled
interaction factor. It raises `HauteValidationError` naming the term when an
expression fails the grammar, when an expression key collides with a
native term key or with one of its own identifiers, when a term type is
outside the seven supported types, or when a term carries `variable` or
`interaction`. This phase serves the projection demand computed before
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

`_build_interactions(interactions, terms, cat_features)` resolves each
factor's spec as override, else native main term, else dtype default, and
then:

- rejects, naming the interaction and factor, an override type outside
  {`linear`, `categorical`}; a `categorical` override on a factor whose main
  term exists and is not categorical; a `linear` override on a string
  column or on a factor whose main term is categorical; an effective
  `target_encoding` fit, whether inherited from a main term or written as
  an override; conflicting overrides for one column across cards; and two
  cards with the same factor set in any order;
- materialises a main effect for every factor that has no main term when its
  card's `include_main` is true, adding the resolved spec to the terms dict
  handed to RustyStats exactly once per column, and always passes
  `include_main: False` to RustyStats, so no main effect is ever duplicated;
- passes a spline main term through unchanged (RustyStats reuses the main
  spline) and passes `{"type": "linear"}` for a linear override on a spline
  main term.

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
- Row: column name, dtype pill, a muted "Not in model" or "Interaction only"
  tag where applicable, and an "Add term" button.
- Term sub-card (shared `TermCard.tsx`): for a native term, a type select
  over the six native types and the subset parameters for the chosen type
  (df and degree for `bs`/`ms`, df for `ns`, prior weight for
  `target_encoding`); for an expression term, a fixed "Expression" label, a
  name field, and an expression field. Monotonicity uses the existing
  red-down/yellow-neutral/green-up arrows for `linear`, `bs`, and
  `expression`, and an increasing/decreasing pair for `ms`. Every sub-card
  has a remove button. Native and expression terms never convert into each
  other through the type select.
- An expression term is listed under the first eligible column its
  expression names. Expression terms whose identifiers name no eligible
  column (possible only via JSON mode) are listed in a trailing "Unresolved
  expressions" group with a warning; training fails loudly on them.
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
- **Native type switch** to `T`: `terms[col] =` the current spec reduced to
  `{"type": T}` plus the subset keys for `T` that were present; switching to
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
card language. "Add interaction" appends `{"factors": ["", ""],
"include_main": true}`. Each slot pairs a column select (any eligible column
not already in that card, excluding columns whose main term is target
encoding, with the hint "Target-encoded features cannot be interacted in
RustyStats 0.7") with a fit select offering only what RustyStats
honours: **As main term** (only when the column has a native term; deletes
`specs[col]`), **Linear** (numeric columns only; writes `specs[col] =
{"type": "linear"}`; refused when the main term is categorical), and
**Categorical** (only when the column has no native term or a categorical
one; writes `specs[col] = {"type": "categorical"}`). A slot with no native
term and no override shows its dtype default as the effective fit. Picking
a column writes `factors[i]` and deletes any `specs` entry for the column it
replaced. "+ feature" appends an empty slot; a slot can be removed when the
card has more than two. The "Include main effects" checkbox appears only
while at least one picked column has no native term, with the help text
"Adds a main effect for factors that have none; factors with a term already
keep it". A card with fewer than two picked columns shows "Pick at least
two features" and is ignored at train time. A card whose factor set equals
another card's shows "Duplicate interaction" and training rejects it.
Removing a card removes its entry.

### Test impact

- `specs/modelling/low-level.md`: the "Modelling-node algorithm config"
  bullet (GLM keys, `specs` overrides, `exclude` no longer narrowing GLM),
  the `monotone_constraints` bullet (CatBoost-only, GLM rejection), and the
  `_build_interactions` bullets (materialised main effects, rejected
  combinations, design-column tests).
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

Delivery order is `MOD-T01` → `MOD-T02` → `MOD-T03`. The three packages ship
on one pull request; one Codex review and one Playwright run happen after
the third package, not per package.

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
(`variable` on a target-encoding term, `interaction`, `frequency_encoding`);
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
`test_build_interactions_rejects_spline_override_categorical_retype_and_linear_on_string`;
`test_build_interactions_rejects_effective_target_encoding_inherited_or_overridden`;
`test_build_interactions_linear_override_on_spline_main_forces_linear_column`;
`test_glm_ignores_exclude_and_rejects_monotone_constraints`;
`test_training_and_dispersion_sinks_keep_excluded_glm_term_columns`;
`test_all_factors_is_gone` (grep-level assertion over `src/`, `tests/`,
`specs/`, `docs/`); and an end-to-end fit in
`tests/test_glm_integration.py` of a native term, an expression term keyed
by a non-column name, and an interaction with a materialised main effect,
whose exported script trains identically.

**Dependencies:** RustyStats 0.7 dict API (`glm_dict`), its expression
grammar, and the interaction behaviours verified above.

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
per-type subset table, expression grammar check and identifier extraction,
expression anchoring, unique expression naming, model membership, every
editor transition as a pure config-to-config function). Compose it as the
GLM Features pane in `ModellingConfig.tsx`. Delete `GLMFactorConfig.tsx`
and `cleanupFeatureDependencies`; make `CommonFeatureConfig` CatBoost-only;
make `finalSelectedFeatureNames` return columns with a term or a filled
interaction factor for GLM; update the `glm-factor-selection` message in
`trainingObjective.ts`. Update the UI spec sections named under Test
impact.

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
unresolvable one under Unresolved expressions`; `the GLM pane never writes
exclude, monotone_constraints, or all_factors`. The CatBoost pane tests pass
unchanged apart from the removed GLM cases.

**Dependencies:** MOD-T01.

**Evidence:** `frontend/src/panels/modelling/CommonFeatureConfig.tsx`;
`frontend/src/panels/modelling/GLMFactorConfig.tsx`;
`frontend/src/panels/modelling/featureSelection.ts`;
`frontend/src/utils/trainingObjective.ts`;
`frontend/src/panels/__tests__/GLMComponents.test.tsx`;
`frontend/src/panels/modelling/__tests__/CommonFeatureConfig.test.tsx`.

### MOD-T03 — Interaction cards
**Why:** Interactions can only pair columns that already have a main term
and cannot choose a linear or categorical fit inside the interaction,
although RustyStats honours both.

**Plan:** Add `frontend/src/panels/modelling/GLMInteractionsConfig.tsx`
rendering beneath `GLMTermsConfig`, with slot rules and writes exactly as
in the Interactions section, and slot-availability helpers in `glmTerms.ts`.
Update the interactions sentences of
`specs/frontend-modelling-optimiser-ui/low-level.md`.

**Acceptance:** Named scenarios in `GLMInteractionsConfig.test.tsx`: `Add
interaction appends two empty slots with include_main true`; `picking a
column writes factors[i] and no specs entry`; `As main term is offered only
for columns with a native term`; `Linear is refused for string columns and
categorical main terms`; `Categorical is refused for non-categorical main
terms`; `choosing an override writes only specs[col] and As main term
deletes it`; `replacing a slot's column deletes the old override`; `+
feature adds a third slot and a slot can be removed only above two`;
`Include main effects appears only while a picked column has no native
term`; `a card with one column shows the incomplete note`; `the same column
cannot be picked twice in one card`; `columns with a target-encoding main
term are not offered in slots`; `a card duplicating another's factor set
shows Duplicate interaction`; `removing a card removes its entry`. A
final Playwright pass through `frontend/e2e/core-flows.spec.ts` configures a
GLM with one native term, one expression term, and one interaction using a
linear override on a spline main term, trains it, and sees results.

**Dependencies:** MOD-T01, MOD-T02.

**Evidence:** `frontend/src/panels/modelling/GLMFactorConfig.tsx`
(interactions section); `src/haute/modelling/_rustystats.py`
(`_build_interactions`); `frontend/e2e/core-flows.spec.ts`.
