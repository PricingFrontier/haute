# Frontend modelling and optimiser UI roadmap

## Scope

The modelling panel (training configuration, the results workspace with its
evaluation, lift, residual, feature, term, SHAP and loss tabs, export and MLflow
logging) and the optimiser workspace that shares its results scaffolding.
Current behaviour is specified in
[the frontend modelling and optimiser UI specification](../frontend-modelling-optimiser-ui/high-level.md).
These packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FMOU-R03 | Planned | P3 | The frontend modelling specification describes the editor that exists; the dead sidebar component, the duplicated GLM conflict derivations and view switches, the message-prefix matching and the family-blind tune copy are gone. |

## Planned improvements

### FMOU-R03 — The specification, the dead copy and the duplicates
**Why:** The frontend modelling specification describes an editor the code and
the modelling specification no longer have: five GLM panes with regularisation
on Target, no tab badges, Include/Exclude pills, no tune-mode explainer, a test
set seeded on "Tune parameters"; tests pin the code's behaviour. The sidebar
`FeatureImportance` component is imported only by its test, and
`refitCapability`, `fixedBudgetCapability`, `GLM_REGULARIZATIONS` and
`GLM_CV_SELECTIONS` have no reference outside their definitions. The GLM
smooth-spline and robust-standard-error conflict derivations, text included, are
implemented twice; six hand-rolled view switches exist, two with the generic
accent; two jscpd clones remain. An estimate failure is classified as an
evaluation-preview failure by matching the server's message prefix, and the
fraction-sum issue is picked out of the issue list by a regex over its own text.
The Params pane's tune copy labels every family's search space "CatBoost" and
says "+ 1 final fit" for t-boost, which is never refit.

**Plan:** Rewrite the frontend modelling passages to the code; delete the dead
component and exports; export the conflict derivations from one module; reuse
one view switch; structured codes for the preview failure and the fraction-sum
issue; the family's label and the Train pane's wording in the tune copy.

**Acceptance:** The two specifications agree with each other and the code; knip
and the quoted greps report none of the symbols; jscpd none of the pairs; no
message-prefix match remains; the search-space label names the selected family.

**Dependencies:** `FSH-R09` covers the same message-matching pattern for
previews.

**Evidence:** `specs/frontend-modelling-optimiser-ui/high-level.md`;
`frontend/src/panels/modelling/FeatureImportance.tsx`;
`frontend/src/panels/modelling/algorithmCapabilities.ts`;
`frontend/src/panels/modelling/glmFamilies.ts`;
`frontend/src/panels/modelling/GLMRegularizationConfig.tsx`;
`frontend/src/panels/modelling/TermTableView.tsx`;
`frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/modelling/HyperparametersConfig.tsx`.
