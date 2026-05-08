# Price Contour Apply Explainer Columns Spec

## Purpose

Haute wants to make online optimiser apply tracing less black-box.

For a clicked quote, Haute can already render most of the explanation itself:

- the optimiser apply input DataFrame is long-format scored data with every candidate scenario;
- `ApplyOptimiser.apply(df).dataframe` contains the selected scenario per quote;
- the saved artifact already contains lambdas, constraints, objective, and column names.

So `price-contour` does not need a large parallel explanation API. Haute mainly needs the library to expose the pieces that should not be reimplemented outside `price-contour`, especially ratio-constraint linearisation and exact fixed-lambda score semantics.

## Proposed API

Add one helper to `ApplyOptimiser`:

```python
explained = applier.with_explainer_columns(df)
```

This returns the original input DataFrame with deterministic explainer columns appended.

The method must not change `ApplyOptimiser.apply(df)` behaviour or output.

## Returned DataFrame

`with_explainer_columns(df)` should preserve all input rows and append these columns.

Required columns:

| Column | Type | Meaning |
|---|---:|---|
| `decision_score` | float | Exact scalar fixed-lambda score used to choose the winning candidate. |
| `selected` | bool | True for the candidate selected by `apply(df)` for that quote. |
| `is_baseline` | bool | True for the baseline scenario for that quote. |

For each constraint `name`, append:

| Column | Meaning |
|---|---|
| `lambda_term_<name>` | Signed contribution of this constraint to `decision_score`. |
| `linearised_<name>` | Value used in the fixed-lambda score. For sum constraints this equals the original constraint column. For ratio constraints this is the internal linearised value. |

If `constraints == {}` and `lambdas == {}`, the method should still append `decision_score`, `selected`, and `is_baseline`; `decision_score` should equal the objective column, and no `lambda_term_*` or `linearised_*` columns are required.

## Score Semantics

The key requirement is that `decision_score` and `selected` match `ApplyOptimiser.apply(df)`.

For every candidate row:

```text
decision_score == objective + sum(lambda_term_<constraint>)
```

For each constraint:

```text
lambda_term_<name> = signed_lambda_<name> * linearised_<name>
```

where `signed_lambda_<name>` is:

- `+lambda` for minimum constraints;
- `-lambda` for maximum constraints.

For sum constraints, `linearised_<name>` is the original constraint column value. For ratio constraints, `linearised_<name>` is the internal sum-shaped value produced by the same ratio-linearisation path used by apply mode.

For every quote:

- exactly one row has `selected == True`;
- that row is the same candidate chosen by `ApplyOptimiser.apply(df)`;
- `selected` agrees with `optimal_step` and `optimal_scenario_value` in the apply output;
- tie-breaking matches apply mode exactly.

## Baseline Semantics

Haute wants to mark the baseline point on the chart.

Set `is_baseline` per quote using deterministic rules:

1. Prefer exact `scenario_value == 1.0`.
2. If no exact match exists, choose the nearest `scenario_value` to `1.0`.
3. If tied, use stable scenario ordering.

Exactly one candidate per quote should have `is_baseline == True` when the quote has at least one candidate row.

In the current online optimiser shape, scenario values are shared across quotes, so this usually resolves to the same baseline `scenario_index` for every quote. The API should still set the marker on each quote's candidate rows.

## Ratio Constraints

This is the main reason this helper belongs in `price-contour`.

For ratio constraints, Haute should not reimplement `_linearise_ratio_constraints`.

`with_explainer_columns(df)` should expose:

- the actual input numerator/denominator data remains unchanged in the original columns;
- `linearised_<name>` contains the sum-shaped internal value used for fixed-lambda scoring;
- `lambda_term_<name>` is computed from the same internal value and sign convention as apply mode;
- `decision_score` uses that linearised value.

This lets Haute chart:

- actual objective curve;
- raw input constraint columns where meaningful;
- linearised ratio curve when the optimiser decision needs to be explained;
- selected and baseline markers.

## Haute Rendering Plan

For a clicked online optimiser apply row, Haute will:

1. Get the quote id from the traced output row.
2. Build or access the apply input candidate frame.
3. Call `applier.with_explainer_columns(candidate_df)`.
4. Filter to the clicked quote.
5. Plot:
   - x-axis: `scenario_value` or `scenario_index`;
   - objective curve;
   - constraint curves;
   - optional `decision_score` curve;
   - selected marker where `selected == True`;
   - baseline marker where `is_baseline == True`.

Haute owns the charting and trace UI. `price-contour` owns the optimiser-consistent scoring columns.

## Ratebook Note

No `price-contour` change is required for ratebook tracing if saved artifacts continue to include factor tables:

```python
factor_tables: dict[str, list[dict]]
```

with entries containing:

- `__factor_group__`
- `optimal_scenario_value`
- optional supporting metadata such as `quote_count`

Haute can explain ratebook apply from the artifact:

```text
Base = 1.0000
age_band = 17-24    x 0.8750 -> 0.8750
region = North      x 1.0500 -> 0.9188
Final optimised_factor = 0.9188
```

## Error Semantics

`with_explainer_columns(df)` should validate with the same rules as `apply(df)`.

It should fail loudly for:

- missing quote id, scenario index, scenario value, objective, or constraint columns;
- invalid/null data that `apply(df)` would reject;
- unknown lambda keys;
- invalid ratio constraint numerator/denominator columns.

Do not silently omit explainer columns or fall back to approximate scoring.

## Tests Required In Price Contour

1. Returned DataFrame has the same row count and original columns as input.
2. `selected` matches `ApplyOptimiser.apply(df)` for every quote.
3. `decision_score` reconstructs from objective plus lambda terms.
4. Sum constraints get correct `linearised_<name>` and `lambda_term_<name>` values.
5. Ratio constraints get correct `linearised_<name>` and `lambda_term_<name>` values.
6. Exact `scenario_value == 1.0` is marked as baseline.
7. Nearest-to-1.0 scenario is marked when exact baseline is absent.
8. Tie-breaking matches `apply(df)`.
9. Custom column names work.
10. `ApplyOptimiser.apply(df)` remains unchanged.

## Acceptance Criteria

This is complete when Haute can use `with_explainer_columns(df)` to render online optimiser tracing without reimplementing optimiser maths:

- objective and constraint curves come from the scored input;
- selected point comes from `selected`;
- baseline point comes from `is_baseline`;
- optional decision-score curve comes from `decision_score`;
- ratio constraints are explained using library-owned linearisation.
