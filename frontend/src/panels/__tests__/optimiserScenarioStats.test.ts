import { describe, expect, it } from "vitest"
import { computeScenarioStatsBySeries } from "../optimiserScenarioStats"

describe("computeScenarioStatsBySeries", () => {
  it("throws when scenario inputs contain non-numeric values", () => {
    expect(() =>
      computeScenarioStatsBySeries({
        rows: [
          { scenario_index: 0, scenario_value: "0.10", premium: 100 },
          { scenario_index: 1, scenario_value: "bad", premium: 120 },
        ],
        series: ["premium"],
        scenarioIndexCol: "scenario_index",
        scenarioValueCol: "scenario_value",
      }),
    ).toThrow(/scenario_value/)
  })

  it("throws when rows disagree on the value for the same scenario index", () => {
    expect(() =>
      computeScenarioStatsBySeries({
        rows: [
          { scenario_index: 2, scenario_value: 0.1, premium: 100 },
          { scenario_index: 2, scenario_value: 0.2, premium: 120 },
        ],
        series: ["premium"],
        scenarioIndexCol: "scenario_index",
        scenarioValueCol: "scenario_value",
      }),
    ).toThrow(/Conflicting scenario_value/)
  })

  it("throws when a requested series contains non-numeric values", () => {
    expect(() =>
      computeScenarioStatsBySeries({
        rows: [
          { scenario_index: 0, scenario_value: 0.1, premium: "not-a-number" },
        ],
        series: ["premium"],
        scenarioIndexCol: "scenario_index",
        scenarioValueCol: "scenario_value",
      }),
    ).toThrow(/premium/)
  })
})
