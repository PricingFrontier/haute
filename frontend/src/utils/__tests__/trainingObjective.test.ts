import { describe, expect, it } from "vitest"

import { trainingConfigurationIssues } from "../trainingObjective"

describe("trainingConfigurationIssues", () => {
  it("aggregates independent CatBoost requirements", () => {
    expect(
      trainingConfigurationIssues({ algorithm: "catboost" }).map(
        (issue) => issue.code,
      ),
    ).toEqual(["training-target", "catboost-loss-function"])
  })

  it("reports conditional CatBoost Tweedie configuration", () => {
    expect(
      trainingConfigurationIssues({
        algorithm: "catboost",
        target: "loss",
        loss_function: "Tweedie",
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
      },
      "glm-tweedie-variance-power",
    ],
    [
      {
        algorithm: "glm",
        target: "loss",
        family: "negbinomial",
        all_factors: true,
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
      }),
    ).toEqual([])
  })
})
