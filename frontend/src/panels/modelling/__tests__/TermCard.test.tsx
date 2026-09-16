import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { TermCard } from "../TermCard"

afterEach(cleanup)

describe("TermCard native", () => {
  it("retains an invalid advanced draft when the disclosure is closed and reopened", () => {
    render(<TermCard kind="native" column="age" spec={{ type: "bs", df: 5 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />)
    fireEvent.click(screen.getByText("Advanced"))
    const knots = screen.getByRole("textbox", { name: "age knots" })
    fireEvent.change(knots, { target: { value: "[3, 2]" } })
    fireEvent.blur(knots)
    fireEvent.click(screen.getByText("Advanced"))
    fireEvent.click(screen.getByText("Advanced"))
    expect(screen.getByRole("textbox", { name: "age knots" })).toHaveValue("[3, 2]")
    expect(screen.getByRole("alert")).toHaveTextContent("strictly increasing")
  })
  it("offers native types including frequency encoding and never Expression", () => {
    render(
      <TermCard kind="native" column="age" spec={{ type: "linear" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    const select = screen.getByRole("combobox", { name: "age term type" })
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value)
    expect(options).toEqual(["linear", "categorical", "bs", "ns", "ms", "target_encoding", "frequency_encoding"])
  })

  it("shows Auto/Fixed fit controls and an Auto prior weight without a misleading numeric default", () => {
    const { rerender } = render(
      <TermCard kind="native" column="age" spec={{ type: "bs", df: 4 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("combobox", { name: "age df mode" })).toHaveValue("fixed")
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(4)
    expect(screen.getByRole("spinbutton", { name: "age degree" })).toBeInTheDocument()
    rerender(
      <TermCard kind="native" column="age" spec={{ type: "ns", df: 3 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("combobox", { name: "age df mode" })).toHaveValue("fixed")
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(3)
    expect(screen.queryByRole("spinbutton", { name: "age degree" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "age monotonicity" })).not.toBeInTheDocument()
    rerender(
      <TermCard kind="native" column="brand" spec={{ type: "target_encoding" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByRole("combobox", { name: "brand prior weight mode" })).toHaveValue("auto")
    expect(screen.queryByRole("spinbutton", { name: "brand prior weight" })).not.toBeInTheDocument()
  })

  it("keeps visible labels associated with configured spline controls", () => {
    render(
      <TermCard kind="native" column="age" spec={{ type: "bs", df: 4, degree: 3 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByText("Fit type")).toBeInTheDocument()
    expect(screen.queryByText("Main term")).not.toBeInTheDocument()
    expect(screen.getByLabelText("df mode")).toHaveAttribute("aria-label", "age df mode")
    expect(screen.getByLabelText("df")).toHaveAttribute("aria-label", "age df")
    expect(screen.getByText("Degree")).toBeInTheDocument()
    expect(screen.getByLabelText("Degree")).toHaveAttribute("aria-label", "age degree")
  })

  it("keeps advanced spline fields hidden until expanded", () => {
    render(
      <TermCard kind="native" column="age" spec={{ type: "bs", df: 4 }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByText("Interior knots")).not.toBeVisible()
    expect(screen.getByText("Boundary knots")).not.toBeVisible()
    const advanced = screen.getByRole("button", { name: "Advanced" })
    expect(advanced).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(advanced)
    expect(advanced).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByText("Interior knots")).toBeVisible()
    expect(screen.getByText("Boundary knots")).toBeVisible()
  })

  it("emits explicit Auto and Fixed modes for spline df and target prior weight", () => {
    const onChangeField = vi.fn()
    const { rerender } = render(
      <TermCard
        kind="native"
        column="age"
        spec={{ type: "bs", degree: 4, knots: [1, 2] }}
        onChangeType={vi.fn()}
        onChangeField={onChangeField}
        onRemove={vi.fn()}
      />,
    )
    expect(screen.getByText("Custom knots")).toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "age df mode" }), { target: { value: "auto" } })
    expect(onChangeField).toHaveBeenCalledWith("df", undefined)
    rerender(
      <TermCard
        kind="native"
        column="brand"
        spec={{ type: "target_encoding", prior_weight: "auto" }}
        onChangeType={vi.fn()}
        onChangeField={onChangeField}
        onRemove={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByRole("combobox", { name: "brand prior weight mode" }), { target: { value: "fixed" } })
    expect(onChangeField).toHaveBeenCalledWith("prior_weight", 1)
  })

  it("validates advanced JSON arrays without replacing invalid drafts", () => {
    const onChangeField = vi.fn()
    render(
      <TermCard
        kind="native"
        column="age"
        spec={{ type: "bs", df: 5, boundary_knots: [0, 10] }}
        onChangeType={vi.fn()}
        onChangeField={onChangeField}
        onRemove={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText("Advanced"))
    const knots = screen.getByRole("textbox", { name: "age knots" })
    fireEvent.change(knots, { target: { value: "[1, 3, 4]" } })
    fireEvent.blur(knots)
    expect(onChangeField).toHaveBeenCalledWith("knots", [1, 3, 4])
    fireEvent.change(knots, { target: { value: "[3, 2]" } })
    fireEvent.blur(knots)
    expect(knots).toHaveValue("[3, 2]")
    expect(screen.getByRole("alert")).toHaveTextContent("strictly increasing")
  })

  it.each([
    ["levels", { type: "categorical" }, "brand levels", '["basic", "2"]', ["basic", "2"]],
    ["boundary knots", { type: "bs", df: 5 }, "age boundary knots", "[0, 10]", [0, 10]],
  ])("commits valid %s arrays", (_kind, spec, field, draft, expected) => {
    const onChangeField = vi.fn()
    render(
      <TermCard
        kind="native"
        column={field.startsWith("brand") ? "brand" : "age"}
        spec={spec}
        onChangeType={vi.fn()}
        onChangeField={onChangeField}
        onRemove={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText("Advanced"))
    const input = screen.getByRole("textbox", { name: field })
    fireEvent.change(input, { target: { value: draft } })
    fireEvent.blur(input)
    expect(onChangeField).toHaveBeenCalledWith(field.endsWith("levels") ? "levels" : "boundary_knots", expected)
  })

  it.each([
    ["age boundary knots", "[0, \"bad\"]"],
    ["age boundary knots", "[1e999, 2]"],
    ["brand levels", "[{}]"],
    ["brand levels", "[1]"],
  ])("refuses invalid advanced array %s: %s", (field, draft) => {
    const onChangeField = vi.fn()
    const categorical = field.startsWith("brand")
    render(
      <TermCard
        kind="native"
        column={categorical ? "brand" : "age"}
        spec={categorical ? { type: "categorical" } : { type: "bs", df: 5 }}
        onChangeType={vi.fn()}
        onChangeField={onChangeField}
        onRemove={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByText("Advanced"))
    onChangeField.mockClear()
    const input = screen.getByRole("textbox", { name: field })
    fireEvent.change(input, { target: { value: draft } })
    fireEvent.blur(input)
    expect(input).toHaveAttribute("aria-invalid", "true")
    expect(onChangeField).not.toHaveBeenCalled()
  })

  it("writes advanced basis size and permutations", () => {
    const onChangeField = vi.fn()
    const { rerender } = render(<TermCard kind="native" column="age" spec={{ type: "bs" }} onChangeType={vi.fn()} onChangeField={onChangeField} onRemove={vi.fn()} />)
    fireEvent.click(screen.getByText("Advanced"))
    fireEvent.change(screen.getByRole("spinbutton", { name: "age k" }), { target: { value: "12" } })
    expect(onChangeField).toHaveBeenCalledWith("k", 12)
    rerender(<TermCard kind="native" column="brand" spec={{ type: "target_encoding" }} onChangeType={vi.fn()} onChangeField={onChangeField} onRemove={vi.fn()} />)
    fireEvent.click(screen.getByText("Advanced"))
    fireEvent.change(screen.getByRole("spinbutton", { name: "brand n permutations" }), { target: { value: "6" } })
    expect(onChangeField).toHaveBeenCalledWith("n_permutations", 6)
  })

  it("changes a fixed prior weight of zero back to Auto", () => {
    const onChangeField = vi.fn()
    render(<TermCard kind="native" column="brand" spec={{ type: "target_encoding", prior_weight: 0 }} onChangeType={vi.fn()} onChangeField={onChangeField} onRemove={vi.fn()} />)
    expect(screen.getByRole("spinbutton", { name: "brand prior weight" })).toHaveValue(0)
    fireEvent.change(screen.getByRole("combobox", { name: "brand prior weight mode" }), { target: { value: "auto" } })
    expect(onChangeField).toHaveBeenCalledWith("prior_weight", undefined)
  })

  it("renders three monotonicity arrows for linear and bs, two for ms", () => {
    const { rerender } = render(
      <TermCard kind="native" column="age" spec={{ type: "linear" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getAllByRole("button", { name: /^age: / })).toHaveLength(3)
    rerender(
      <TermCard kind="native" column="age" spec={{ type: "ms", df: 4, monotonicity: "increasing" }} onChangeType={vi.fn()} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getAllByRole("button", { name: /^age: / })).toHaveLength(2)
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute("aria-pressed", "true")
  })

  it("routes edits to the callbacks", () => {
    const onChangeType = vi.fn()
    const onChangeField = vi.fn()
    const onRemove = vi.fn()
    render(
      <TermCard kind="native" column="age" spec={{ type: "bs", df: 4 }} onChangeType={onChangeType} onChangeField={onChangeField} onRemove={onRemove} />,
    )
    fireEvent.change(screen.getByRole("combobox", { name: "age term type" }), { target: { value: "ns" } })
    expect(onChangeType).toHaveBeenCalledWith("ns")
    fireEvent.change(screen.getByRole("spinbutton", { name: "age df" }), { target: { value: "6" } })
    expect(onChangeField).toHaveBeenCalledWith("df", 6)
    fireEvent.change(screen.getByRole("spinbutton", { name: "age df" }), { target: { value: "" } })
    expect(onChangeField).toHaveBeenCalledWith("df", undefined)
    fireEvent.click(screen.getByRole("button", { name: "age: decreasing" }))
    expect(onChangeField).toHaveBeenCalledWith("monotonicity", "decreasing")
    fireEvent.click(screen.getByRole("button", { name: "age: no constraint" }))
    expect(onChangeField).toHaveBeenCalledWith("monotonicity", undefined)
    fireEvent.click(screen.getByRole("button", { name: "Remove age term" }))
    expect(onRemove).toHaveBeenCalled()
  })
})

describe("TermCard expression", () => {
  it("renders an encoding additional card without expression fields", () => {
    render(<TermCard kind="additional" termKey="region_te" column="region" dtype="String" spec={{ type: "target_encoding", variable: "region" }} typeOptions={[{ value: "expression", label: "Expression", disabled: true }, { value: "target_encoding", label: "Target enc.", disabled: false }, { value: "frequency_encoding", label: "Frequency enc.", disabled: false }]} onChangeType={() => ({ ok: true, terms: {} })} onRename={() => ({ ok: true, terms: {} })} onChangeExpr={() => ({ ok: true, terms: {} })} onChangeField={vi.fn()} onRemove={vi.fn()} />)
    expect(screen.getByRole("combobox", { name: "region_te term type" })).toHaveValue("target_encoding")
    expect(screen.queryByRole("textbox", { name: "region_te name" })).not.toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "region_te expression" })).not.toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "region_te prior weight mode" })).toBeInTheDocument()
  })

  it("keeps a fit selector even for an expression with no other valid fit", () => {
    render(
      <TermCard kind="expression" termKey="age_sq" spec={{ type: "expression", expr: "age ** 2" }} onRename={() => ({ ok: true, terms: {} })} onChangeExpr={() => ({ ok: true, terms: {} })} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getAllByText("Expression")).not.toHaveLength(0)
    expect(screen.getByRole("combobox", { name: "age_sq term type" })).toHaveValue("expression")
    expect(screen.getByRole("textbox", { name: "age_sq name" })).toHaveValue("age_sq")
    expect(screen.getByRole("textbox", { name: "age_sq expression" })).toHaveValue("age ** 2")
    expect(screen.getByRole("textbox", { name: "age_sq expression" })).toHaveAttribute("title", expect.stringContaining("Supported forms"))
  })

  it("associates visible Name and Expression labels with the existing fields", () => {
    render(
      <TermCard kind="expression" termKey="age_sq" spec={{ type: "expression", expr: "age ** 2" }} onRename={() => ({ ok: true, terms: {} })} onChangeExpr={() => ({ ok: true, terms: {} })} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-label", "age_sq name")
    expect(screen.getByLabelText("Expression")).toHaveAttribute("aria-label", "age_sq expression")
  })

  it("keeps the draft and shows the reason when a rename or expression edit is refused", () => {
    const onRename = vi.fn(() => ({ ok: false as const, reason: "income is a column; expression names must not be columns." }))
    const onChangeExpr = vi.fn(() => ({ ok: false as const, reason: "Unknown column: height." }))
    render(
      <TermCard kind="expression" termKey="age_sq" spec={{ type: "expression", expr: "age ** 2" }} onRename={onRename} onChangeExpr={onChangeExpr} onChangeField={vi.fn()} onRemove={vi.fn()} />,
    )
    const name = screen.getByRole("textbox", { name: "age_sq name" })
    fireEvent.change(name, { target: { value: "income" } })
    fireEvent.blur(name)
    expect(onRename).toHaveBeenCalledWith("income")
    expect(name).toHaveValue("income")
    expect(screen.getByRole("alert")).toHaveTextContent("income is a column")
    const expr = screen.getByRole("textbox", { name: "age_sq expression" })
    fireEvent.change(expr, { target: { value: "height ** 2" } })
    fireEvent.blur(expr)
    expect(onChangeExpr).toHaveBeenCalledWith("height ** 2")
    expect(expr).toHaveValue("height ** 2")
    expect(screen.getByRole("alert")).toHaveTextContent("Unknown column: height")
  })
})

describe("TermCard slot", () => {
  it("shows target-encoding controls and its main-effect note for an explicit product fit", () => {
    render(<TermCard kind="slot" column="region" dtype="String" mainSpec={{ type: "categorical" }} otherSpecs={[{ type: "linear" }]} override={{ type: "target_encoding" }} onChangeFit={vi.fn()} onChangeField={vi.fn()} />)
    expect(screen.getByRole("combobox", { name: "region prior weight mode" })).toBeInTheDocument()
    expect(screen.getByText("Target encoding includes its main effect, even when Include main effects is off.")).toBeInTheDocument()
  })
  it.each([
    [{ prior_weight: 2, n_permutations: 7 }, false],
    [{ prior_weight: 3, n_permutations: 7 }, true],
    [{ prior_weight: "auto", n_permutations: 7 }, true],
    [{ n_permutations: 4 }, true],
  ])("compares explicit target override settings with shared settings", (override, warns) => {
    render(<TermCard kind="slot" column="region" dtype="String" mainSpec={{ type: "categorical" }} otherSpecs={[{ type: "linear" }]} override={{ type: "target_encoding", ...override }} sharedTargetEncoding={{ key: "region_te", spec: { type: "target_encoding", prior_weight: 2, n_permutations: 7 } }} onChangeFit={vi.fn()} onChangeField={vi.fn()} />)
    expect(screen.queryByRole("button", { name: "Use shared settings" }) !== null).toBe(warns)
  })
  it("keeps an explicit target encoding valid when the native main is target encoded", () => {
    render(<TermCard kind="slot" column="region" dtype="String" mainSpec={{ type: "target_encoding" }} otherSpecs={[{ type: "linear" }]} override={{ type: "target_encoding" }} onChangeFit={vi.fn()} onChangeField={vi.fn()} />)
    expect(screen.getByRole("combobox", { name: "region fit in interaction" })).toHaveValue("target_encoding")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
  it("offers only honoured fits and names the inherited fit in the selector", () => {
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={{ type: "linear" }} override={undefined} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    const select = screen.getByRole("combobox", { name: "age fit in interaction" })
    const options = Array.from(select.querySelectorAll("option"))
    expect(options.map((o) => o.value)).toEqual(["main", "linear", "bs", "ns"])
    expect(options.find((o) => o.value === "main")).toHaveTextContent("Linear")
    expect(screen.queryByText("Effective fit")).not.toBeInTheDocument()
    expect(select).toHaveValue("main")
  })

  it.each([["Float64", "linear"], ["String", "categorical"]])("selects the %s default without writing a hidden override", (dtype, fit) => {
    const onChangeFit = vi.fn()
    render(<TermCard kind="slot" column="feature" dtype={dtype} mainSpec={null} override={undefined} onChangeFit={onChangeFit} onChangeField={vi.fn()} />)
    const select = screen.getByRole("combobox", { name: "feature fit in interaction" })
    expect(select).toHaveValue(fit)
    expect(select.querySelector('option[value="main"]')).toBeNull()
    expect(screen.getByText("Fit type")).toBeInTheDocument()
    expect(onChangeFit).not.toHaveBeenCalled()
  })

  it("keeps an unavailable saved override visible for repair", () => {
    const onChangeFit = vi.fn()
    render(<TermCard kind="slot" column="region" dtype="String" mainSpec={null} override={{ type: "bs" }} onChangeFit={onChangeFit} onChangeField={vi.fn()} />)
    const select = screen.getByRole("combobox", { name: "region fit in interaction" })
    expect(select).toHaveValue("bs")
    expect(select).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByRole("alert")).toHaveTextContent("This fit is unavailable for this feature or its main term")
    expect(onChangeFit).not.toHaveBeenCalled()
    fireEvent.change(select, { target: { value: "categorical" } })
    expect(onChangeFit).toHaveBeenCalledWith("categorical")
  })

  it("shows spline fields for a spline override and never a monotonicity control", () => {
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={null} override={{ type: "bs", df: 3 }} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    expect(screen.getByRole("combobox", { name: "age fit in interaction" })).toHaveValue("bs")
    expect(screen.getByRole("spinbutton", { name: "age df" })).toHaveValue(3)
    expect(screen.getByRole("spinbutton", { name: "age degree" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^age: / })).not.toBeInTheDocument()
  })

  it("does not expose categorical levels for a product slot override", () => {
    render(
      <TermCard kind="slot" column="region" dtype="String" mainSpec={null} override={{ type: "categorical" }} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    expect(screen.queryByText("Levels")).not.toBeInTheDocument()
  })

  it("demands an explicit fit for a monotone main term", () => {
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={{ type: "ms", df: 4, monotonicity: "increasing" }} override={undefined} onChangeFit={vi.fn()} onChangeField={vi.fn()} />,
    )
    expect(screen.getByText("Monotone splines cannot be used inside interactions")).toBeInTheDocument()
    const select = screen.getByRole("combobox", { name: "age fit in interaction" })
    expect(select).toHaveValue("")
  })

  it("never emits an empty fit from the placeholder", () => {
    const onChangeFit = vi.fn()
    render(
      <TermCard kind="slot" column="age" dtype="Float64" mainSpec={{ type: "ms", df: 4, monotonicity: "increasing" }} override={undefined} onChangeFit={onChangeFit} onChangeField={vi.fn()} />,
    )
    const select = screen.getByRole("combobox", { name: "age fit in interaction" })
    const placeholder = Array.from(select.querySelectorAll("option")).find((o) => o.value === "")
    expect(placeholder).toBeDisabled()
    expect(select).toHaveValue("")
    fireEvent.change(select, { target: { value: "" } })
    expect(onChangeFit).not.toHaveBeenCalled()
  })
})
