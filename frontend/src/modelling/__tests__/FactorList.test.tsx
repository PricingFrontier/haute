import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import FactorList from "../FactorList"

const columns = [
  { name: "claims", dtype: "Float64" },
  { name: "age", dtype: "Int64" },
  { name: "region", dtype: "String" },
]

describe("FactorList", () => {
  afterEach(cleanup)

  it("keeps chart selection, settings and inclusion independent", () => {
    const onSelect = vi.fn()
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(<FactorList config={{ target: "claims", feature_columns: ["age"] }} columns={columns} selected="age" onSelect={onSelect} onUpdate={onUpdate} readOnly={false} />)
    const settings = screen.getByRole("button", { name: "region settings" })
    const checkbox = screen.getByRole("checkbox", { name: "Include region" })
    expect(settings).toHaveAttribute("aria-expanded", "false")
    expect(checkbox).not.toBeChecked()
    fireEvent.click(checkbox)
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith({ feature_columns: ["age", "region"] })
    expect(onSelect).not.toHaveBeenCalled()
    expect(settings).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(settings)
    expect(settings).toHaveAttribute("aria-expanded", "true")
    expect(onSelect).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole("button", { name: "region" }))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("region")
    expect(settings).toHaveAttribute("aria-expanded", "false")
    expect(checkbox).toBeInTheDocument()
  })

  it("keeps closing controls mounted for animation but hides them from interaction", () => {
    render(<FactorList config={{ target: "claims", feature_columns: ["age"] }} columns={columns} selected="age" onSelect={vi.fn()} onUpdate={vi.fn(() => ({ ok: true as const }))} readOnly={false} />)
    const age = screen.getByRole("button", { name: "age settings" })
    expect(screen.getByRole("checkbox", { name: "Include age" })).toBeChecked()
    fireEvent.click(age)
    const controls = screen.getByRole("group", { name: "age fitting options" })
    fireEvent.click(age)
    expect(controls).toBeInTheDocument()
    expect(controls.closest('[aria-hidden="true"]')).toHaveAttribute("inert")
    expect(screen.getByRole("checkbox", { name: "Include age" })).toBeChecked()
    fireEvent.click(age)
    expect(screen.getByRole("group", { name: "age fitting options" })).toBe(controls)
    expect(screen.getByRole("checkbox", { name: "Include age" })).toBeChecked()
  })

  it("keeps dormant roles and missing saved names when changing selection, in upstream order", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(<FactorList config={{ target: "claims", feature_columns: ["missing", "claims", "region"] }} columns={columns} selected={null} onSelect={vi.fn()} onUpdate={onUpdate} readOnly={false} />)
    expect(screen.queryByRole("button", { name: "claims" })).not.toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Include missing" })).toBeChecked()
    fireEvent.click(screen.getByRole("checkbox", { name: "Include missing" }))
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["claims", "region"] })
    fireEvent.click(screen.getByRole("button", { name: "age settings" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Include age" }))
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["claims", "age", "region", "missing"] })
    fireEvent.click(screen.getByRole("button", { name: "Exclude all factors" }))
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["claims", "missing"] })
    fireEvent.click(screen.getByRole("button", { name: "Include all factors" }))
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["claims", "age", "region", "missing"] })
    fireEvent.click(screen.getByRole("button", { name: "missing settings" }))
    expect(screen.getByText("Not found")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Remove missing factor" }))
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["claims", "region"] })
  })

  it("keeps a dormant direction when excluded and restores its controls on re-inclusion", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    const props = { columns, selected: "age", onSelect: vi.fn(), onUpdate, readOnly: false }
    const config = { target: "claims", monotone_constraints: { age: -1, region: 1 }, feature_columns: [] as string[] }
    const { rerender } = render(<FactorList {...props} config={config} />)
    fireEvent.click(screen.getByRole("button", { name: "age settings" }))
    expect(screen.getByRole("button", { name: "age: decreasing" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "age: decreasing" })).toBeDisabled()
    rerender(<FactorList {...props} config={{ ...config, feature_columns: ["age"] }} />)
    expect(screen.getByRole("button", { name: "age: decreasing" })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name: "age: no constraint" }))
    expect(onUpdate).toHaveBeenLastCalledWith("monotone_constraints", { region: 1 })
  })
})
