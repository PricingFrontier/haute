import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
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
    task: "regression",
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

  it("shows regression task as active by default", () => {
    render(<TargetAndTaskConfig {...makeProps()} />)
    const regressionBtn = screen.getByText("regression")
    const classificationBtn = screen.getByText("classification")
    expect(regressionBtn.style.color).toContain("var(--accent)")
    expect(classificationBtn.style.color).not.toContain("var(--accent)")
  })

  it("switching to classification updates task and metrics", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    fireEvent.click(screen.getByText("classification"))
    expect(onUpdate).toHaveBeenCalledWith({
      task: "classification",
      metrics: ["auc", "logloss"],
      loss_function: null,
    })
  })

  it("shows regression losses when task is regression", () => {
    render(<TargetAndTaskConfig {...makeProps({ task: "regression" })} />)
    // RMSE and MAE appear as both loss buttons and metric buttons
    expect(screen.getAllByText("RMSE").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("MAE").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("Poisson")).toBeInTheDocument()
    expect(screen.getByText("Tweedie")).toBeInTheDocument()
    // Classification-only losses should not appear
    expect(screen.queryByText("CrossEntropy")).not.toBeInTheDocument()
  })

  it("shows classification losses when task is classification", () => {
    render(<TargetAndTaskConfig {...makeProps({ task: "classification" })} />)
    // Logloss appears as both loss and metric
    expect(screen.getAllByText("Logloss").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("CrossEntropy")).toBeInTheDocument()
    expect(screen.queryByText("Poisson")).not.toBeInTheDocument()
  })

  it("toggling a loss function calls onUpdate with objective-matched metrics", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate })} />)
    // RMSE appears in both loss and metrics — the loss section comes first in the DOM
    const rmseButtons = screen.getAllByText("RMSE")
    fireEvent.click(rmseButtons[0]) // first one is in the Loss section
    expect(onUpdate).toHaveBeenCalledWith({
      loss_function: "RMSE",
      metrics: ["gini", "rmse"],
    })
  })

  it("deselecting a selected loss function sets null", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "RMSE" } })} />)
    const rmseButtons = screen.getAllByText("RMSE")
    fireEvent.click(rmseButtons[0])
    expect(onUpdate).toHaveBeenCalledWith("loss_function", null)
  })

  it("gates Tweedie variance power until it is set explicitly", () => {
    // Unset: no silent 1.5 slider — a prompt to set it (the gate).
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "Tweedie" } })} />)
    expect(screen.getByText(/Variance power/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Set variance power/ })).toBeInTheDocument()
    expect(screen.queryByRole("slider")).toBeNull()
  })

  it("shows the Tweedie slider once a variance power is set", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "Tweedie", variance_power: 1.5 } })} />)
    expect(screen.getByRole("slider")).toBeInTheDocument()
  })

  it("Set variance power writes an explicit value", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, config: { loss_function: "Tweedie" } })} />)
    fireEvent.click(screen.getByRole("button", { name: /Set variance power/ }))
    expect(onUpdate).toHaveBeenCalledWith("variance_power", 1.5)
  })

  it("does not show Tweedie slider for other loss functions", () => {
    render(<TargetAndTaskConfig {...makeProps({ config: { loss_function: "RMSE" } })} />)
    expect(screen.queryByText(/Variance power/)).not.toBeInTheDocument()
  })

  it("shows regression metrics when task is regression", () => {
    render(<TargetAndTaskConfig {...makeProps()} />)
    expect(screen.getByText("Gini")).toBeInTheDocument()
    // RMSE appears in both loss and metric sections
    expect(screen.getAllByText("RMSE").length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText("MAE").length).toBeGreaterThanOrEqual(2)
  })

  it("toggling a metric adds it", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, metrics: ["gini"] })} />)
    // RMSE buttons: [0] is loss section, [1] is metrics section
    const rmseButtons = screen.getAllByText("RMSE")
    fireEvent.click(rmseButtons[rmseButtons.length - 1]) // last one is the metric button
    expect(onUpdate).toHaveBeenCalledWith("metrics", ["gini", "rmse"])
  })

  it("toggling a selected metric removes it", () => {
    const onUpdate = vi.fn()
    render(<TargetAndTaskConfig {...makeProps({ onUpdate, metrics: ["gini", "rmse"] })} />)
    fireEvent.click(screen.getByText("Gini"))
    expect(onUpdate).toHaveBeenCalledWith("metrics", ["rmse"])
  })
})
