import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import { SplitAndMetricsConfig } from "../SplitAndMetricsConfig"
import type { SplitAndMetricsConfigProps } from "../SplitAndMetricsConfig"

afterEach(cleanup)

const COLUMNS = [
  { name: "loss_amount", dtype: "Float64" },
  { name: "date_col", dtype: "Date" },
  { name: "group_id", dtype: "Utf8" },
  { name: "is_active", dtype: "Boolean" },
  { name: "age", dtype: "Int64" },
  { name: "exposure", dtype: "Float64" },
]

function makeProps(overrides: Partial<SplitAndMetricsConfigProps> = {}): SplitAndMetricsConfigProps {
  return {
    config: {},
    onUpdate: vi.fn(),
    columns: COLUMNS,
    target: "loss_amount",
    weight: "",
    exclude: [],
    split: { strategy: "random", validation_size: 0.2, holdout_size: 0, seed: 42 },
    mlflowOpen: false,
    monotonicOpen: false,
    toggleSection: vi.fn(),
    onSplitUpdate: vi.fn(),
    ...overrides,
  }
}

describe("SplitAndMetricsConfig", () => {
  it("renders split strategy buttons", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    expect(screen.getByText("random")).toBeInTheDocument()
    expect(screen.getByText("temporal")).toBeInTheDocument()
    expect(screen.getByText("group")).toBeInTheDocument()
  })

  it("highlights the active split strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    const randomBtn = screen.getByText("random")
    expect(randomBtn.style.color).toContain("var(--accent)")
  })

  it("clicking a split strategy calls onSplitUpdate", () => {
    const onSplitUpdate = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onSplitUpdate })} />)
    fireEvent.click(screen.getByText("temporal"))
    expect(onSplitUpdate).toHaveBeenCalledWith("strategy", "temporal")
  })

  it("shows validation/holdout/seed inputs for random strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    expect(screen.getByText("Validation")).toBeInTheDocument()
    expect(screen.getByText("Holdout")).toBeInTheDocument()
    expect(screen.getByText("Seed")).toBeInTheDocument()
  })

  it("shows date column and cutoff for temporal strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps({
      split: { strategy: "temporal", date_column: "", cutoff_date: "" },
    })} />)
    expect(screen.getByText("Date column")).toBeInTheDocument()
    expect(screen.getByText("Cutoff date")).toBeInTheDocument()
  })

  it("shows group column for group strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps({
      split: { strategy: "group", group_column: "", validation_size: 0.2, holdout_size: 0 },
    })} />)
    expect(screen.getByText("Group column")).toBeInTheDocument()
  })

  it("changing validation size calls onSplitUpdate", () => {
    const onSplitUpdate = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onSplitUpdate })} />)
    const inputs = screen.getAllByRole("spinbutton")
    // First spinbutton is validation size
    fireEvent.change(inputs[0], { target: { value: "0.3" } })
    expect(onSplitUpdate).toHaveBeenCalledWith("validation_size", 0.3)
  })

  it("MLflow section toggles on click", () => {
    const toggleSection = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ toggleSection })} />)
    fireEvent.click(screen.getByText("MLflow Logging"))
    expect(toggleSection).toHaveBeenCalledWith("modelling.mlflow")
  })

  it("shows MLflow fields when mlflowOpen is true", () => {
    render(<SplitAndMetricsConfig {...makeProps({ mlflowOpen: true })} />)
    expect(screen.getByText("Experiment path")).toBeInTheDocument()
    expect(screen.getByText(/Model name/)).toBeInTheDocument()
  })

  it("monotonic constraints section toggles on click", () => {
    const toggleSection = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ toggleSection })} />)
    fireEvent.click(screen.getByText("Monotonic Constraints"))
    expect(toggleSection).toHaveBeenCalledWith("modelling.monotonic")
  })

  it("shows monotonic constraint rows only for numeric features", () => {
    render(<SplitAndMetricsConfig {...makeProps({ monotonicOpen: true })} />)
    // Int64 and Float64 are eligible; target, Date, Boolean, and String are not.
    expect(screen.getByText("age")).toBeInTheDocument()
    expect(screen.getByText("exposure")).toBeInTheDocument()
    expect(screen.queryByText("group_id")).not.toBeInTheDocument()
    expect(screen.queryByText("date_col")).not.toBeInTheDocument()
    expect(screen.queryByText("is_active")).not.toBeInTheDocument()
  })

  it("writes +1 constraints and removes a key when reset to zero", () => {
    const onUpdate = vi.fn()
    const { rerender } = render(<SplitAndMetricsConfig {...makeProps({ monotonicOpen: true, onUpdate })} />)

    fireEvent.click(within(screen.getByText("age").parentElement!).getByText("+1"))
    expect(onUpdate).toHaveBeenCalledWith("monotone_constraints", { age: 1 })

    rerender(<SplitAndMetricsConfig {...makeProps({
      monotonicOpen: true,
      onUpdate,
      config: { monotone_constraints: { age: 1 } },
    })} />)
    fireEvent.click(within(screen.getByText("age").parentElement!).getByText("0"))
    expect(onUpdate).toHaveBeenLastCalledWith("monotone_constraints", null)
  })

  it("row limit input calls onUpdate", () => {
    const onUpdate = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onUpdate })} />)
    const rowLimitInput = screen.getByPlaceholderText("All rows")
    fireEvent.change(rowLimitInput, { target: { value: "50000" } })
    expect(onUpdate).toHaveBeenCalledWith("row_limit", 50000)
  })
})
