import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { BandingRulesGrid } from "../BandingRulesGrid"
import type { BandingFactor, CategoricalRule } from "../../../../types/banding"
import { CHART_COLORS } from "../../../../theme/colors"
import useToastStore from "../../../../stores/useToastStore"

const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard")

function restoreClipboard(): void {
  if (originalClipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", originalClipboardDescriptor)
    return
  }
  Reflect.deleteProperty(navigator, "clipboard")
}

function makeFactor(overrides: Partial<BandingFactor> = {}): BandingFactor {
  return {
    banding: "categorical",
    column: "vehicle_type",
    outputColumn: "vehicle_group",
    rules: [],
    default: null,
    ...overrides,
  }
}

describe("BandingRulesGrid", () => {
  afterEach(() => {
    cleanup()
    restoreClipboard()
  })

  it("renders empty state for categorical banding with no rules", () => {
    render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={vi.fn()} />)
    expect(screen.getByText("No rules yet")).toBeInTheDocument()
  })

  it("renders categorical rule rows", () => {
    const rules: CategoricalRule[] = [
      { value: "Semi-detached", assignment: "House" },
      { value: "Terraced", assignment: "House" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByDisplayValue("Semi-detached")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Terraced")).toBeInTheDocument()
    // Both map to "House" assignment
    expect(screen.getAllByDisplayValue("House")).toHaveLength(2)
  })

  it("delete button removes a rule", () => {
    const onUpdate = vi.fn()
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
      { value: "Bike", assignment: "Cycle" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    fireEvent.click(screen.getByLabelText("Delete rule 1"))
    // Should have removed the first rule, keeping the second (with _id)
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(1)
    expect(lastCall.rules[0]).toMatchObject({ value: "Bike", assignment: "Cycle" })
  })

  it("updating a categorical rule field calls onUpdateFactor", () => {
    const onUpdate = vi.fn()
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    const inputs = screen.getAllByRole("textbox")
    fireEvent.change(inputs[0], { target: { value: "Truck" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0].value).toBe("Truck")
    expect(lastCall.rules[0].assignment).toBe("Vehicle")
  })

  it("updating the group name calls onUpdateFactor with new value", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "r5" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    fireEvent.change(screen.getByLabelText("Rule 1 group name"), { target: { value: "Motor" } })
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules[0]).toMatchObject({ value: "Car", assignment: "Motor" })
  })

  it("keeps generated row keys local until a user edit", () => {
    const onUpdate = vi.fn()
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    const factor = makeFactor({ rules })
    const { rerender } = render(<BandingRulesGrid factor={factor} onUpdateFactor={onUpdate} />)
    const textbox = screen.getByLabelText("Rule 1 match value")
    textbox.focus()

    expect(onUpdate).not.toHaveBeenCalled()
    rerender(<BandingRulesGrid factor={factor} onUpdateFactor={onUpdate} />)
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByLabelText("Rule 1 match value")).toBe(textbox)
    expect(document.activeElement).toBe(textbox)

    fireEvent.change(textbox, { target: { value: "Van" } })
    expect(onUpdate).toHaveBeenCalledOnce()
    const assignedRules = onUpdate.mock.calls[0][0].rules
    expect(assignedRules[0]._id).toBeDefined()
    expect(typeof assignedRules[0]._id).toBe("string")
    expect(assignedRules[0]._id.length).toBeGreaterThan(0)
  })

  it("keeps row identity when the parent recreates an id-less rules array", () => {
    const onUpdate = vi.fn()
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
      { value: "Bike", assignment: "Cycle" },
    ]
    const factor = makeFactor({ rules })
    const { rerender } = render(<BandingRulesGrid factor={factor} onUpdateFactor={onUpdate} />)
    const textbox = screen.getByLabelText("Rule 1 match value")
    textbox.focus()

    rerender(<BandingRulesGrid factor={{ ...factor, rules: [...rules] }} onUpdateFactor={onUpdate} />)

    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByLabelText("Rule 1 match value")).toBe(textbox)
    expect(document.activeElement).toBe(textbox)
  })

  it("rules with existing _id are not reassigned", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "existing_id" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText("Rule 1 match value"), { target: { value: "Van" } })
    expect(onUpdate.mock.calls[0][0].rules[0]._id).toBe("existing_id")
  })

  it("each rule gets a unique _id", () => {
    const onUpdate = vi.fn()
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
      { value: "Bike", assignment: "Cycle" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    fireEvent.change(screen.getByLabelText("Rule 1 match value"), { target: { value: "Van" } })
    expect(onUpdate).toHaveBeenCalledOnce()
    const assignedRules = onUpdate.mock.calls[0][0].rules
    expect(assignedRules[0]._id).not.toBe(assignedRules[1]._id)
  })

  it("delete button removes the correct rule from the middle", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "a" },
      { value: "Bike", assignment: "Cycle", _id: "b" },
      { value: "Bus", assignment: "Coach", _id: "c" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    fireEvent.click(screen.getByLabelText("Delete rule 2"))
    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(2)
    expect(lastCall.rules[0].assignment).toBe("Vehicle")
    expect(lastCall.rules[1].assignment).toBe("Coach")
  })

  it("rules with _id are preserved across re-render", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "stable1" },
      { value: "Bike", assignment: "Cycle", _id: "stable2" },
    ] as unknown as CategoricalRule[]
    const { rerender } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    rerender(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    fireEvent.change(screen.getByLabelText("Rule 2 group name"), { target: { value: "Pedal" } })
    const edited = onUpdate.mock.calls[0][0].rules
    expect(edited[0]._id).toBe("stable1")
    expect(edited[1]._id).toBe("stable2")
  })

  // --- Scrollable container & sticky headers ---

  it("scrollable container has max-height style", () => {
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const scrollContainer = container.querySelector("[data-testid='banding-scroll-container']")
    expect(scrollContainer).toBeTruthy()
    expect(scrollContainer).toHaveClass("max-h-[300px]")
    expect(scrollContainer).toHaveClass("overflow-y-auto")
  })

  it("keeps boxed inputs but tightens cell spacing and removes row divider lines", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "compact-1" },
    ] as unknown as CategoricalRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    const firstEditableCell = screen.getByLabelText("Rule 1 match value").closest("td")
    expect(firstEditableCell).toHaveClass("px-0.5")
    expect(firstEditableCell).toHaveClass("py-0.5")
    expect(screen.getByLabelText("Rule 1 match value")).toHaveClass("rounded")
    expect(screen.getByLabelText("Rule 1 match value")).not.toHaveClass("rounded-none")
    expect(screen.getByLabelText("Rule 1 match value").style.border).toBe("1px solid var(--border)")

    const dataRow = container.querySelector("tbody tr") as HTMLTableRowElement
    expect(dataRow.style.borderBottom).toBe("")
  })

  it("thead has position sticky", () => {
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const thead = container.querySelector("thead")
    expect(thead).toBeTruthy()
    expect(thead!.style.position).toBe("sticky")
    expect(thead!.style.top).toBe("0px")
    expect(thead!.style.zIndex).toBe("1")
  })

  // --- Accessibility: aria-labels ---

  it("all inputs have aria-labels for categorical rules", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "c1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByLabelText("Rule 1 match value")).toBeInTheDocument()
    expect(screen.getByLabelText("Rule 1 group name")).toBeInTheDocument()
  })

  it("delete buttons have aria-labels", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "d1" },
      { value: "Bike", assignment: "Cycle", _id: "d2" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByLabelText("Delete rule 1")).toBeInTheDocument()
    expect(screen.getByLabelText("Delete rule 2")).toBeInTheDocument()
  })

  // --- Match counts column ---

  it("match counts column renders when provided", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "m1" },
      { value: "Bike", assignment: "Cycle", _id: "m2" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[42, 7]} />)
    expect(screen.getByText("Matches", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.getByText("7")).toBeInTheDocument()
  })

  it("match count of 0 shows warning style", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "w1" },
    ] as unknown as CategoricalRule[]
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
      { value: "Car", assignment: "Vehicle", _id: "h1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.queryByText("Matches", { selector: "th" })).toBeNull()
  })

  // --- Clipboard paste support ---

  it("appends pasted rows after the existing rules", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "p1" },
    ] as unknown as CategoricalRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    fireEvent.paste(pasteTarget, { clipboardData: { getData: () => "Bike\tCycle\nBus\tCoach" } })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules).toHaveLength(3)
    expect(lastCall.rules[0]).toMatchObject({ value: "Car", assignment: "Vehicle" })
    expect(lastCall.rules[1]).toMatchObject({ value: "Bike", assignment: "Cycle" })
    expect(lastCall.rules[2]).toMatchObject({ value: "Bus", assignment: "Coach" })
  })

  it("pastes TSV into a range starting at the focused cell and creates missing rows", () => {
    const onUpdate = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "range-1" },
      { value: "Bike", assignment: "Cycle", _id: "range-2" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)

    fireEvent.paste(screen.getByLabelText("Rule 2 match value"), {
      clipboardData: { getData: () => "Bus\tCoach\nTram\tRail" },
    })

    expect(onUpdate).toHaveBeenCalledTimes(1)
    const updated = onUpdate.mock.calls[0][0].rules
    expect(updated).toHaveLength(3)
    expect(updated[0]).toMatchObject({ value: "Car", assignment: "Vehicle" })
    expect(updated[1]).toMatchObject({ value: "Bus", assignment: "Coach" })
    expect(updated[2]).toMatchObject({ value: "Tram", assignment: "Rail" })
  })

  it("paste handler parses 2-column TSV for categorical", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
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
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
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
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
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
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Copy banding as TSV" }))

    expect(writeText).toHaveBeenCalledWith("Value\tMaps To\nLondon\tSouth\nLeeds\tNorth")
  })

  it("shows a toast when the clipboard API is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    })
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "copy-missing" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Copy banding as TSV" }))

    await waitFor(() => {
      expect(useToastStore.getState().toasts).toContainEqual(
        expect.objectContaining({
          type: "error",
          text: "Could not copy banding TSV: Clipboard API is not available",
        }),
      )
    })
  })

  it("shows the copy banding action as an icon-only control below the grid", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "copy-ui-1" },
    ] as unknown as CategoricalRule[]
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)

    const button = screen.getByRole("button", { name: "Copy banding as TSV" })
    expect(screen.queryByText("Copy TSV")).not.toBeInTheDocument()
    expect(button).toHaveAttribute("title", "Copy banding as TSV")
    expect(container.querySelector("[data-testid='banding-scroll-container'] + div button")).toBe(button)
  })

  // --- Keyboard: Enter to add row ---

  it("Enter on non-last assignment input does not call onAddRule", () => {
    const onAddRule = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "e3" },
      { value: "Bike", assignment: "Cycle", _id: "e4" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} onAddRule={onAddRule} />)
    const firstInput = screen.getByLabelText("Rule 1 group name")
    fireEvent.keyDown(firstInput, { key: "Enter" })
    expect(onAddRule).not.toHaveBeenCalled()
  })

  // --- Header labels ---

  it("header labels say 'Value', 'Maps To' for categorical", () => {
    const rules: CategoricalRule[] = [
      { value: "Car", assignment: "Vehicle" },
    ]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    expect(screen.getByText("Value", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("Maps To", { selector: "th" })).toBeInTheDocument()
  })

  // --- AccentColor prop ---

  it("accentColor prop is applied to assignment inputs", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "ac1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} accentColor="#ff0000" />)
    const assignmentInput = screen.getByLabelText("Rule 1 group name")
    // Browser may normalize to rgb()
    expect(assignmentInput.style.color === '#ff0000' || assignmentInput.style.color === 'rgb(255, 0, 0)').toBe(true)
  })

  it("accentColor defaults to the banding-accent token when not provided", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "ac2" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const assignmentInput = screen.getByLabelText("Rule 1 group name")
    expect(assignmentInput.style.color).toBe(CHART_COLORS.bandingAccent)
  })

  // --- Match counts for categorical ---

  it("match counts column renders for categorical when provided", () => {
    const rules = [
      { value: "London", assignment: "South", _id: "mc1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[15]} />)
    expect(screen.getByText("Matches", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("15")).toBeInTheDocument()
  })

  // --- Enter for categorical ---

  it("Enter on last categorical assignment input calls onAddRule", () => {
    const onAddRule = vi.fn()
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "ce1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} onAddRule={onAddRule} />)
    const lastInput = screen.getByLabelText("Rule 1 group name")
    fireEvent.keyDown(lastInput, { key: "Enter" })
    expect(onAddRule).toHaveBeenCalledTimes(1)
  })

  // --- Paste edge cases ---

  it("paste with trailing newlines ignores empty lines", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
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
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)
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
    const { container } = render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={onUpdate} />)
    onUpdate.mockClear()

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    const clipboardData = { getData: () => "just plain text" }
    fireEvent.paste(pasteTarget, { clipboardData })

    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("skips a pasted row that has a value but no label column", () => {
    const onUpdate = vi.fn()
    const { container } = render(<BandingRulesGrid factor={makeFactor()} onUpdateFactor={onUpdate} />)

    const pasteTarget = container.querySelector("[data-testid='banding-scroll-container']")!
    fireEvent.paste(pasteTarget, { clipboardData: { getData: () => "London\tSouth\nLeeds\nManchester\tNorth" } })

    const lastCall = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0]
    expect(lastCall.rules.map((rule: CategoricalRule) => rule.value)).toEqual(["London", "Manchester"])
  })

  it("matchCounts shorter than rules shows empty for missing indices", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "ms1" },
      { value: "Bike", assignment: "Cycle", _id: "ms2" },
    ] as unknown as CategoricalRule[]
    const { container } = render(
      <BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} matchCounts={[42]} />,
    )
    const countCells = Array.from(container.querySelectorAll("tbody tr")).map(
      (row) => row.querySelectorAll("td")[2]?.textContent,
    )
    expect(countCells).toEqual(["42", ""])
    expect(screen.getByText("Matches", { selector: "th" })).toBeInTheDocument()
  })

  it("Enter without onAddRule prop does not throw", () => {
    const rules = [
      { value: "Car", assignment: "Vehicle", _id: "nr1" },
    ] as unknown as CategoricalRule[]
    render(<BandingRulesGrid factor={makeFactor({ rules })} onUpdateFactor={vi.fn()} />)
    const lastInput = screen.getByLabelText("Rule 1 group name")
    // Should not throw even without onAddRule
    expect(() => fireEvent.keyDown(lastInput, { key: "Enter" })).not.toThrow()
  })
})
