import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import { TargetAndTaskConfig } from "../TargetAndTaskConfig"
import type { TargetAndTaskConfigProps } from "../TargetAndTaskConfig"

afterEach(cleanup)

const COLUMNS = [
  { name: "loss_amount", dtype: "Float64" },
  { name: "exposure", dtype: "Float64" },
  { name: "region", dtype: "Utf8" },
  { name: "age", dtype: "Int64" },
]

function makeProps(overrides: Partial<TargetAndTaskConfigProps> = {}): TargetAndTaskConfigProps {
  return {
    config: {},
    onUpdate: vi.fn(),
    columns: COLUMNS,
    target: "loss_amount",
    weight: "",
    metrics: ["gini", "rmse"],
    ...overrides,
  }
}

describe("TargetAndTaskConfig", () => {
  it("renders target column select with all columns as options", () => {
    render(<TargetAndTaskConfig {...makeProps()} />)
    const targetSelect = screen.getByRole("button", { name: "Target column" })
    expect(targetSelect).toHaveTextContent("loss_amount")
    fireEvent.click(targetSelect)
    // All columns should be options
    COLUMNS.forEach(c => {
      expect(screen.getAllByText(new RegExp(c.name)).length).toBeGreaterThan(0)
    })
  })

  it("calls onUpdate when target column changes", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    fireEvent.click(screen.getByRole("button", { name: "Target column" }))
    fireEvent.click(screen.getByRole("option", { name: /age.*Int64/ }))
    expect(onUpdate).toHaveBeenCalledWith("target", "age")
  })

  it("calls onUpdate when weight column changes", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    fireEvent.click(screen.getByRole("button", { name: "Weight column" }))
    fireEvent.click(screen.getByRole("option", { name: /exposure.*Float64/ }))
    expect(onUpdate).toHaveBeenCalledWith("weight", "exposure")
  })

  it("does not render a separate task selector", () => {
    render(<TargetAndTaskConfig {...makeProps()} />)
    expect(screen.queryByText("Task")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "regression" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "classification" })).not.toBeInTheDocument()
  })

  it("shows every supported loss regardless of the stored task", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { task: "regression" } })} />)
    const losses = within(screen.getByRole("group", { name: "Loss functions" }))
    for (const loss of ["RMSE", "MAE", "Poisson", "Tweedie", "Logloss", "CrossEntropy"]) {
      expect(losses.getByRole("button", { name: loss })).toBeInTheDocument()
    }
  })

  it("selecting a regression loss derives the task and objective-matched metrics", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Loss functions" })).getByRole("button", { name: "RMSE" }))
    expect(onUpdate).toHaveBeenCalledWith({
      loss_function: "RMSE",
      task: "regression",
      metrics: ["gini", "rmse"],
    })
  })

  it("selecting a classification loss derives the task and objective-matched metrics", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Loss functions" })).getByRole("button", { name: "Logloss" }))
    expect(onUpdate).toHaveBeenCalledWith({
      loss_function: "Logloss",
      task: "classification",
      metrics: ["auc", "logloss"],
    })
  })

  it("deselecting a selected loss function sets null", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "RMSE" } })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Loss functions" })).getByRole("button", { name: "RMSE" }))
    expect(onUpdate).toHaveBeenCalledWith("loss_function", null)
  })

  it("shows missing Tweedie power as unset instead of implying a saved value", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "Tweedie", variance_power: null } })} />)
    expect(screen.getByText(/Variance power/)).toBeInTheDocument()
    expect(screen.queryByRole("slider")).toBeNull()
    expect(screen.getByRole("spinbutton", { name: "Variance power" })).toHaveValue(null)
    expect(screen.queryByRole("button", { name: /Set variance power/ })).not.toBeInTheDocument()
  })

  it("shows a previously stored Tweedie variance power", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "Tweedie", variance_power: 1.7 } })} />)
    expect(screen.getByRole("slider")).toHaveValue("1.7")
    expect(screen.getByRole("spinbutton", { name: "Variance power" })).toHaveValue(1.7)
  })

  it("selecting Tweedie initializes an absent variance power in the same update", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Loss functions" })).getByRole("button", { name: "Tweedie" }))
    expect(onUpdate).toHaveBeenCalledWith({
      loss_function: "Tweedie",
      task: "regression",
      metrics: ["gini", "tweedie_deviance"],
      variance_power: 1.5,
    })
  })

  it("writes changes made with the Tweedie slider", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "Tweedie", variance_power: 1.5 } })} />)
    fireEvent.change(screen.getByRole("slider"), { target: { value: "1.7" } })
    expect(onUpdate).toHaveBeenCalledWith("variance_power", 1.7)
  })

  it("does not show Tweedie slider for other loss functions", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "RMSE" } })} />)
    expect(screen.queryByText(/Variance power/)).not.toBeInTheDocument()
  })

  it("shows every metric and disables classification metrics for a regression loss", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "RMSE" } })} />)
    const metricButtons = within(screen.getByRole("group", { name: "Metrics" }))
    expect(metricButtons.getByRole("button", { name: "Gini" })).toBeEnabled()
    expect(metricButtons.getByRole("button", { name: "R²" })).toBeEnabled()
    expect(metricButtons.getByRole("button", { name: "AUC" })).toBeDisabled()
    expect(metricButtons.getByRole("button", { name: "Logloss" })).toBeDisabled()
  })

  it("shows every metric and disables regression metrics for a classification loss", () => {
    render(<TargetAndTaskConfig {...makeProps({
      config: { loss_function: "Logloss" },
      metrics: ["auc", "logloss"],
    })} />)
    const metricButtons = within(screen.getByRole("group", { name: "Metrics" }))
    expect(metricButtons.getByRole("button", { name: "AUC" })).toBeEnabled()
    expect(metricButtons.getByRole("button", { name: "Logloss" })).toBeEnabled()
    expect(metricButtons.getByRole("button", { name: "Gini" })).toBeDisabled()
    expect(metricButtons.getByRole("button", { name: "RMSE" })).toBeDisabled()
  })

  it("disables all metrics until a loss is selected", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: {} })} />)
    const metricButtons = within(screen.getByRole("group", { name: "Metrics" })).getAllByRole("button")
    expect(metricButtons).toHaveLength(10)
    metricButtons.forEach(button => expect(button).toBeDisabled())
  })

  it("does not update metrics when an incompatible metric is clicked", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "RMSE" } })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Metrics" })).getByRole("button", { name: "AUC" }))
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("toggling a metric adds it", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "RMSE" }, metrics: ["gini"] })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Metrics" })).getByRole("button", { name: "RMSE" }))
    expect(onUpdate).toHaveBeenCalledWith("metrics", ["gini", "rmse"])
  })

  it("toggling a selected metric removes it", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "RMSE" }, metrics: ["gini", "rmse"] })} />)
    fireEvent.click(within(screen.getByRole("group", { name: "Metrics" })).getByRole("button", { name: "Gini" }))
    expect(onUpdate).toHaveBeenCalledWith("metrics", ["rmse"])
  })
  it("supports precise Tweedie power while rejecting boundary drafts", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "Tweedie", variance_power: 1.5 } })} />)
    const input = screen.getByRole("spinbutton", { name: "Variance power" })
    fireEvent.change(input, { target: { value: "1.99" } })
    fireEvent.blur(input)
    expect(onUpdate).toHaveBeenLastCalledWith("variance_power", 1.99)
    onUpdate.mockClear()
    fireEvent.change(input, { target: { value: "2" } })
    fireEvent.blur(input)
    expect(onUpdate).not.toHaveBeenCalled()
    expect(input).toHaveAttribute("aria-invalid", "true")
  })
  it("offers numeric columns only for weight and offset", () => {
    render(<TargetAndTaskConfig {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: "Weight column" }))
    expect(screen.queryByRole("option", { name: /region/ })).toBeNull()
    expect(screen.queryByRole("option", { name: /loss_amount/ })).toBeNull()
    fireEvent.click(screen.getByRole("option", { name: /^None$/ }))
    fireEvent.click(screen.getByRole("button", { name: "Offset column" }))
    expect(screen.getByRole("option", { name: /exposure/ })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /region/ })).toBeNull()
  })


  describe("model-family capabilities and binary classes (MOD-F01)", () => {
    const CLASSIFICATION_COLUMNS = [
      { name: "outcome", dtype: "String" },
      { name: "flag", dtype: "Boolean" },
      { name: "grade", dtype: "Int64" },
      { name: "x", dtype: "Float64" },
    ]

    it("offers exactly the losses the family's capabilities list", () => {
      render(<TargetAndTaskConfig {...makeProps({ algorithm: "catboost" })} />)
      const group = screen.getByRole("group", { name: "Loss functions" })
      const offered = within(group).getAllByRole("button").map((button) => button.textContent)
      expect(offered).toEqual(["RMSE", "MAE", "Poisson", "Tweedie", "Logloss", "CrossEntropy"])
      expect(screen.getByLabelText("Selected algorithm")).toHaveTextContent("CatBoost")
    })

    it("requires a positive class for a text classification target", () => {
      const onUpdate = vi.fn()
      render(
        <TargetAndTaskConfig
          {...makeProps({
            onUpdate,
            columns: CLASSIFICATION_COLUMNS,
            target: "outcome",
            config: { loss_function: "Logloss", task: "classification" },
          })}
        />,
      )
      expect(screen.getByRole("alert")).toHaveTextContent("Choose which label is the positive class.")
      fireEvent.change(screen.getByLabelText("Positive class"), { target: { value: "claim" } })
      expect(onUpdate).toHaveBeenCalledWith("positive_class", "claim")
    })

    it("hides the positive class for a Boolean target", () => {
      render(
        <TargetAndTaskConfig
          {...makeProps({
            columns: CLASSIFICATION_COLUMNS,
            target: "flag",
            config: { loss_function: "Logloss", task: "classification" },
          })}
        />,
      )
      expect(screen.queryByLabelText("Positive class")).toBeNull()
    })

    it("keeps an integer target's positive class numeric and optional", () => {
      const onUpdate = vi.fn()
      render(
        <TargetAndTaskConfig
          {...makeProps({
            onUpdate,
            columns: CLASSIFICATION_COLUMNS,
            target: "grade",
            config: { loss_function: "Logloss", task: "classification" },
          })}
        />,
      )
      expect(screen.queryByRole("alert")).toBeNull()
      fireEvent.change(screen.getByLabelText("Positive class"), { target: { value: "2" } })
      expect(onUpdate).toHaveBeenCalledWith("positive_class", 2)
    })

    it("clears a saved positive class to null", () => {
      const onUpdate = vi.fn()
      render(
        <TargetAndTaskConfig
          {...makeProps({
            onUpdate,
            columns: CLASSIFICATION_COLUMNS,
            target: "grade",
            config: { loss_function: "Logloss", task: "classification", positive_class: 2 },
          })}
        />,
      )
      expect(screen.getByLabelText("Positive class")).toHaveValue("2")
      fireEvent.change(screen.getByLabelText("Positive class"), { target: { value: "" } })
      expect(onUpdate).toHaveBeenCalledWith("positive_class", null)
    })

    it("shows no positive class for a regression objective", () => {
      render(
        <TargetAndTaskConfig
          {...makeProps({
            columns: CLASSIFICATION_COLUMNS,
            target: "outcome",
            config: { loss_function: "RMSE", task: "regression" },
          })}
        />,
      )
      expect(screen.queryByLabelText("Positive class")).toBeNull()
    })
  })
})
