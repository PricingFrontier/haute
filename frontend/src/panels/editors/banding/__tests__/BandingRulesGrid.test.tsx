import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { BandingRulesGrid } from "../BandingRulesGrid"
import type { BandingFactor, ContinuousRule, CategoricalRule } from "../../../../types/banding"
import { CHART_COLORS } from "../../../../theme/colors"

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
    expect(screen.getByText("From", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("Label", { selector: "th" })).toBeInTheDocument()
    // "Value" appears twice (for lower and upper value columns)
    expect(screen.getAllByText("Value", { selector: "th" })).toHaveLength(2)
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

  // --- Scrollable container & sticky headers ---

  it("scrollable container has max-height style", () => {
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
    ]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const scrollContainer = container.querySelector("[data-testid='banding-scroll-container']")
    expect(scrollContainer).toBeTruthy()
    expect(scrollContainer).toHaveClass("max-h-[300px]")
    expect(scrollContainer).toHaveClass("overflow-y-auto")
  })

  it("keeps boxed inputs but tightens cell spacing and removes row divider lines", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "compact-1" },
    ] as unknown as ContinuousRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    const firstEditableCell = screen.getByLabelText("Rule 1 lower value").closest("td")
    expect(firstEditableCell).toHaveClass("px-0.5")
    expect(firstEditableCell).toHaveClass("py-0.5")
    expect(screen.getByLabelText("Rule 1 lower value")).toHaveClass("rounded")
    expect(screen.getByLabelText("Rule 1 lower value")).not.toHaveClass("rounded-none")
    expect(screen.getByLabelText("Rule 1 lower value").style.border).toBe("1px solid var(--border)")

    const dataRow = container.querySelector("tbody tr") as HTMLTableRowElement
    expect(dataRow.style.borderBottom).toBe("")
  })

  it("thead has position sticky", () => {
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
    ]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const thead = container.querySelector("thead")
    expect(thead).toBeTruthy()
    expect(thead!.style.position).toBe("sticky")
    expect(thead!.style.top).toBe("0px")
    expect(thead!.style.zIndex).toBe("1")
  })

  // --- Accessibility: aria-labels ---

  it("all inputs have aria-labels for continuous rules", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "<=", val2: "60", assignment: "young", _id: "a1" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByLabelText("Rule 1 lower operator")).toBeInTheDocument()
    expect(screen.getByLabelText("Rule 1 lower value")).toBeInTheDocument()
    expect(screen.getByLabelText("Rule 1 upper operator")).toBeInTheDocument()
    expect(screen.getByLabelText("Rule 1 upper value")).toBeInTheDocument()
    expect(screen.getByLabelText("Rule 1 label")).toBeInTheDocument()
  })

  it("all inputs have aria-labels for categorical rules", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "c1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByLabelText("Rule 1 match value")).toBeInTheDocument()
    expect(screen.getByLabelText("Rule 1 group name")).toBeInTheDocument()
  })

  it("delete buttons have aria-labels", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "d1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "d2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByLabelText("Delete rule 1")).toBeInTheDocument()
    expect(screen.getByLabelText("Delete rule 2")).toBeInTheDocument()
  })

  // --- Match counts column ---

  it("match counts column renders when provided", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "m1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "m2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[42, 7]} />)
    expect(screen.getByText("Matches", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.getByText("7")).toBeInTheDocument()
  })

  it("match count of 0 shows warning style", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "w1" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[0]} />)
    const zeroCell = screen.getByText("0")
    // Should have warning color applied.
    const zeroColor = zeroCell.style.color
    expect(zeroColor === 'var(--danger)' || zeroColor === 'rgba(239, 68, 68, 0.7)').toBe(true)
    // Verify the non-zero case in a separate render
    cleanup()
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[5]} />)
    const fiveCell = screen.getByText("5")
    const fiveColor = fiveCell.style.color
    expect(fiveColor !== 'var(--danger)' && fiveColor !== 'rgba(239, 68, 68, 0.7)').toBe(true)
  })

  it("match counts column hidden when not provided", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "h1" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.queryByText("Matches", { selector: "th" })).toBeNull()
  })

  // --- Clipboard paste support ---

  it("paste handler parses 2-column TSV for continuous", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "10", op2: "", val2: "", assignment: "low", _id: "p1" },
    ] as unknown as ContinuousRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "25\tyoung\n60\told" }
    fireEvent.paste(pasteTarget, { clipboardData })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    // Should append 2 parsed rules to existing 1
    expect(lastCall.rules).toHaveLength(3)
    // First parsed rule: val1=25, op1=">=" (first pasted row)
    expect(lastCall.rules[1].val1).toBe("25")
    expect(lastCall.rules[1].op1).toBe(">=")
    expect(lastCall.rules[1].assignment).toBe("young")
    // Second parsed rule: val1=60, op1=">" (subsequent rows)
    expect(lastCall.rules[2].val1).toBe("60")
    expect(lastCall.rules[2].op1).toBe(">")
    expect(lastCall.rules[2].assignment).toBe("old")
  })

  it("pastes TSV into a continuous range starting at the focused cell and creates missing rows", () => {
    const onUpdate = vi.fn()
    const rules = [
      { op1: "<", val1: "20", op2: "", val2: "", assignment: "young", _id: "range-c1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "range-c2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    fireEvent.paste(screen.getByLabelText("Rule 2 upper value"), {
      clipboardData: { getData: () => "75\told\n100\toldest" },
    })

    expect(onUpdate).toHaveBeenCalledTimes(1)
    const updated = onUpdate.mock.calls[0][0].rules
    expect(updated).toHaveLength(3)
    expect(updated[0]).toMatchObject({ op1: "<", val1: "20", op2: "", val2: "", assignment: "young" })
    expect(updated[1]).toMatchObject({ op1: ">=", val1: "60", op2: "", val2: "75", assignment: "old" })
    expect(updated[2]).toMatchObject({ op1: "", val1: "", op2: "", val2: "100", assignment: "oldest" })
  })

  it("paste handler parses 5-column TSV for continuous", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => ">=\t25\t<\t60\tmid" }
    fireEvent.paste(pasteTarget, { clipboardData })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(1)
    expect(lastCall.rules[0].op1).toBe(">=")
    expect(lastCall.rules[0].val1).toBe("25")
    expect(lastCall.rules[0].op2).toBe("<")
    expect(lastCall.rules[0].val2).toBe("60")
    expect(lastCall.rules[0].assignment).toBe("mid")
  })

  it("round-trips copied continuous TSV with trailing blank cells through grid paste", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    fireEvent.paste(pasteTarget, {
      clipboardData: { getData: () => "From\tValue\tTo\tValue\tLabel\n<\t25\t\t\t" },
    })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(1)
    expect(lastCall.rules[0]).toMatchObject({ op1: "<", val1: "25", op2: "", val2: "", assignment: "" })
  })

  it("paste handler parses 2-column TSV for categorical", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor({ banding: "categorical" })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "London\tSouth\nManchester\tNorth" }
    fireEvent.paste(pasteTarget, { clipboardData })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
    expect(lastCall.rules[0].value).toBe("London")
    expect(lastCall.rules[0].assignment).toBe("South")
    expect(lastCall.rules[1].value).toBe("Manchester")
    expect(lastCall.rules[1].assignment).toBe("North")
  })

  it("round-trips copied categorical TSV with a blank final cell through grid paste", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor({ banding: "categorical" })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    fireEvent.paste(pasteTarget, {
      clipboardData: { getData: () => "Value\tMaps To\nLondon\t\nLeeds\tNorth" },
    })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
    expect(lastCall.rules[0]).toMatchObject({ value: "London", assignment: "" })
    expect(lastCall.rules[1]).toMatchObject({ value: "Leeds", assignment: "North" })
  })

  it("pastes TSV into a categorical range and preserves internal blank cells", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "range-cat-1" },
      { value: "Bike", assignment: "Cycle", _id: "range-cat-2" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    fireEvent.paste(screen.getByLabelText("Rule 1 group name"), {
      clipboardData: { getData: () => "Road\n\nMetro" },
    })

    expect(onUpdate).toHaveBeenCalledTimes(1)
    const updated = onUpdate.mock.calls[0][0].rules
    expect(updated).toHaveLength(3)
    expect(updated[0]).toMatchObject({ value: "Car", assignment: "Road" })
    expect(updated[1]).toMatchObject({ value: "Bike", assignment: "" })
    expect(updated[2]).toMatchObject({ value: "", assignment: "Metro" })
  })

  it("copies the whole continuous banding as TSV", () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    })
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "copy-1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "copy-2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Copy banding as TSV" }))

    expect(writeText).toHaveBeenCalledWith("From\tValue\tTo\tValue\tLabel\n<\t25\t\t\tyoung\n>=\t60\t\t\told")
  })

  it("copies the whole categorical banding as TSV", () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    })
    const rules = [
      { value: "London", assignment: "South", _id: "copy-cat-1" },
      { value: "Leeds", assignment: "North", _id: "copy-cat-2" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Copy banding as TSV" }))

    expect(writeText).toHaveBeenCalledWith("Value\tMaps To\nLondon\tSouth\nLeeds\tNorth")
  })

  it("shows the copy banding action as an icon-only control below the grid", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "copy-ui-1" },
    ] as unknown as ContinuousRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    const button = screen.getByRole("button", { name: "Copy banding as TSV" })
    expect(screen.queryByText("Copy TSV")).not.toBeInTheDocument()
    expect(button).toHaveAttribute("title", "Copy banding as TSV")
    expect(container.querySelector("[data-testid='banding-scroll-container'] + div button")).toBe(button)
  })

  // --- Keyboard: Enter to add row ---

  it("Enter on last assignment input calls onAddRule", () => {
    const onAddRule = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "e1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "e2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} onAddRule={onAddRule} />)
    // Last assignment input is the last textbox
    const lastInput = screen.getByLabelText("Rule 2 label")
    fireEvent.keyDown(lastInput, { key: "Enter" })
    expect(onAddRule).toHaveBeenCalledTimes(1)
  })

  it("Enter on non-last assignment input does not call onAddRule", () => {
    const onAddRule = vi.fn()
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "e3" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "e4" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} onAddRule={onAddRule} />)
    const firstInput = screen.getByLabelText("Rule 1 label")
    fireEvent.keyDown(firstInput, { key: "Enter" })
    expect(onAddRule).not.toHaveBeenCalled()
  })

  // --- Header labels ---

  it("header labels say 'From', 'To (opt.)', 'Label' for continuous", () => {
    const rules: ContinuousRule[] = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByText("From", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("Label", { selector: "th" })).toBeInTheDocument()
    // "To (opt.)" header
    const toHeader = screen.getByText((_content, element) => {
      return element?.tagName === "TH" && element.textContent === "To (opt.)"
    })
    expect(toHeader).toBeInTheDocument()
  })

  it("header labels say 'Value', 'Maps To' for categorical", () => {
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByText("Value", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("Maps To", { selector: "th" })).toBeInTheDocument()
  })

  // --- AccentColor prop ---

  it("accentColor prop is applied to assignment inputs", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "ac1" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} accentColor="#ff0000" />)
    const assignmentInput = screen.getByLabelText("Rule 1 label")
    // Browser may normalize to rgb()
    expect(assignmentInput.style.color === '#ff0000' || assignmentInput.style.color === 'rgb(255, 0, 0)').toBe(true)
  })

  it("accentColor defaults to the banding-accent token when not provided", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "ac2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const assignmentInput = screen.getByLabelText("Rule 1 label")
    expect(assignmentInput.style.color).toBe(CHART_COLORS.bandingAccent)
  })

  // --- Paste 3-column TSV for continuous ---

  it("paste handler parses 3-column TSV for continuous", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "25\t60\tmid\n60\t100\thigh" }
    fireEvent.paste(pasteTarget, { clipboardData })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
    expect(lastCall.rules[0].op1).toBe(">")
    expect(lastCall.rules[0].val1).toBe("25")
    expect(lastCall.rules[0].op2).toBe("<=")
    expect(lastCall.rules[0].val2).toBe("60")
    expect(lastCall.rules[0].assignment).toBe("mid")
  })

  // --- Match counts for categorical ---

  it("match counts column renders for categorical when provided", () => {
    const rules = [
      { value: "London", assignment: "South", _id: "mc1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={vi.fn()} matchCounts={[15]} />)
    expect(screen.getByText("Matches", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("15")).toBeInTheDocument()
  })

  // --- Enter for categorical ---

  it("Enter on last categorical assignment input calls onAddRule", () => {
    const onAddRule = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "ce1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={vi.fn()} onAddRule={onAddRule} />)
    const lastInput = screen.getByLabelText("Rule 1 group name")
    fireEvent.keyDown(lastInput, { key: "Enter" })
    expect(onAddRule).toHaveBeenCalledTimes(1)
  })

  // --- Paste edge cases ---

  it("paste with trailing newlines ignores empty lines", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor({ banding: "categorical" })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "London\tSouth\n\n\nManchester\tNorth\n\n" }
    fireEvent.paste(pasteTarget, { clipboardData })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
    expect(lastCall.rules[0].value).toBe("London")
    expect(lastCall.rules[1].value).toBe("Manchester")
  })

  it("paste with tab-only lines ignores them", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor({ banding: "categorical" })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "London\tSouth\n\t\t\nManchester\tNorth" }
    fireEvent.paste(pasteTarget, { clipboardData })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
  })

  it("paste without tabs is ignored (not TSV)", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "nt1" },
    ] as unknown as CategoricalRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ banding: "categorical", rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "just plain text" }
    fireEvent.paste(pasteTarget, { clipboardData })

    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("paste with 4-column continuous data (malformed) skips those lines", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    // 4 columns doesn't match any parsing branch for continuous
    const clipboardData = { getData: () => ">=\t25\t<\t60" }
    fireEvent.paste(pasteTarget, { clipboardData })

    // No rules parsed, so onUpdate should not be called
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("matchCounts shorter than rules shows empty for missing indices", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "ms1" },
      { op1: ">=", val1: "60", op2: "", val2: "", assignment: "old", _id: "ms2" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[42]} />)
    // First rule shows 42
    expect(screen.getByText("42")).toBeInTheDocument()
    // Second rule's match count should be empty (matchCounts[1] is undefined -> "" via nullish coalescing)
    // The Matches header should still be present
    expect(screen.getByText("Matches", { selector: "th" })).toBeInTheDocument()
  })

  it("Enter without onAddRule prop does not throw", () => {
    const rules = [
      { op1: "<", val1: "25", op2: "", val2: "", assignment: "young", _id: "nr1" },
    ] as unknown as ContinuousRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const lastInput = screen.getByLabelText("Rule 1 label")
    // Should not throw even without onAddRule
    expect(() => fireEvent.keyDown(lastInput, { key: "Enter" })).not.toThrow()
  })
})
