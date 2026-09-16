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
| MOD-T01 | Planned | P2 | GLM backend resolves model columns from terms and interactions, honours per-factor interaction fits, and drops the `all_factors` flag. |
| MOD-T02 | Planned | P2 | One GLM Features list where a feature enters the model only through its terms. |
| MOD-T03 | Planned | P2 | Interaction cards that choose features and configure each feature's fit inside the interaction. |

## Design (approved 16 September 2026)

### Problem

The GLM Features pane has two sections that both decide model membership.
The Features list offers Include/Exclude and monotonicity arrows, but for a
GLM an included feature is not fitted unless it also appears in the Factors
list below or the `all_factors` flag is ticked. Monotonicity exists twice
(top-level `monotone_constraints` and the per-term `monotonicity`) and the
backend merges them. Interactions can only reuse a factor's main-effect fit,
although RustyStats accepts a fit per factor inside each interaction. Two
latent defects sit in the same code: the Nat. spline row offers a
monotonicity toggle that RustyStats rejects (`ns` has no `monotonicity`
key), and `all_factors` is absent from the config registry and the cache key
list, so toggling it never changes the cached-result identity.

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
- Per-type keys mirror RustyStats' `VALID_KEYS`: `linear` {monotonicity};
  `categorical` {levels}; `bs` {df, degree, monotonicity}; `ns` {df};
  `ms` {df, degree, monotonicity, default `increasing`};
  `target_encoding` {prior_weight}; `expression` {expr, monotonicity}.
  The frontend's per-type property table is pinned to this list by a test.
- `interactions` entries become `{"factors": [...], "specs": {factor: spec},
  "include_main": bool}`. `specs` holds a native spec per factor (expression
  fits are not valid inside a RustyStats interaction). Two or more filled
  factors are required; incomplete entries are skipped at train time, as
  today.
- `all_factors` is removed from config, backend, validation, and docs.
- The GLM pane stops writing `exclude` and `monotone_constraints`. Both
  fields remain CatBoost levers. The backend keeps its `exclude` narrowing
  (harmless for GLM) and stops merging `monotone_constraints` into GLM terms.

### Model membership

A GLM feature is in the model exactly when it has at least one term or is a
filled factor of an interaction. The training gate message becomes "add a
term to at least one feature". "Fit all with defaults" materialises explicit
dtype-default native terms (categorical for string dtypes, linear otherwise)
for every eligible column that lacks a native term, leaving existing terms
untouched; it replaces the `all_factors`
opt-in and keeps the silent-failover gate because empty terms still block
training.

### Backend column resolution

One helper, `glm_model_columns(terms, interactions, *, schema)`, returns the
ordered unique columns the model needs: native term keys, every identifier
of each expression term, and every filled interaction factor. It raises
`HauteValidationError` naming the term when an expression does not match
the grammar or references a column outside the schema, when a native
term key is not a schema column, or when an expression term is keyed by a
schema column (a column key always means a native fit). The three places that currently treat term
keys as the column set switch to it: `_training_preparation`'s feature
contract, `_training_job`'s final feature contract, and the dispersion
worker's column load. The evaluation-key guard uses the same resolution.

### Interaction building

`_build_interactions` uses `specs[factor]` when present, otherwise the
factor's main term, otherwise the dtype default. The existing rule that
forces `include_main` to `False` when every factor already has a main term
is unchanged.

### GLM Features pane

`GLMTermsConfig.tsx` replaces both `CommonFeatureConfig` (for GLM) and
`GLMFactorConfig`. `CommonFeatureConfig` becomes CatBoost-only and loses its
`algorithm` prop and GLM-specific hint text.

- Header: "Features", "N of M in model", the existing search box, an
  "In model only" filter toggle, bulk "Fit all with defaults" and "Remove
  all terms" (the latter confirms once and clears `terms` and
  `interactions`), and the Builder/JSON mode toggle.
- Row: column name, dtype pill, a muted "Not in model" or "Interaction only"
  tag where applicable, and an "Add term" button. The first Add term on a
  row creates the dtype-default native term. Once a row has a native term,
  Add term creates an expression term prefilled with name `<column>_sq`
  (uniquified) and expression `<column> ** 2`.
- Term sub-card (shared `TermCard.tsx`): type select, type parameters
  (df and degree for `bs`/`ms`, df for `ns`, prior weight for
  `target_encoding`, name and expression for `expression`), the existing
  red-down/yellow-neutral/green-up monotonicity arrows only for `linear`,
  `bs`, and `expression`, an increasing/decreasing pair for `ms`, and a
  remove button. Switching type keeps only the properties valid for the new
  type. Choosing a native type is disabled with a hint when the row already
  has a different native term. The expression field validates against the
  grammar above and shows the grammar as help.
- An expression term is listed under the first eligible column its
  expression names. Expression terms whose identifiers name no eligible
  column (possible only via JSON mode) are listed in a trailing "Unresolved
  expressions" group with a warning; training fails loudly on them.
- Removing a term needs no confirmation. Interactions do not depend on main
  terms, so nothing else changes.
- JSON mode is unchanged: the RustyStats `terms` dict, saved on blur.

### Interactions

`GLMInteractionsConfig.tsx` renders beneath the Features list with the same
card language. "Add interaction" appends a card with two feature slots and a
"+ feature" control for higher orders. Each slot pairs a column select (any
eligible column not already in that card) with a `TermCard` restricted to
native types; picking a column seeds the spec from the column's main term
when it has one, otherwise the dtype default. The card keeps the
"Include main effects" checkbox (help text: applies only to factors with no
main term of their own) and a remove button. A card with fewer than two
picked columns shows "Pick at least two features" and is ignored at train
time.

## Planned improvements

Delivery order is `MOD-T01` → `MOD-T02` → `MOD-T03`. The three packages ship
on one pull request; one Codex review and one Playwright run happen after
the third package, not per package.

### MOD-T01 — GLM backend term contract
**Why:** The backend assumes every term key is a column, ignores per-factor
interaction fits, carries the unregistered `all_factors` flag, and merges a
second monotonicity source into terms.

**Plan:** Add `glm_model_columns` to `src/haute/modelling/_rustystats.py` and
route `_training_preparation._glm_training_term_columns`, the
`_training_job` GLM feature contract and evaluation-key guard, and the
dispersion worker's column load through it. Teach `_build_interactions` to
honour `specs`. Delete `all_factors` from `GLM_CONFIG_KEYS`,
`_resolve_glm_terms`, and `training_objective_issue`, whose empty-terms
message becomes "GLM config has no terms. Add a term to at least one
feature — an empty term set would silently auto-build a term for every
column." Remove the GLM branch that copies `monotone_constraints` into
terms. Add a test that pins Haute's per-type property table against
RustyStats' `VALID_KEYS`. Update the "Modelling-node algorithm config" and
`monotone_constraints` bullets of `specs/modelling/low-level.md` and the
`terms`/`interactions` rows of
`docs/building-models/nodes/model-training.md` (which also list term types
that do not exist).

**Acceptance:** A config with a native term, an expression term keyed by a
non-column name, and an interaction whose factor has no main term trains
end-to-end in `tests/test_glm_integration.py`; the exported script for the
same config trains identically. Expression terms with an unknown column or
an unsupported expression fail before data load with a message naming the
term. An interaction `specs` entry reaches RustyStats verbatim; a missing
entry still inherits the main term. `all_factors` no longer appears in
`src/`, `tests/`, `specs/`, or `docs/`. A GLM config carrying
`monotone_constraints` fits without those constraints being applied.

**Dependencies:** RustyStats 0.7 dict API (`glm_dict`) and its expression
grammar.

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
per-type property table, expression grammar check and identifier
extraction, expression anchoring, unique expression naming, model
membership). Compose it as the GLM Features pane in `ModellingConfig.tsx`.
Delete `GLMFactorConfig.tsx` and `cleanupFeatureDependencies`; make
`CommonFeatureConfig` CatBoost-only; make `finalSelectedFeatureNames` return
columns with a term or a filled interaction factor for GLM; update the
`glm-factor-selection` message in `trainingObjective.ts`. Update the
`CommonFeatureConfig`/`GLMFactorConfig` bullet and the Features-pane
sentences of `specs/frontend-modelling-optimiser-ui/low-level.md`.

**Acceptance:** Component tests cover: a row with no term shows "Not in
model" and Add term writes the dtype-default native term; a second Add term
writes a uniquely named `** 2` expression term listed under the same row;
native types are disabled on the type select while another native term
exists; type switches drop invalid properties and `ns` offers no
monotonicity; `ms` writes `increasing` by default; expression edits that
break the grammar are rejected client-side with the grammar shown; "Fit all
with defaults" adds only missing native terms; "Remove all terms" confirms
once and clears `terms` and `interactions`; the "In model only" filter and
the "N of M in model" count treat interaction-only columns as in the model;
JSON mode round-trips an expression term keyed by a non-column name and
lists an unresolvable one under "Unresolved expressions"; the GLM pane never
writes `exclude`, `monotone_constraints`, or `all_factors`. The CatBoost
pane tests pass unchanged apart from the removed GLM cases.

**Dependencies:** MOD-T01.

**Evidence:** `frontend/src/panels/modelling/CommonFeatureConfig.tsx`;
`frontend/src/panels/modelling/GLMFactorConfig.tsx`;
`frontend/src/panels/modelling/featureSelection.ts`;
`frontend/src/utils/trainingObjective.ts`;
`frontend/src/panels/__tests__/GLMComponents.test.tsx`;
`frontend/src/panels/modelling/__tests__/CommonFeatureConfig.test.tsx`.

### MOD-T03 — Interaction cards
**Why:** Interactions can only pair columns that already have a main term
and cannot choose a different fit inside the interaction, although
RustyStats supports both.

**Plan:** Add `frontend/src/panels/modelling/GLMInteractionsConfig.tsx`
rendering beneath `GLMTermsConfig`, reusing `TermCard` restricted to native
types. Write `specs[factor]` on column pick, seeded from the main term or
the dtype default, and keep `factors`/`include_main` as today. Support two
or more slots per card. Update the interactions sentences of
`specs/frontend-modelling-optimiser-ui/low-level.md`.

**Acceptance:** Component tests cover: Add interaction appends a card with
two empty slots; picking a column writes `factors[i]` and `specs[column]`
seeded from the main term when present; a column with no main term seeds
the dtype default; "+ feature" adds a third slot and the backend contract
accepts it; changing a slot's fit writes only that `specs` entry; removing a
slot removes its spec; a card with one column shows the incomplete note; the
same column cannot be picked twice in one card; removing a card removes its
entry. A final Playwright pass through `frontend/e2e/core-flows.spec.ts`
configures a GLM with one native term, one expression term, and one
interaction with its own fit, trains it, and sees results.

**Dependencies:** MOD-T01, MOD-T02.

**Evidence:** `frontend/src/panels/modelling/GLMFactorConfig.tsx`
(interactions section); `src/haute/modelling/_rustystats.py`
(`_build_interactions`); `frontend/e2e/core-flows.spec.ts`.
