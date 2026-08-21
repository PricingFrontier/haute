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
    const selects = screen.getAllByRole("combobox")
    // First select is target
    const targetSelect = selects[0]
    expect(targetSelect).toHaveValue("loss_amount")
    // All columns should be options
    COLUMNS.forEach(c => {
      expect(screen.getAllByText(new RegExp(c.name)).length).toBeGreaterThan(0)
    })
  })

  it("calls onUpdate when target column changes", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    const selects = screen.getAllByRole("combobox")
    fireEvent.change(selects[0], { target: { value: "age" } })
    expect(onUpdate).toHaveBeenCalledWith("target", "age")
  })

  it("calls onUpdate when weight column changes", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    const selects = screen.getAllByRole("combobox")
    // Second select is weight
    fireEvent.change(selects[1], { target: { value: "exposure" } })
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

  it("shows the Tweedie slider at 1.5 without an intermediate warning action", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "Tweedie", variance_power: null } })} />)
    expect(screen.getByText(/Variance power/)).toBeInTheDocument()
    expect(screen.getByRole("slider")).toHaveValue("1.5")
    expect(screen.getByText("1.50")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Set variance power/ })).not.toBeInTheDocument()
  })

  it("shows a previously stored Tweedie variance power", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "Tweedie", variance_power: 1.7 } })} />)
    expect(screen.getByRole("slider")).toHaveValue("1.7")
    expect(screen.getByText("1.70")).toBeInTheDocument()
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
    expect(metricButtons).toHaveLength(9)
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
})
