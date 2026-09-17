import { describe, expect, it } from "vitest"

import { trainingConfigurationIssues } from "../trainingObjective"

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
    [{}, ["folds", "selection rule", "seed"]],
    [{ alpha: 0, cv_folds: 5, cv_selection: "min" }, ["seed"]],
  ])("requires every cross-validation setting when the penalty is cross-validated %#", (settings, missing) => {
    expect(trainingConfigurationIssues({
      algorithm: "glm",
      target: "loss",
      family: "poisson",
      terms: { age: { type: "linear" } },
      regularization: "ridge",
      evaluation,
      ...settings,
    })).toEqual([{
      code: "glm-cross-validation",
      message: `Set the cross-validation ${missing.join(", ")} so the selected penalty is reproducible.`,
    }])
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
