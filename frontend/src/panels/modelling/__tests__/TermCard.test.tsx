import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { nativeTypeOptions, type AdditionalTermType, type EditResult, type TermSpec, type Terms } from "../glmTerms"
import { TermCard } from "../TermCard"

afterEach(cleanup)

function renderNative(spec: TermSpec, { column = "age", dtype = "Int64" }: { column?: string; dtype?: string } = {}) {
  const handlers = { onChangeType: vi.fn(), onChangeField: vi.fn(), onChangeSplineMode: vi.fn(), onRemove: vi.fn() }
  const view = render(
    <TermCard
      kind="native"
      column={column}
      dtype={dtype}
      spec={spec}
      typeOptions={nativeTypeOptions({ [column]: spec }, column, dtype)}
      {...handlers}
    />,
  )
  return { ...handlers, view }
}

function renderSlot(props: { column: string; dtype: string; terms?: Terms; override?: TermSpec; partnerSpecs?: (TermSpec | null)[]; sharedTargetEncoding?: { key: string; spec: TermSpec } }) {
  const handlers = { onChangeFit: vi.fn(), onChangeField: vi.fn(), onChangeSplineMode: vi.fn() }
  render(
    <TermCard
      kind="slot"
      column={props.column}
      dtype={props.dtype}
      terms={props.terms ?? {}}
      override={props.override}
      partnerSpecs={props.partnerSpecs ?? [{ type: "linear" }]}
      sharedTargetEncoding={props.sharedTargetEncoding}
      {...handlers}
    />,
  )
  return handlers
}

const optionValues = (select: HTMLElement) => Array.from(select.querySelectorAll("option")).map((option) => option.value)
const optionLabels = (select: HTMLElement) => Array.from(select.querySelectorAll("option")).map((option) => option.textContent)

describe("TermCard native", () => {
  it("offers the column class's fits and flags a saved fit the class refuses", () => {
    renderNative({ type: "linear" })
    expect(optionValues(screen.getByRole("combobox", { name: "age term type" })))
      .toEqual(["linear", "categorical", "bs", "ns", "ms", "target_encoding", "frequency_encoding"])
    cleanup()
    renderNative({ type: "linear" }, { column: "region", dtype: "String" })
    const select = screen.getByRole("combobox", { name: "region term type" })
    expect(optionLabels(select)).toEqual(["Linear", "Categorical", "Target enc.", "Frequency enc."])
    expect(screen.getByRole("alert")).toHaveTextContent("Linear is not available for a categorical column; choose another fit or remove it.")
  })

  it("shows spline controls, degree only for bs and ms, and hides Advanced until expanded", () => {
    renderNative({ type: "bs", df: 4, degree: 3 })
    expect(screen.getByRole("combobox", { name: "age df mode" })).toHaveValue("fixed")
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(4)
    expect(screen.getByLabelText("Degree")).toHaveAttribute("aria-label", "age degree")
    expect(screen.getByText("Interior knots")).not.toBeVisible()
    const advanced = screen.getByRole("button", { name: "Advanced" })
    fireEvent.click(advanced)
    expect(advanced).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByText("Interior knots")).toBeVisible()
    cleanup()
    renderNative({ type: "ns" })
    expect(screen.getByRole("combobox", { name: "age df mode" })).toHaveValue("auto")
    expect(screen.queryByRole("spinbutton", { name: "age df" })).not.toBeInTheDocument()
    expect(screen.queryByRole("spinbutton", { name: "age degree" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "age monotonicity" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }))
    expect(screen.getByRole("spinbutton", { name: "age k" })).toBeVisible()
    expect(screen.queryByRole("textbox", { name: "age knots" })).not.toBeInTheDocument()
  })

  it("switches spline mode through one transition", () => {
    const { onChangeSplineMode, onChangeField } = renderNative({ type: "bs" })
    fireEvent.change(screen.getByRole("combobox", { name: "age df mode" }), { target: { value: "fixed" } })
    expect(onChangeSplineMode).toHaveBeenCalledWith("fixed")
    expect(onChangeField).not.toHaveBeenCalled()
  })

  it("clearing a spline df field keeps Fixed mode and sibling settings", () => {
    const { onChangeField, onChangeSplineMode } = renderNative({ type: "bs", df: 6, degree: 2 })
    const df = screen.getByRole("spinbutton", { name: "age df" })
    fireEvent.change(df, { target: { value: "" } })
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an integer from 3 to 20")
    fireEvent.blur(df)
    expect(onChangeField).not.toHaveBeenCalled()
    expect(onChangeSplineMode).not.toHaveBeenCalled()
    expect(screen.getByRole("combobox", { name: "age df mode" })).toHaveValue("fixed")
    expect(screen.getByRole("spinbutton", { name: "age degree" })).toHaveValue(2)
  })

  it("commits a numeric edit once, on blur or Enter, and never an out-of-range draft", () => {
    const { onChangeField } = renderNative({ type: "bs", df: 6 })
    const df = screen.getByRole("spinbutton", { name: "age df" })
    fireEvent.change(df, { target: { value: "1" } })
    fireEvent.change(df, { target: { value: "12" } })
    expect(onChangeField).not.toHaveBeenCalled()
    fireEvent.blur(df)
    expect(onChangeField.mock.calls).toEqual([["df", 12]])
    fireEvent.change(df, { target: { value: "21" } })
    fireEvent.keyDown(df, { key: "Enter" })
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an integer from 4 to 20")
    expect(onChangeField.mock.calls).toEqual([["df", 12]])
    fireEvent.change(df, { target: { value: "9" } })
    fireEvent.keyDown(df, { key: "Enter" })
    expect(onChangeField.mock.calls).toEqual([["df", 12], ["df", 9]])
  })

  it("clears an optional numeric field to undefined", () => {
    const { onChangeField } = renderNative({ type: "bs", df: 6, degree: 2 })
    const degree = screen.getByRole("spinbutton", { name: "age degree" })
    fireEvent.change(degree, { target: { value: "" } })
    fireEvent.blur(degree)
    expect(onChangeField).toHaveBeenCalledWith("degree", undefined)
  })

  it("validates advanced JSON arrays and keeps an invalid draft", () => {
    const { onChangeField } = renderNative({ type: "bs", df: 5 })
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }))
    const knots = screen.getByRole("textbox", { name: "age knots" })
    fireEvent.change(knots, { target: { value: "[3, 2]" } })
    fireEvent.blur(knots)
    expect(knots).toHaveValue("[3, 2]")
    expect(screen.getByRole("alert")).toHaveTextContent("strictly increasing")
    fireEvent.change(knots, { target: { value: "[1, 3, 4]" } })
    fireEvent.blur(knots)
    expect(onChangeField).toHaveBeenCalledWith("knots", [1, 3, 4])
    const boundary = screen.getByRole("textbox", { name: "age boundary knots" })
    fireEvent.change(boundary, { target: { value: "[0, 1e999]" } })
    fireEvent.blur(boundary)
    expect(onChangeField).not.toHaveBeenCalledWith("boundary_knots", expect.anything())
    fireEvent.change(boundary, { target: { value: "[0, 10]" } })
    fireEvent.blur(boundary)
    expect(onChangeField).toHaveBeenCalledWith("boundary_knots", [0, 10])
  })

  it("offers a reference level and levels for a categorical fit", () => {
    const { onChangeField } = renderNative({ type: "categorical", levels: ["basic"] }, { column: "brand", dtype: "String" })
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }))
    expect(screen.getByText(/Replaces Levels/)).toBeInTheDocument()
    expect(screen.getByText(/Replaces Reference level/)).toBeInTheDocument()
    const reference = screen.getByRole("textbox", { name: "brand reference level" })
    fireEvent.change(reference, { target: { value: " premium " } })
    fireEvent.blur(reference)
    expect(onChangeField).toHaveBeenCalledWith("reference", "premium")
    const levels = screen.getByRole("textbox", { name: "brand levels" })
    fireEvent.change(levels, { target: { value: "[1]" } })
    fireEvent.blur(levels)
    expect(screen.getByRole("alert")).toHaveTextContent("Levels must be strings")
    fireEvent.change(levels, { target: { value: '["basic", "2"]' } })
    fireEvent.blur(levels)
    expect(onChangeField).toHaveBeenCalledWith("levels", ["basic", "2"])
  })

  it("renders monotonicity arrows for linear and bs, two for ms, and writes the direction", () => {
    const { onChangeField } = renderNative({ type: "linear" })
    expect(within(screen.getByRole("group", { name: "age monotonicity" })).getAllByRole("button")).toHaveLength(3)
    fireEvent.click(screen.getByRole("button", { name: "age: increasing" }))
    expect(onChangeField).toHaveBeenCalledWith("monotonicity", "increasing")
    fireEvent.click(screen.getByRole("button", { name: "age: no constraint" }))
    expect(onChangeField).toHaveBeenCalledWith("monotonicity", undefined)
    cleanup()
    renderNative({ type: "ms", monotonicity: "increasing" })
    expect(within(screen.getByRole("group", { name: "age monotonicity" })).getAllByRole("button")).toHaveLength(2)
  })

  it("edits target encoding prior weight and permutations", () => {
    const { onChangeField, view } = renderNative({ type: "target_encoding" }, { column: "brand", dtype: "String" })
    expect(screen.getByRole("combobox", { name: "brand prior weight mode" })).toHaveValue("auto")
    expect(screen.queryByRole("spinbutton", { name: "brand prior weight" })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "brand prior weight mode" }), { target: { value: "fixed" } })
    expect(onChangeField).toHaveBeenCalledWith("prior_weight", 1)
    view.rerender(
      <TermCard kind="native" column="brand" dtype="String" spec={{ type: "target_encoding", prior_weight: 0 }} typeOptions={[]} onChangeType={vi.fn()} onChangeField={onChangeField} onChangeSplineMode={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("spinbutton", { name: "brand prior weight" })).toHaveValue(0)
    fireEvent.change(screen.getByRole("combobox", { name: "brand prior weight mode" }), { target: { value: "auto" } })
    expect(onChangeField).toHaveBeenCalledWith("prior_weight", undefined)
    fireEvent.click(screen.getByRole("button", { name: "Advanced" }))
    const permutations = screen.getByRole("spinbutton", { name: "brand n permutations" })
    fireEvent.change(permutations, { target: { value: "8" } })
    fireEvent.blur(permutations)
    expect(onChangeField).toHaveBeenCalledWith("n_permutations", 8)
  })

  it("lists parameter problems the backend would refuse and removes the term", () => {
    const { onRemove } = renderNative({ type: "bs", df: 5, k: 8 })
    expect(screen.getByRole("alert")).toHaveTextContent("Set only one of df, k, or knots (found df, k).")
    fireEvent.click(screen.getByRole("button", { name: "Remove age term" }))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })
})

describe("TermCard additional", () => {
  type Edit<T> = (value: T) => EditResult
  const accepted = (): EditResult => ({ ok: true, terms: {} })
  function renderAdditional(overrides: Partial<{ onRename: Edit<string>; onChangeExpr: Edit<string>; onChangeType: Edit<AdditionalTermType>; notice: string }> = {}) {
    const handlers = {
      onChangeType: overrides.onChangeType ?? vi.fn<Edit<AdditionalTermType>>(accepted),
      onRename: overrides.onRename ?? vi.fn<Edit<string>>(accepted),
      onChangeExpr: overrides.onChangeExpr ?? vi.fn<Edit<string>>(accepted),
      onChangeField: vi.fn(),
      onRemove: vi.fn(),
    }
    render(
      <TermCard
        kind="additional"
        termKey="age_sq"
        spec={{ type: "expression", expr: "age ** 2" }}
        typeOptions={[{ value: "expression", label: "Expression" }, { value: "target_encoding", label: "Target enc." }]}
        notice={overrides.notice}
        {...handlers}
      />,
    )
    return handlers
  }

  it("associates visible Name and Expression labels with the fields", () => {
    renderAdditional()
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-label", "age_sq name")
    expect(screen.getByLabelText("Expression")).toHaveAttribute("aria-label", "age_sq expression")
    expect(screen.getByRole("group", { name: "age_sq monotonicity" })).toBeInTheDocument()
  })

  it("keeps the draft and shows the reason per field when an edit is refused", () => {
    const onRename = vi.fn<Edit<string>>(() => ({ ok: false, reason: "income is a column; term names must not be columns." }))
    const onChangeExpr = vi.fn<Edit<string>>(() => ({ ok: false, reason: "height is not in the upstream data." }))
    const onChangeType = vi.fn<Edit<AdditionalTermType>>(() => ({ ok: false, reason: "This feature already has a target encoding." }))
    renderAdditional({ onRename, onChangeExpr, onChangeType })
    const name = screen.getByRole("textbox", { name: "age_sq name" })
    fireEvent.change(name, { target: { value: "income" } })
    fireEvent.blur(name)
    const expression = screen.getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expression, { target: { value: "height ** 2" } })
    fireEvent.blur(expression)
    fireEvent.change(screen.getByRole("combobox", { name: "age_sq term type" }), { target: { value: "target_encoding" } })
    expect(name).toHaveValue("income")
    expect(expression).toHaveValue("height ** 2")
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
      "This feature already has a target encoding.",
      "income is a column; term names must not be columns.",
      "height is not in the upstream data.",
    ])
    expect(onChangeType).toHaveBeenCalledWith("target_encoding")
  })

  it("shows why an additional term is unresolved", () => {
    renderAdditional({ notice: "height is not in the upstream data" })
    expect(screen.getByRole("alert")).toHaveTextContent("height is not in the upstream data")
  })
})

describe("TermCard unresolved", () => {
  it("shows the saved fit and reason and removes the term", () => {
    const onRemove = vi.fn()
    render(<TermCard kind="unresolved" termKey="y" spec={{ type: "bs", df: 5 }} reason="y is the target column" onRemove={onRemove} />)
    expect(screen.getByText("y")).toBeInTheDocument()
    expect(screen.getByText("B-spline")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("y is the target column")
    fireEvent.click(screen.getByRole("button", { name: "Remove y term" }))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  it("repairs a malformed entry keyed by an eligible column with a valid fit", () => {
    const onRepair = vi.fn()
    render(
      <TermCard
        kind="unresolved"
        termKey="age"
        spec={null}
        reason="This entry has no fit type"
        repairOptions={[{ value: "linear", label: "Linear" }, { value: "bs", label: "B-spline" }]}
        onRepair={onRepair}
        onRemove={vi.fn()}
      />,
    )
    const select = screen.getByRole("combobox", { name: "age term type" })
    expect(optionLabels(select)).toEqual(["Choose a fit…", "Linear", "B-spline"])
    fireEvent.change(select, { target: { value: "" } })
    expect(onRepair).not.toHaveBeenCalled()
    fireEvent.change(select, { target: { value: "bs" } })
    expect(onRepair).toHaveBeenCalledWith("bs")
  })
})

describe("TermCard slot", () => {
  it("offers only honoured fits and names the inherited fit", () => {
    const { onChangeFit } = renderSlot({ column: "income", dtype: "Float64", terms: { income: { type: "bs", df: 4 } } })
    const select = screen.getByRole("combobox", { name: "income fit in interaction" })
    expect(optionLabels(select)).toEqual(["As main (B-spline)", "Linear", "B-spline", "Nat. spline"])
    expect(select).toHaveValue("main")
    fireEvent.change(select, { target: { value: "linear" } })
    expect(onChangeFit).toHaveBeenCalledWith("linear")
  })

  it("selects the dtype default without writing a hidden override", () => {
    const handlers = renderSlot({ column: "flag", dtype: "Boolean" })
    expect(screen.getByRole("combobox", { name: "flag fit in interaction" })).toHaveValue("categorical")
    expect(handlers.onChangeFit).not.toHaveBeenCalled()
    expect(handlers.onChangeField).not.toHaveBeenCalled()
  })

  it("demands an explicit fit when the main effect cannot be inherited", () => {
    const { onChangeFit } = renderSlot({ column: "income", dtype: "Float64", terms: { income: { type: "linear", monotonicity: "increasing" } } })
    const select = screen.getByRole("combobox", { name: "income fit in interaction" })
    expect(select).toHaveValue("")
    expect(screen.getByRole("alert")).toHaveTextContent("Monotonicity is not applied inside interactions")
    fireEvent.change(select, { target: { value: "" } })
    expect(onChangeFit).not.toHaveBeenCalled()
  })

  it("keeps an unavailable saved override visible with the class refusal", () => {
    renderSlot({ column: "income", dtype: "Float64", override: { type: "categorical" } })
    const select = screen.getByRole("combobox", { name: "income fit in interaction" })
    expect(select).toHaveValue("categorical")
    expect(optionLabels(select)[0]).toBe("Categorical (unavailable)")
    expect(screen.getByRole("alert")).toHaveTextContent("Categorical is not available for a continuous column")
  })

  it("shows spline fields for a spline override and never monotonicity or levels", () => {
    const { onChangeSplineMode } = renderSlot({ column: "income", dtype: "Float64", override: { type: "bs", df: 5 } })
    expect(screen.getByRole("spinbutton", { name: "income df" })).toHaveValue(5)
    expect(screen.queryByRole("group", { name: "income monotonicity" })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "income df mode" }), { target: { value: "auto" } })
    expect(onChangeSplineMode).toHaveBeenCalledWith("auto")
    cleanup()
    renderSlot({ column: "region", dtype: "String", override: { type: "categorical" } })
    expect(screen.queryByRole("textbox", { name: "region levels" })).not.toBeInTheDocument()
  })

  it("shows target-encoding controls and its main-effect note for an explicit product fit", () => {
    renderSlot({ column: "region", dtype: "String", override: { type: "target_encoding" } })
    expect(screen.getByRole("combobox", { name: "region prior weight mode" })).toBeInTheDocument()
    expect(screen.getByText("Target encoding includes its main effect, even when Include main effects is off.")).toBeInTheDocument()
  })

  it("uses a named target encoding's settings and offers to drop a differing override", () => {
    const shared = { key: "region_te", spec: { type: "target_encoding", variable: "region", prior_weight: 2 } }
    const { onChangeFit } = renderSlot({
      column: "region",
      dtype: "String",
      terms: { region_te: shared.spec },
      override: { type: "target_encoding", prior_weight: 5 },
      sharedTargetEncoding: shared,
    })
    expect(screen.getByText("Uses target encoding settings from region_te.")).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "region prior weight mode" })).not.toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("This override differs from the shared target encoding settings.")
    fireEvent.click(screen.getByRole("button", { name: "Use shared settings" }))
    expect(onChangeFit).toHaveBeenCalledWith("target_encoding")
  })
})
