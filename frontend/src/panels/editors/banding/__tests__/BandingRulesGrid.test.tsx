import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { BandingRulesGrid } from "../BandingRulesGrid"
import type { BandingFactor, ContinuousRule, CategoricalRule } from "../../../../types/banding"

function makeFactor(overrides: Partial<BandingFactor> = {}): BandingFactor {
  return {
    banding: "continuous",
    column: "age",
    outputColumn: "age_band",
    rules: [],
    default: null,
    ...overrides,
  }
}

describe("BandingRulesGrid", () => {
  afterEach(cleanup)

  it("renders empty state for continuous banding with no rules", () => {
    render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={vi.fn()} />)
    expect(screen.getByText("No rules yet")).toBeInTheDocument()
  })

  it("renders empty state for categorical banding with no rules", () => {
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical" })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByText("No rules yet")).toBeInTheDocument()
  })

  it("renders continuous rule rows with correct headers", () => {
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
      { op1: ">=", val1: "25", op2: "<", val2: "60", assignment: "mid" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    // Two "Op" columns (primary and secondary)
    expect(screen.getAllByText("Op", { selector: "th" })).toHaveLength(2)
    expect(screen.getByText("Band", { selector: "th" })).toBeInTheDocument()
  })

  it("renders categorical rule rows", () => {
    const rules: CategoricalRule[] = [
      { value: "Semi-detached", assignment: "House" },
      { value: "Terraced", assignment: "House" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByDisplayValue("Semi-detached")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Terraced")).toBeInTheDocument()
    // Both map to "House" assignment
    expect(screen.getAllByDisplayValue("House")).toHaveLength(2)
  })

  it("delete button removes a continuous rule", () => {
    const onUpdate = vi.fn()
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    // First call is the id-assignment call (ensureRuleIds); clear it
    onUpdate.mockClear()
    // Click the first delete button
    const deleteButtons = screen.getAllByRole("button")
    fireEvent.click(deleteButtons[0])
    // Should have removed the first rule, keeping the second (with _id)
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(1)
    expect(lastCall.rules[0].assignment).toBe("old")
  })

  it("updating a categorical rule field calls onUpdateFactor", () => {
    const onUpdate = vi.fn()
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={onUpdate} />)
    // First call is the id-assignment call, clear it
    onUpdate.mockClear()
    const inputs = screen.getAllByRole("textbox")
    fireEvent.change(inputs[0], { target: { value: "Truck" } })
    // The update call should include the _id field from ensureRuleIds
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].value).toBe("Truck")
    expect(lastCall.rules[0].assignment).toBe("Vehicle")
  })

  it("assigns stable _id keys to rules without them", () => {
    const onUpdate = vi.fn()
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    // Should have been called with rules that now have _id
    expect(onUpdate).toHaveBeenCalled()
    const assignedRules = onUpdate.mock.calls[0][0].rules
    expect(assignedRules[0]._id).toBeDefined()
    expect(typeof assignedRules[0]._id).toBe("string")
    expect(assignedRules[0]._id.length).toBeGreaterThan(0)
  })

  it("rules with existing _id are not reassigned", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "existing_id" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    // onUpdateFactor should NOT be called for id assignment since _id already exists
    // Either not called at all, or called with the same _id preserved
    if (onUpdate.mock.calls.length > 0 && onUpdate.mock.calls[0][0].rules) {
      expect(onUpdate.mock.calls[0][0].rules[0]._id).toBe("existing_id")
    }
  })

  it("each rule gets a unique _id", () => {
    const onUpdate = vi.fn()
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
      { op1: ">=", val1: "25", op2: "", val2: "", assignment: "old" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    expect(onUpdate).toHaveBeenCalled()
    const assignedRules = onUpdate.mock.calls[0][0].rules
    expect(assignedRules[0]._id).not.toBe(assignedRules[1]._id)
  })

  it("updating op1 select calls onUpdateFactor with new value", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "r1" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()
    const selects = screen.getAllByRole("combobox")
    fireEvent.change(selects[0], { target: { value: ">=" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].op1).toBe(">=")
    expect(lastCall.rules[0].val1).toBe("25")
    expect(lastCall.rules[0].assignment).toBe("young")
  })

  it("updating val1 input calls onUpdateFactor with new value", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "r2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()
    const inputs = screen.getAllByRole("textbox")
    fireEvent.change(inputs[0], { target: { value: "30" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].val1).toBe("30")
  })

  it("updating op2 select calls onUpdateFactor with new value", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "r3" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()
    const selects = screen.getAllByRole("combobox")
    fireEvent.change(selects[1], { target: { value: "<=" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].op2).toBe("<=")
  })

  it("updating val2 input calls onUpdateFactor with new value", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "<", val2: "60", assignment: "young", _id: "r4" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()
    const inputs = screen.getAllByRole("textbox")
    fireEvent.change(inputs[1], { target: { value: "50" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].val2).toBe("50")
  })

  it("updating assignment input calls onUpdateFactor with new value", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "r5" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()
    const inputs = screen.getAllByRole("textbox")
    const assignmentInput = inputs[inputs.length - 1]
    fireEvent.change(assignmentInput, { target: { value: "youth" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].assignment).toBe("youth")
  })

  it("delete button removes the correct rule from the middle", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "a" },
      { op1: ">=", val1: "25", op2: "<", val2: "60", assignment: "mid", _id: "b" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "c" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()
    const deleteButtons = screen.getAllByRole("button")
    fireEvent.click(deleteButtons[1])
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
    expect(lastCall.rules[0].assignment).toBe("young")
    expect(lastCall.rules[1].assignment).toBe("old")
  })

  it("rules with _id are preserved across re-render", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "stable1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "stable2" },
    ] as unknown as ContinuousRule[]
    const { rerender } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    rerender(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    const calls = onUpdate.mock.calls
    for (const call of calls) {
      if (call[0].rules) {
        expect(call[0].rules[0]._id).toBe("stable1")
        expect(call[0].rules[1]._id).toBe("stable2")
      }
    }
  })
})
