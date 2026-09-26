import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"

import { TrainingRunSummary } from "../TrainingRunSummary"

afterEach(cleanup)

// A three-level categorical feature beside a numeric one, as in the motor demo.
const COLUMNS = [
  { name: "avg_cheapest_5", dtype: "Float64" },
  { name: "policy_cover_type", dtype: "String" },
  { name: "ncd_years", dtype: "Int64" },
]

function catboostConfig(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    algorithm: "catboost",
    target: "avg_cheapest_5",
    loss_function: "Tweedie",
    params: { iterations: 1000, depth: 6 },
    evaluation: {
      schema_version: 1,
      strategy: "random",
      seed: 42,
      validation: { method: "single", size: 0.2 },
    },
    ...overrides,
  }
}

/** The run summary's categorical encoding line, or null when it has none. */
function categoricalEncoding(): string | null {
  const summary = screen.getByRole("region", { name: "Training run summary" })
  const term = [...summary.querySelectorAll("dt")].find(
    (element) => element.textContent === "Categorical encoding",
  )
  return term?.nextElementSibling?.textContent ?? null
}

describe("TrainingRunSummary categorical encoding", () => {
  it("names one-hot encoding up to one_hot_max_size levels", () => {
    render(
      <TrainingRunSummary
        config={catboostConfig({ params: { iterations: 1000, one_hot_max_size: 10 } })}
        columns={COLUMNS}
        preview={null}
      />,
    )
    expect(categoricalEncoding()).toBe("One-hot up to 10 levels, target statistics above")
  })

  it("names CatBoost's default without claiming its level count", () => {
    render(<TrainingRunSummary config={catboostConfig()} columns={COLUMNS} preview={null} />)
    expect(categoricalEncoding()).toBe("CatBoost's default (target statistics)")
  })

  it("says tuning chooses the encoding when one_hot_max_size is searched", () => {
    render(
      <TrainingRunSummary
        config={catboostConfig({
          params: { iterations: 1000, one_hot_max_size: 10 },
          tuning: { trial_count: 5, search_space: { one_hot_max_size: [2, 10, 255] } },
        })}
        columns={COLUMNS}
        preview={null}
      />,
    )
    expect(categoricalEncoding()).toBe("Tuned (one_hot_max_size is in the search space)")
  })

  it.each([
    ["Categorical(ordering='physical')", true],
    ["Utf8", true],
    // The backend trains only String and Categorical columns as categorical.
    ["Enum(categories=['a', 'b', 'c'])", false],
    ["Boolean", false],
  ])("treats a %s column as categorical: %s", (dtype, categorical) => {
    const columns = [COLUMNS[0], { name: "policy_cover_type", dtype }]
    render(<TrainingRunSummary config={catboostConfig()} columns={columns} preview={null} />)
    expect(categoricalEncoding() !== null).toBe(categorical)
  })

  it.each([
    ["the categorical column is excluded", catboostConfig({ exclude: ["policy_cover_type"] })],
    ["the family is not CatBoost", catboostConfig({ algorithm: "xgboost" })],
  ])("names no encoding when %s", (_case, config) => {
    render(<TrainingRunSummary config={config} columns={COLUMNS} preview={null} />)
    expect(categoricalEncoding()).toBeNull()
  })
})
