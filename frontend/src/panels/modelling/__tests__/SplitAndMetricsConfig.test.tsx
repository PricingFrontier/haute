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
    rowLimit: null,
    onRowLimitChange: vi.fn(),
    evaluation: DEFAULT_EVALUATION,
    onEvaluationChange: vi.fn(),
    refitOnDevelopment: true,
    onRefitOnDevelopmentChange: vi.fn(),
    tuningEnabled: false,
    preview: null,
    ...overrides,
  }
}

describe("SplitAndMetricsConfig", () => {
  it("lets a holdout run save its validation fit instead of refitting", () => {
    const onRefitOnDevelopmentChange = vi.fn()
    const props = makeProps({ onRefitOnDevelopmentChange })
    const { rerender } = render(<SplitAndMetricsConfig {...props} />)
    const checkbox = screen.getByRole("checkbox", { name: "Refit on training + validation" })
    expect(checkbox).toBeChecked()
    fireEvent.click(checkbox)
    expect(onRefitOnDevelopmentChange).toHaveBeenCalledWith(false)

    rerender(<SplitAndMetricsConfig {...props} tuningEnabled />)
    expect(screen.getByRole("checkbox", { name: "Refit on training + validation" })).toBeDisabled()
  })

  it("labels the three evaluation sections", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)

    expect(screen.getByRole("heading", { name: "Split strategy" })).toBeInTheDocument()
    expect(screen.getByText("Random split")).toBeInTheDocument()
    expect(screen.getByText("Group split")).toBeInTheDocument()
    expect(screen.getByText("Time-based split")).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "Validation strategy" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("heading", { name: "Test set" }),
    ).toBeInTheDocument()
  })

  it("highlights the active data structure", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    expect(screen.getByText("Random split")).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("switches strategy as one canonical evaluation update", () => {
    const onEvaluationChange = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onEvaluationChange })} />)

    fireEvent.click(screen.getByText("Time-based split"))

    expect(onEvaluationChange).toHaveBeenCalledWith({
      schema_version: 1,
      strategy: "temporal",
      date_column: "",
      validation: { method: "single", start: "" },
    })
  })

  it("shows validation and final-test controls with the seed kept internal", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)

    expect(screen.getByLabelText("Validation set (%)")).toHaveValue(20)
    expect(screen.queryByRole("checkbox", { name: /test set/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText("Test set (%)")).toHaveValue(20)
    expect(screen.queryByLabelText("Evaluation seed")).not.toBeInTheDocument()
  })

  it("defaults the test percentage to zero and commits a positive percentage as a fraction", () => {
    const evaluation = {
      schema_version: 1,
      strategy: "random",
      seed: 123,
      validation: { method: "single", size: 0.2 },
    }
    const onEvaluationChange = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ evaluation, onEvaluationChange })} />)

    const input = screen.getByLabelText("Test set (%)")
    expect(input).toHaveValue(0)
    fireEvent.change(input, { target: { value: "12.5" } })
    expect(onEvaluationChange).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(onEvaluationChange).toHaveBeenCalledWith({ ...evaluation, test: { size: 0.125 } })
  })

  it.each(["-1", "100"])("rejects an out-of-range test percentage of %s", (value) => {
    const onEvaluationChange = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onEvaluationChange })} />)

    const input = screen.getByLabelText("Test set (%)")
    fireEvent.change(input, { target: { value } })
    fireEvent.blur(input)
    expect(onEvaluationChange).not.toHaveBeenCalled()
    expect(input).toHaveAttribute("aria-invalid", "true")
  })

  it("adds and removes a temporal test set through its optional start date", () => {
    const evaluation = {
      schema_version: 1,
      strategy: "temporal",
      date_column: "date_col",
      validation: { method: "single", start: "2025-01-01" },
    }
    const onEvaluationChange = vi.fn()
    const props = makeProps({ evaluation, onEvaluationChange })
    const { rerender } = render(<SplitAndMetricsConfig {...props} />)

    expect(screen.getByLabelText("Test starts")).toHaveValue("")
    fireEvent.change(screen.getByLabelText("Test starts"), { target: { value: "2025-06-01" } })
    const withTest = { ...evaluation, test: { start: "2025-06-01" } }
    expect(onEvaluationChange).toHaveBeenLastCalledWith(withTest)

    rerender(<SplitAndMetricsConfig {...props} evaluation={withTest} />)
    fireEvent.change(screen.getByLabelText("Test starts"), { target: { value: "" } })
    expect(onEvaluationChange).toHaveBeenLastCalledWith(evaluation)
  })

  it("starts with no test percentage when switching from a temporal boundary", () => {
    const onEvaluationChange = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({
      onEvaluationChange,
      evaluation: {
        schema_version: 1,
        strategy: "temporal",
        date_column: "date_col",
        validation: { method: "single", start: "2025-01-01" },
        test: { start: "2025-06-01" },
      },
    })} />)

    fireEvent.click(screen.getByText("Random split"))
    expect(onEvaluationChange).toHaveBeenCalledWith({
      schema_version: 1,
      strategy: "random",
      seed: 42,
      validation: { method: "single", size: 0.2 },
    })
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
    expect(screen.getByLabelText("Test starts")).toHaveValue("2025-06-01")
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

    expect(screen.getByLabelText("Group column")).toHaveValue("group_id")
    expect(screen.getByLabelText("Fold count")).toHaveValue(5)
    expect(screen.getByLabelText("Test set (%)")).toHaveValue(0)
  })

  it("updates validation and final-test configuration without retired fields", () => {
    const onEvaluationChange = vi.fn()
    const evaluation = { ...DEFAULT_EVALUATION, seed: 123 }
    const props = makeProps({ evaluation, onEvaluationChange })
    const { rerender } = render(<SplitAndMetricsConfig {...props} />)

    fireEvent.change(screen.getByLabelText("Validation set (%)"), {
      target: { value: "30" },
    })
    fireEvent.blur(screen.getByLabelText("Validation set (%)"))
    expect(onEvaluationChange).toHaveBeenLastCalledWith({
      ...evaluation,
      validation: { method: "single", size: 0.3 },
    })

    fireEvent.change(screen.getByLabelText("Test set (%)"), {
      target: { value: "0" },
    })
    fireEvent.blur(screen.getByLabelText("Test set (%)"))
    const { test: _test, ...withoutTest } = evaluation
    void _test
    expect(onEvaluationChange).toHaveBeenLastCalledWith(withoutTest)

    rerender(<SplitAndMetricsConfig {...props} evaluation={withoutTest} />)
    expect(screen.getByLabelText("Test set (%)")).toHaveValue(0)
    expect(screen.getByLabelText("Data allocation")).not.toHaveTextContent("Test set")
  })

  it("shows labelled target partitions without instructional prose", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)

    const allocation = screen.getByLabelText("Data allocation")
    expect(allocation).toHaveTextContent("60% Training set")
    expect(allocation).toHaveTextContent("20% Validation set")
    expect(allocation).toHaveTextContent("20% Test set")
    expect(allocation).not.toHaveTextContent(/Refit on|During model selection|percentages of eligible source data/)
  })

  it("shows two partitions when validation has no final test", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            ...DEFAULT_EVALUATION,
            validation: { method: "single", size: 0.2 },
            test: undefined,
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "80% Training set",
    )
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "20% Validation set",
    )
  })

  it("does not imply a selection fit when validation is disabled", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            ...DEFAULT_EVALUATION,
            validation: { method: "none" },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "80% Training set",
    )
    expect(screen.getByLabelText("Data allocation")).not.toHaveTextContent("Validation set")
    expect(screen.queryByText(/Refit on/)).not.toBeInTheDocument()
  })

  it("commits percentages as fractions", () => {
    const onEvaluationChange = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onEvaluationChange })} />)

    fireEvent.change(screen.getByLabelText("Validation set (%)"), {
      target: { value: "30" },
    })
    fireEvent.keyDown(screen.getByLabelText("Validation set (%)"), {
      key: "Enter",
    })

    expect(onEvaluationChange).toHaveBeenLastCalledWith({
      ...DEFAULT_EVALUATION,
      validation: { method: "single", size: 0.3 },
    })
  })

  it("shows the fraction-sum error and hides a misleading allocation", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            ...DEFAULT_EVALUATION,
            validation: { method: "single", size: 0.8 },
            test: { size: 0.5 },
          },
        })}
      />,
    )

    expect(
      screen.getByText(
        "Validation and test must total below 100% so training retains some rows.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Data allocation")).not.toBeInTheDocument()
  })

  it("uses compatible grouped preview counts instead of target ratios", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            ...DEFAULT_EVALUATION,
            strategy: "group",
            group_column: "group_id",
          },
          preview: {
            schema_version: 1,
            strategy: "group",
            validation_method: "single",
            development_rows: 70,
            final_test_rows: 30,
            validation_fit_count: 1,
            min_selection_train_rows: 50,
            max_selection_train_rows: 50,
            min_selection_validation_rows: 20,
            max_selection_validation_rows: 20,
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "50 Training set",
    )
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "30 Test set",
    )
    expect(screen.getByLabelText("Data allocation")).not.toHaveTextContent("rows per fit")
  })

  it("labels the cross-validation folds", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            ...DEFAULT_EVALUATION,
            validation: { method: "cross_validation", fold_count: 5 },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "5 CV folds",
    )
    expect(screen.getByLabelText("Data allocation")).not.toHaveTextContent(/Refit on|remains held out/)
  })

  it("uses the configured final-test fraction in the cross-validation schematic", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            ...DEFAULT_EVALUATION,
            test: { size: 0.4 },
            validation: { method: "cross_validation", fold_count: 5 },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "60% Training set",
    )
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "40% Test set",
    )
  })

  it("labels temporal cross-validation as expanding windows", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date_col",
            validation: {
              method: "cross_validation",
              fold_count: 5,
              window: "expanding",
            },
            test: { start: "2025-06-01" },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "Expanding-window CV · 5 folds",
    )
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "Awaiting preview",
    )
  })

  it("waits for an exact temporal preview before drawing allocation widths", () => {
    const { rerender } = render(
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
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "Awaiting preview",
    )

    rerender(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date_col",
            validation: { method: "single", start: "2025-01-01" },
            test: { start: "2025-06-01" },
          },
          preview: {
            schema_version: 1,
            strategy: "temporal",
            validation_method: "single",
            development_rows: 80,
            final_test_rows: 20,
            validation_fit_count: 1,
            min_selection_train_rows: 60,
            max_selection_train_rows: 60,
            min_selection_validation_rows: 20,
            max_selection_validation_rows: 20,
          },
        })}
      />,
    )
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "60 Training set",
    )
  })

  it("uses exact temporal development and final-test counts without validation", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date_col",
            validation: { method: "none" },
            test: { start: "2025-06-01" },
          },
          preview: {
            schema_version: 1,
            strategy: "temporal",
            validation_method: "none",
            development_rows: 800,
            final_test_rows: 200,
            validation_fit_count: 0,
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "800 Training set",
    )
    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "200 Test set",
    )
  })

  it("does not guess temporal no-validation allocation before preview", () => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date_col",
            validation: { method: "none" },
            test: { start: "2025-06-01" },
          },
        })}
      />,
    )

    expect(screen.getByLabelText("Data allocation")).toHaveTextContent(
      "Awaiting preview",
    )
    expect(screen.queryByText(/% Training set/)).not.toBeInTheDocument()
  })

  it.each(["random", "temporal"] as const)("shows exact %s CV counts only in the top allocation", (strategy) => {
    render(
      <SplitAndMetricsConfig
        {...makeProps({
          evaluation: strategy === "random" ? {
            ...DEFAULT_EVALUATION,
            validation: { method: "cross_validation", fold_count: 5 },
          } : {
            schema_version: 1,
            strategy: "temporal",
            date_column: "date_col",
            validation: { method: "cross_validation", fold_count: 5, window: "expanding" },
            test: { start: "2025-06-01" },
          },
          preview: {
            schema_version: 1,
            strategy,
            validation_method: "cross_validation",
            development_rows: 800,
            final_test_rows: 200,
            validation_fit_count: 5,
            min_selection_train_rows: 600,
            max_selection_train_rows: 640,
            min_selection_validation_rows: 160,
            max_selection_validation_rows: 200,
          },
        })}
      />,
    )

    const allocation = screen.getByLabelText("Data allocation")
    expect(allocation).toHaveTextContent("800 Training set (80%)")
    expect(allocation).toHaveTextContent("200 Test set (20%)")
    expect(allocation).toHaveTextContent("Training rows per fit: 600–640")
    expect(allocation).toHaveTextContent("Validation rows per fit: 160–200")
    expect(screen.queryByLabelText("Exact evaluation preview")).not.toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "Exact evaluation" })).not.toBeInTheDocument()
  })
})
