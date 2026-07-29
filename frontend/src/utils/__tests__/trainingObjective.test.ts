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
      "glm-factor-selection",
    ])
  })

  it.each([
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "tweedie",
        all_factors: true,
        evaluation,
      },
      "glm-tweedie-variance-power",
    ],
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "negbinomial",
        all_factors: true,
        evaluation,
      },
      "glm-negbin-theta",
    ],
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "poisson",
        all_factors: true,
        regularization: "elastic_net",
        evaluation,
      },
      "glm-elastic-net-l1-ratio",
    ],
  ])("reports the conditional GLM issue %#", (config, code) => {
    expect(trainingConfigurationIssues(config).map((issue) => issue.code)).toEqual([
      code,
    ])
  })

  it("returns no issues for a complete configuration", () => {
    expect(
      trainingConfigurationIssues({
        algorithm: "glm",
        target: "loss",
        family: "poisson",
        all_factors: true,
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
