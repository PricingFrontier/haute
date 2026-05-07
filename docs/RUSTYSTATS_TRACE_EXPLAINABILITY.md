# RustyStats Trace Explainability

## Goal

Add per-prediction trace explanations for RustyStats `.rsglm` model-score nodes, starting with `conversion_scoring`, in a way that feels consistent with CatBoost explanations in Haute.

The consistency target is the **syntax and trace contract**, not the mathematical method.

- CatBoost uses SHAP values.
- RustyStats GLMs should use exact GLM term contributions.
- Both should return a similar `node_detail.explanation` shape so the trace UI can render the same contribution ladder.

## Key Decision

RustyStats should expose a native prediction-contribution API. Haute should not reconstruct RustyStats internals such as spline bases, categorical encodings, interactions, offsets, or complement logic.

The RustyStats API should decompose the model prediction as:

```text
linear predictor = intercept + term contributions
prediction        = inverse_link(linear predictor)
```

For identity-link models, the running total is already on the prediction scale.

For non-identity links, such as the `conversion_scoring` binomial/logit model, the ladder should show the running total on the linear predictor scale, then finish with the response-scale prediction.

## Current Example: `conversion_scoring`

`rating/config/model_scoring/conversion_scoring.json` uses:

```json
{
  "artifact_path": "conversion.rsglm",
  "task": "regression",
  "output_column": "conversion_prediction",
  "contract": {
    "inputs": ["difference_to_market"],
    "outputs": ["conversion_prediction"]
  }
}
```

The loaded RustyStats model is a binomial GLM with a logit link and a smooth term:

```text
sale_flag ~ ns(difference_to_market, df=10)
```

So the user-facing explanation should be grouped back to:

```text
Base
difference_to_market
Prediction
```

The spline basis columns are implementation detail. They can optionally be included for debugging, but the default trace should show the original factor.

## RustyStats API Proposal

Add a public API on `GLMModel`:

```python
model.predict_contributions(
    data: pl.DataFrame,
    *,
    group_terms: bool = True,
    include_design_columns: bool = False,
) -> list[dict]
```

The returned list should contain one explanation per input row.

### Returned Row Shape

```python
{
    "family": "binomial",
    "link": "logit",
    "output_space": "linear_predictor",
    "prediction_space": "response",
    "base_value": 0.04171833,
    "sum_contributions": -1.42801,
    "prediction_from_contributions": -1.38629,
    "prediction_value": 0.2,
    "contributions": [
        {
            "term": "difference_to_market",
            "feature": "difference_to_market",
            "term_type": "smooth",
            "feature_value": -25.0,
            "contribution": -1.42801,
            "rank": 1
        }
    ]
}
```

When `include_design_columns=True`, each grouped term can include the lower-level design-column breakdown:

```python
{
    "term": "difference_to_market",
    "feature": "difference_to_market",
    "term_type": "smooth",
    "feature_value": -25.0,
    "contribution": -1.42801,
    "design_columns": [
        {
            "column": "ns(difference_to_market, 1/9)",
            "basis_value": 0.123,
            "coefficient": -11.335,
            "contribution": -1.394
        }
    ]
}
```

## RustyStats Responsibilities

RustyStats should own:

- Building the prediction design matrix for new data.
- Aligning coefficients to design columns.
- Grouping design columns back to source terms.
- Handling smooth terms, categorical terms, target/frequency encodings, interactions, offsets, complements, and intercepts.
- Applying the inverse link to produce the response-scale prediction.
- Validating additivity:

```text
base_value + sum(contributions) == linear predictor
inverse_link(linear predictor) == prediction
```

If either identity fails beyond a tight tolerance, RustyStats should raise rather than return a misleading explanation.

## Haute Explanation Contract

Haute should map RustyStats output into the same broad explanation shape used for CatBoost.

```python
{
    "type": "rustystats_glm_contributions",
    "method": "rustystats_glm_contributions",
    "status": "ok",
    "output_space": "linear_predictor",
    "prediction_space": "response",
    "base_value": 0.04171833,
    "sum_contributions": -1.42801,
    "contribution_sum": -1.42801,
    "prediction_from_contributions": -1.38629,
    "model_output_value": -1.38629,
    "prediction_value": 0.2,
    "link_function": "logit",
    "family": "binomial",
    "feature_count": 1,
    "feature_values": {
        "difference_to_market": -25.0
    },
    "contributions": [
        {
            "feature": "difference_to_market",
            "feature_value": -25.0,
            "shap_value": -1.42801,
            "contribution": -1.42801,
            "abs_shap_value": 1.42801,
            "abs_contribution": 1.42801,
            "rank": 1,
            "term_type": "smooth"
        }
    ],
    "truncated": false,
    "omitted_count": 0
}
```

The `shap_value` alias is optional but useful if we want the current frontend ladder to consume CatBoost and RustyStats with minimal branching. The preferred semantic field for RustyStats is `contribution`.

## Frontend Behaviour

The trace UI should render RustyStats and CatBoost with the same contribution ladder shape:

```text
Factor                  Value       Contribution     Total
Base                                               0.0417
difference_to_market    -25.0          -1.4280    -1.3863
Prediction              conversion_prediction       0.2000
```

For RustyStats:

- The running total is in `output_space`.
- The final prediction is in `prediction_space`.
- If `output_space != prediction_space`, the UI should label the final conversion clearly.

For `conversion_scoring`, that means:

```text
running total: logit / linear predictor
prediction: conversion probability
```

## Haute Implementation Plan

1. Add RustyStats support to `src/haute/_model_explainability.py`.

   - Detect `.rsglm` configs.
   - Load the same cached model as scoring.
   - Call `scoring_model.raw_model.predict_contributions(one_row_frame)`.
   - Validate the explanation matches the traced prediction.
   - Return the common `node_detail.explanation` shape.

2. Keep CatBoost and RustyStats methods separate internally.

   - CatBoost: `catboost_shap`
   - RustyStats: `rustystats_glm_contributions`

3. Keep the frontend syntax shared.

   - `base_value`
   - `prediction_value`
   - `output_space`
   - `prediction_space`
   - `feature_values`
   - `contributions`
   - `rank`
   - `truncated`
   - `omitted_count`

4. Add tests before implementation.

   - Identity-link GLM reconstructs prediction directly.
   - Logit-link GLM reconstructs linear predictor and response prediction.
   - Smooth terms are grouped back to the original factor.
   - Missing required inputs fail loudly.
   - Interaction and offset rows are represented explicitly.
   - Frontend ladder renders RustyStats using the same layout as CatBoost.

## Non-Goals

- Do not approximate SHAP for RustyStats.
- Do not recreate spline or encoding logic in Haute.
- Do not display internal spline basis columns by default.
- Do not silently fall back to a generic "computed" explanation if RustyStats contribution generation fails.

## Summary

RustyStats should provide GLM-native prediction contributions, and Haute should expose them using the same trace syntax as CatBoost.

This gives users one consistent preview experience:

```text
Base -> factor contribution rows -> prediction
```

while preserving the right mathematical interpretation for each model family.
