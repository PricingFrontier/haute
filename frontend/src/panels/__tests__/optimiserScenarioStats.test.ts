import { describe, expect, it } from "vitest"
import { ScenarioDataError } from "../optimiser/scenarioRows"
import { computeScenarioStatsBySeries } from "../optimiserScenarioStats"

describe("computeScenarioStatsBySeries", () => {
  it("throws when rows disagree on the value for the same scenario index", () => {
    const compute = () =>
      computeScenarioStatsBySeries({
        rows: [
          { quoteId: "Q1", scenarioIndex: 2, scenarioValue: 0.1, values: { premium: 100 } },
          { quoteId: "Q2", scenarioIndex: 2, scenarioValue: 0.2, values: { premium: 120 } },
        ],
        series: ["premium"],
        scenarioValueCol: "scenario_value",
      })
    expect(compute).toThrow(ScenarioDataError)
    expect(compute).toThrow(/Conflicting scenario_value/)
  })

  it("leaves a row without a series value out of that series alone", () => {
    const stats = computeScenarioStatsBySeries({
      rows: [
        { quoteId: "Q1", scenarioIndex: 0, scenarioValue: 1, values: { premium: 100, volume: 1 } },
        { quoteId: "Q2", scenarioIndex: 0, scenarioValue: 1, values: { volume: 3 } },
      ],
      series: ["premium", "volume"],
      scenarioValueCol: "scenario_value",
    })
    expect(stats.get("premium")?.[0]).toMatchObject({ count: 1, summary: { mean: 100, min: 100, max: 100 } })
    expect(stats.get("volume")?.[0]).toMatchObject({ count: 2, summary: { mean: 2 } })
  })
})
