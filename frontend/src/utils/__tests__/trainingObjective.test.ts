import { describe, expect, it } from "vitest"

import { effectiveMetrics, trainingConfigurationIssues, trainingIssuePane } from "../trainingObjective"

describe("trainingConfigurationIssues", () => {
  const evaluation = {
    schema_version: 1,
    strategy: "random",
    seed: 42,
    validation: { method: "single", size: 0.2 },
  }

  it("aggregates independent CatBoost requirements", () => {
    expect(
      trainingConfigurationIssues({ algorithm: "catboost" }).map(
        (issue) => issue.code,
      ),
    ).toEqual([
      "training-target",
      "evaluation-config",
      "catboost-loss-function",
    ])
  })

  it.each([1, 2, 0, NaN, Infinity, "1.5"])("rejects invalid CatBoost Tweedie power %s", (variance_power) => {
    expect(trainingConfigurationIssues({ algorithm: "catboost", target: "y", loss_function: "Tweedie", variance_power, evaluation }))
      .toEqual([expect.objectContaining({ code: "catboost-tweedie-variance-power" })])
  })

  it.each([0.5, 0.8])("rejects validation plus final test consuming all rows (%s)", (size) => {
    const issues = trainingConfigurationIssues({ algorithm: "catboost", target: "y", loss_function: "RMSE",
      evaluation: { ...evaluation, validation: { method: "single", size }, test: { size: 0.5 } },
    })
    expect(issues).toEqual([expect.objectContaining({ code: "evaluation-config", message: expect.stringMatching(/below 100%/) })])
  })

  it("refuses LightGBM monotonicity under MAE on the Features pane (MOD-F03)", () => {
    const base = { algorithm: "lightgbm", target: "y", loss_function: "MAE", evaluation }
    const issues = trainingConfigurationIssues({ ...base, monotone_constraints: { age: 1 } })
    expect(issues).toEqual([expect.objectContaining({
      code: "monotone-loss",
      message: expect.stringMatching(/LightGBM cannot apply monotonicity constraints with the MAE loss/),
    })])
    expect(trainingIssuePane(issues[0])).toBe("features")
    // Dormant (excluded) constraints, other losses and other families are fine.
    expect(trainingConfigurationIssues({ ...base, monotone_constraints: { age: 1 }, exclude: ["age"] })).toEqual([])
    // Explicit feature_columns override a stale exclusion, as in the backend.
    expect(trainingConfigurationIssues({
      ...base, monotone_constraints: { age: 1 }, exclude: ["age"], feature_columns: ["age"],
    }).map((issue) => issue.code)).toEqual(["monotone-loss"])
    expect(trainingConfigurationIssues({ ...base, loss_function: "RMSE", monotone_constraints: { age: 1 } })).toEqual([])
    expect(trainingConfigurationIssues({ ...base, algorithm: "xgboost", monotone_constraints: { age: 1 } })).toEqual([])
  })

  it("mirrors the backend's EBM budget and interaction rules (MOD-F04)", () => {
    const base = { algorithm: "ebm", target: "y", loss_function: "RMSE", evaluation }
    const codes = (config: Record<string, unknown>) =>
      trainingConfigurationIssues({ ...base, ...config }).map((issue) => [issue.code, trainingIssuePane(issue)])
    expect(codes({ params: { max_rounds: 500, interactions: 5 } })).toEqual([])
    expect(codes({ params: { interactions: 5 } })).toEqual([["ebm-max-rounds", "params"]])
    expect(codes({ params: { max_rounds: 0 } })).toEqual([["ebm-max-rounds", "params"]])
    expect(codes({ params: { max_rounds: 5, interactions: -1 } })).toEqual([["ebm-interactions", "features"]])
    expect(codes({ params: { max_rounds: 5, interactions: [["a", ""]] } })).toEqual([["ebm-interactions", "features"]])
    expect(codes({ params: { max_rounds: 5, interactions: [["a", "b"], ["b", "a"]] } })).toEqual([["ebm-interactions", "features"]])
    const monotone = trainingConfigurationIssues({
      ...base,
      params: { max_rounds: 5, interactions: [["age", "region"]] },
      monotone_constraints: { age: 1 },
    })
    expect(monotone).toEqual([expect.objectContaining({
      code: "ebm-interactions",
      message: expect.stringMatching(/involves monotone-constrained age/),
    })])
  })

  it("reports conditional CatBoost Tweedie configuration", () => {
    expect(
      trainingConfigurationIssues({
        algorithm: "catboost",
        target: "loss",
        loss_function: "Tweedie",
        evaluation,
      }).map((issue) => issue.code),
    ).toEqual(["catboost-tweedie-variance-power"])
  })

  it("aggregates independent GLM requirements", () => {
    expect(
      trainingConfigurationIssues({ algorithm: "glm" }).map(
        (issue) => issue.code,
      ),
    ).toEqual([
      "training-target",
      "evaluation-config",
      "glm-family",
      "glm-terms",
    ])
  })

  it.each([
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "tweedie",
        terms: { age: { type: "linear" } },
        evaluation,
      },
      "glm-tweedie-variance-power",
    ],
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "negbinomial",
        terms: { age: { type: "linear" } },
        evaluation,
      },
      "glm-negbin-theta",
    ],
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "poisson",
        terms: { age: { type: "linear" } },
        regularization: "elastic_net",
        alpha: 0.5,
        evaluation,
      },
      "glm-elastic-net-l1-ratio",
    ],
  ])("reports the conditional GLM issue %#", (config, code) => {
    expect(trainingConfigurationIssues(config).map((issue) => issue.code)).toEqual([
      code,
    ])
  })

  it.each([
    [{}, ["folds", "selection rule"]],
    [{ alpha: 0, cv_folds: 5, cv_selection: "min" }, []],
  ])("requires editable cross-validation settings while defaulting the hidden seed %#", (settings, missing) => {
    const issues = trainingConfigurationIssues({
      algorithm: "glm",
      target: "loss",
      family: "poisson",
      terms: { age: { type: "linear" } },
      regularization: "ridge",
      evaluation,
      ...settings,
    })
    expect(issues).toEqual(missing.length ? [{
      code: "glm-cross-validation",
      message: `Set the cross-validation ${missing.join(", ")} so the selected penalty is reproducible.`,
    }] : [])
  })

  it("refuses regularization with automatic splines and robust standard errors with invalid inference", () => {
    const base = { algorithm: "glm", target: "loss", family: "poisson", evaluation }
    expect(trainingConfigurationIssues({
      ...base,
      terms: { age: { type: "bs" }, income: { type: "ns", df: 4 } },
      interactions: [{ factors: ["income", "region"], specs: { income: { type: "ns" } }, include_main: true }, null],
      regularization: "ridge",
      alpha: 0.5,
    })).toEqual([{
      code: "glm-smooth-regularization",
      message: "Regularization cannot be combined with automatically smoothed splines (age, Interaction 1 income): set Fixed df on those splines or turn regularization off.",
    }])
    const robust = (overrides: Record<string, unknown>) => trainingConfigurationIssues({
      ...base,
      terms: { age: { type: "linear" } },
      robust_standard_errors: "HC1",
      ...overrides,
    }).filter((issue) => issue.code === "glm-robust-standard-errors").map((issue) => issue.message)
    expect(robust({})).toEqual([])
    expect(robust({ regularization: "lasso", alpha: 1 })).toEqual([
      "Robust standard errors cannot be combined with regularization: RustyStats marks that inference as not valid. Turn robust standard errors off or remove the conflict.",
    ])
    expect(robust({ terms: { age: { type: "linear", monotonicity: "decreasing" } } })).toEqual([
      "Robust standard errors cannot be combined with monotonicity constraints (age): RustyStats marks that inference as not valid. Turn robust standard errors off or remove the conflict.",
    ])
    expect(robust({ terms: { age: { type: "ms", df: 5 }, income: { type: "bs" } } })).toEqual([
      "Robust standard errors cannot be combined with automatically smoothed splines (income): RustyStats marks that inference as not valid. Turn robust standard errors off or remove the conflict.",
    ])
  })

  it("returns no issues for a complete configuration", () => {
    expect(
      trainingConfigurationIssues({
        algorithm: "glm",
        target: "loss",
        family: "poisson",
        terms: { age: { type: "linear" } },
        evaluation,
      }),
    ).toEqual([])
  })

  it("reports invalid bounded tuning alongside an otherwise complete config", () => {
    expect(
      trainingConfigurationIssues({
        algorithm: "catboost",
        target: "loss",
        loss_function: "RMSE",
        metrics: ["gini", "rmse"],
        evaluation,
        tuning: {
          schema_version: 1,
          trial_count: 51,
          seed: 42,
          metric: "gini",
          search_space: {},
        },
      }).map((issue) => issue.code),
    ).toEqual(["tuning-config"])
  })

  it.each([
    {
      ...evaluation,
      test: { size: 1 },
    },
    {
      schema_version: 1,
      strategy: "temporal",
      date_column: "accident_date",
      validation: { method: "single", start: "2025-01-01" },
      test: { start: "" },
    },
  ])("reports an incomplete or invalid configured final test", (invalidEvaluation) => {
    expect(
      trainingConfigurationIssues({
        algorithm: "catboost",
        target: "loss",
        loss_function: "RMSE",
        evaluation: invalidEvaluation,
      }).map((issue) => issue.code),
    ).toEqual(["evaluation-config"])
  })

  it.each([
    { ...evaluation, test: { size: 0 } },
    { ...evaluation, validation: { method: "single", size: 0 } },
  ])("rejects zero test and validation fractions at click time", (zeroSizeEvaluation) => {
    expect(
      trainingConfigurationIssues({
        algorithm: "catboost",
        target: "loss",
        loss_function: "RMSE",
        evaluation: zeroSizeEvaluation,
      }).map((issue) => issue.code),
    ).toEqual(["evaluation-config"])
  })

  it("requires temporal validation to precede the configured final test", () => {
    expect(
      trainingConfigurationIssues({
        algorithm: "catboost",
        target: "loss",
        loss_function: "RMSE",
        evaluation: {
          schema_version: 1,
          strategy: "temporal",
          date_column: "accident_date",
          validation: { method: "single", start: "2025-07-01" },
          test: { start: "2025-06-01" },
        },
      }).map((issue) => issue.code),
    ).toEqual(["evaluation-config"])
  })
})

describe("effectiveMetrics", () => {
  it.each([
    [{ algorithm: "catboost", loss_function: "RMSE" }, ["gini", "rmse"]],
    [{ algorithm: "catboost", loss_function: "Poisson" }, ["gini", "poisson_deviance"]],
    [{ algorithm: "catboost", loss_function: "Tweedie" }, ["gini", "tweedie_deviance"]],
    [{ algorithm: "catboost", loss_function: "Logloss", task: "regression" }, ["auc", "logloss"]],
    [{ algorithm: "catboost", task: "classification" }, ["auc", "logloss"]],
    [{ algorithm: "glm", family: "negbinomial", loss_function: "RMSE" }, ["gini", "poisson_deviance"]],
    [{ algorithm: "glm", family: "binomial" }, ["auc", "logloss"]],
    [{ algorithm: "glm" }, ["gini", "rmse"]],
    [{ algorithm: "catboost", loss_function: "Poisson", metrics: ["mae"] }, ["mae"]],
    [{ algorithm: "catboost", loss_function: "Poisson", metrics: [] }, ["gini", "poisson_deviance"]],
  ])("mirrors the backend objective defaults for %j", (config, metrics) => {
    expect(effectiveMetrics(config)).toEqual(metrics)
  })

  it("accepts a tuning metric implied by the objective when metrics are unset", () => {
    const issues = trainingConfigurationIssues({
      algorithm: "catboost",
      target: "y",
      loss_function: "Poisson",
      evaluation: {
        schema_version: 1,
        strategy: "random",
        seed: 42,
        validation: { method: "single", size: 0.2 },
      },
      tuning: {
        schema_version: 1,
        trial_count: 5,
        seed: 1,
        metric: "poisson_deviance",
        search_space: { depth: [4, 6] },
      },
    })
    expect(issues.map((issue) => issue.code)).not.toContain("tuning-config")
  })
})
