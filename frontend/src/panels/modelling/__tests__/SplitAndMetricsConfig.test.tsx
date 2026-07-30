import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import { SplitAndMetricsConfig } from "../SplitAndMetricsConfig"
import type { SplitAndMetricsConfigProps } from "../SplitAndMetricsConfig"

afterEach(cleanup)

const COLUMNS = [
  { name: "loss_amount", dtype: "Float64" },
  { name: "date_col", dtype: "Date" },
  { name: "group_id", dtype: "Utf8" },
  { name: "age", dtype: "Int64" },
]

const DEFAULT_EVALUATION = {
  schema_version: 1,
  strategy: "random",
  seed: 42,
  test: { size: 0.2 },
  validation: { method: "single", size: 0.2 },
}

function makeProps(
  overrides: Partial<SplitAndMetricsConfigProps> = {},
): SplitAndMetricsConfigProps {
  return {
    columns: COLUMNS,
    evaluation: DEFAULT_EVALUATION,
    onEvaluationChange: vi.fn(),
    preview: null,
    ...overrides,
  }
}

describe("SplitAndMetricsConfig", () => {
  it("asks the three canonical evaluation questions", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)

    expect(screen.getByText("How is the data structured?")).toBeInTheDocument()
    expect(screen.getByText("Random rows")).toBeInTheDocument()
    expect(screen.getByText("Keep entities together")).toBeInTheDocument()
    expect(screen.getByText("Respect time order")).toBeInTheDocument()
    expect(screen.getByText("How should candidates be validated?")).toBeInTheDocument()
    expect(screen.getByText("Reserve an untouched final test?")).toBeInTheDocument()
  })

  it("highlights the active data structure", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    expect(screen.getByText("Random rows")).toHaveAttribute("aria-pressed", "true")
  })

  it("switches strategy as one canonical evaluation update", () => {
    const onEvaluationChange = vi.fn()
    render(
      <SplitAndMetricsConfig
        {...makeProps({ onEvaluationChange })}
      />,
    )

    fireEvent.click(screen.getByText("Respect time order"))

    expect(onEvaluationChange).toHaveBeenCalledWith({
      schema_version: 1,
      strategy: "temporal",
      date_column: "",
      test: { start: "" },
      validation: { method: "single", start: "" },
    })
  })

  it("shows random validation, final-test and seed controls", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)

    expect(screen.getByLabelText("Validation fraction")).toHaveValue(0.2)
    expect(screen.getByLabelText("Reserve final test")).toBeChecked()
    expect(screen.getByLabelText("Final test fraction")).toHaveValue(0.2)
    expect(screen.getByLabelText("Evaluation seed")).toHaveValue(42)
  })

  it("shows temporal boundary controls", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date_col",
            validation: { method: "single", start: "2025-01-01" },
            test: { start: "2025-06-01" },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Date column")).toHaveValue("date_col")
    expect(screen.getByLabelText("Validation starts")).toHaveValue("2025-01-01")
    expect(screen.getByLabelText("Final test starts")).toHaveValue("2025-06-01")
  })

  it("shows entity column for grouped data", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            schema_version: 1,
            strategy: "group",
            group_column: "group_id",
            seed: 42,
            validation: { method: "cross_validation", fold_count: 5 },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Entity column")).toHaveValue("group_id")
    expect(screen.getByLabelText("Fold count")).toHaveValue(5)
  })

  it("updates validation and final-test configuration without retired fields", () => {
    const onEvaluationChange = vi.fn()
    render(
      <SplitAndMetricsConfig
        {...makeProps({ onEvaluationChange })}
      />,
    )

    fireEvent.change(screen.getByLabelText("Validation fraction"), {
      target: { value: "0.3" },
    })
    expect(onEvaluationChange).toHaveBeenLastCalledWith({
      ...DEFAULT_EVALUATION,
      validation: { method: "single", size: 0.3 },
    })

    fireEvent.click(screen.getByLabelText("Reserve final test"))
    const { test: _test, ...withoutTest } = DEFAULT_EVALUATION
    void _test
    expect(onEvaluationChange).toHaveBeenLastCalledWith(withoutTest)
  })

  it("shows the exact random evaluation preview", () => {
    render(<SplitAndMetricsConfig {...makeProps({ preview: {
      schema_version: 1, strategy: "random", validation_method: "cross_validation",
      development_rows: 800, final_test_rows: 200, validation_fit_count: 5,
      min_selection_train_rows: 600, max_selection_train_rows: 640,
      min_selection_validation_rows: 160, max_selection_validation_rows: 200,
    } })} />)

    expect(screen.getByLabelText("Exact evaluation preview")).toHaveTextContent("Development rows: 800")
    expect(screen.getByLabelText("Exact evaluation preview")).toHaveTextContent("Selection train rows: min 600, max 640")
  })

  it("shows group counts in the exact evaluation preview", () => {
    render(<SplitAndMetricsConfig {...makeProps({ preview: {
      schema_version: 1, strategy: "group", validation_method: "single",
      development_rows: 80, final_test_rows: 20, validation_fit_count: 1,
      development_group_count: 8, final_test_group_count: 2,
    } })} />)

    expect(screen.getByLabelText("Exact evaluation preview")).toHaveTextContent("Development groups: 8")
    expect(screen.getByLabelText("Exact evaluation preview")).toHaveTextContent("Final-test groups: 2")
  })

  it("shows temporal ranges in the exact evaluation preview", () => {
    render(<SplitAndMetricsConfig {...makeProps({ preview: {
      schema_version: 1, strategy: "temporal", validation_method: "single",
      development_rows: 90, final_test_rows: 10, validation_fit_count: 1,
      development_date_range: { start: "2024-01-01", end: "2024-09-30" },
      final_test_date_range: { start: "2024-10-01", end: "2024-12-31" },
    } })} />)

    expect(screen.getByLabelText("Exact evaluation preview")).toHaveTextContent("Development dates: 2024-01-01 to 2024-09-30")
    expect(screen.getByLabelText("Exact evaluation preview")).toHaveTextContent("Final-test dates: 2024-10-01 to 2024-12-31")
  })
})
