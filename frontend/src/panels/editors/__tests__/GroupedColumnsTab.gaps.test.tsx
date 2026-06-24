import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import GroupedColumnsTab from "../GroupedColumnsTab"

// Columns that exercise pattern grouping: numeric array segments collapse to "*"
// and a shared top-level prefix groups them under "licence".
const COLUMNS = [
  { name: "licence.0.type", dtype: "String" },
  { name: "licence.1.type", dtype: "String" },
  { name: "licence.points", dtype: "Int64" },
  { name: "age", dtype: "Int64" },
]

describe("GroupedColumnsTab", () => {
  afterEach(cleanup)

  it("shows empty state when no columns available", () => {
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={[]}
        columns={[]}
      />,
    )
    expect(screen.getByText(/Preview or run/)).toBeInTheDocument()
  })

  it("renders group prefixes and the count badge", () => {
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    // Group headers for the two top-level prefixes.
    expect(screen.getByText("licence")).toBeInTheDocument()
    // "age" appears both as a group header and its own pattern row.
    expect(screen.getAllByText("age").length).toBeGreaterThan(0)
    // 4 concrete columns, none deselected → all selected.
    expect(screen.getByText("4 / 4")).toBeInTheDocument()
  })

  it("collapses 4 array columns into a ×2 pattern row", () => {
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    // licence.0.type and licence.1.type collapse to pattern "*.type" (×2 badge).
    expect(screen.getByText("×2")).toBeInTheDocument()
  })

  it("falls back to columns prop when availableColumns is empty", () => {
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={[]}
        columns={COLUMNS}
      />,
    )
    expect(screen.getByText("licence")).toBeInTheDocument()
  })

  it("filters columns by pattern via search", () => {
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText("Filter columns..."), {
      target: { value: "age" },
    })
    expect(screen.getAllByText("age").length).toBeGreaterThan(0)
    expect(screen.queryByText("licence")).not.toBeInTheDocument()
  })

  it("select all button clears selected_columns", () => {
    const onUpdate = vi.fn()
    render(
      <GroupedColumnsTab
        config={{ selected_columns: ["age"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS.filter((c) => c.name === "age")}
      />,
    )
    const allButtons = screen.getAllByRole("button", { name: /^All$/i })
    const enabled = allButtons.find((b) => !(b as HTMLButtonElement).disabled)!
    fireEvent.click(enabled)
    expect(onUpdate).toHaveBeenCalledWith("selected_columns", [])
  })

  it("select none button keeps first column", () => {
    const onUpdate = vi.fn()
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /^None$/i }))
    expect(onUpdate).toHaveBeenCalledWith("selected_columns", [COLUMNS[0].name])
  })

  it("shows hint text when columns are deselected", () => {
    render(
      <GroupedColumnsTab
        config={{ selected_columns: ["age"] }}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    expect(screen.getByText(/Deselected columns will be dropped/)).toBeInTheDocument()
  })

  it("toggling a pattern row when all selected deselects its concrete columns", () => {
    const onUpdate = vi.fn()
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    // The "points" pattern row is a single concrete column under licence.
    fireEvent.click(screen.getByLabelText("Select points"))
    expect(onUpdate).toHaveBeenCalledWith(
      "selected_columns",
      expect.not.arrayContaining(["licence.points"]),
    )
  })

  it("toggling a group checkbox deselects all its columns", () => {
    const onUpdate = vi.fn()
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    fireEvent.click(screen.getByLabelText("Select all licence columns"))
    const [, value] = onUpdate.mock.calls[0]
    // licence.* columns must all be gone; "age" remains.
    expect(value).not.toContain("licence.0.type")
    expect(value).not.toContain("licence.points")
    expect(value).toContain("age")
  })

  it("collapses a group when its chevron is clicked, hiding pattern rows", () => {
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    // The "points" pattern row is visible before collapse.
    expect(screen.getByLabelText("Select points")).toBeInTheDocument()
    // Collapse the licence group via its chevron (title "Collapse group").
    const chevrons = screen.getAllByTitle("Collapse group")
    fireEvent.click(chevrons[0])
    // After collapse the licence pattern rows are gone.
    expect(screen.queryByLabelText("Select points")).not.toBeInTheDocument()
  })

  it("stripping a group prefix renames its columns", () => {
    const onUpdate = vi.fn()
    render(
      <GroupedColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    // Click the clickable "licence" group prefix to strip it.
    fireEvent.click(screen.getByTitle('Strip "licence" prefix from column names'))
    const [key, value] = onUpdate.mock.calls[0]
    expect(key).toBe("column_renames")
    // Stripped names drop the leading "licence." segment.
    expect((value as Record<string, string>)["licence.points"]).toBe("points")
  })

  it("restoring a stripped group prefix clears its renames", () => {
    const onUpdate = vi.fn()
    render(
      <GroupedColumnsTab
        config={{
          column_renames: {
            "licence.0.type": "0.type",
            "licence.1.type": "1.type",
            "licence.points": "points",
          },
        }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    fireEvent.click(screen.getByTitle('Restore "licence" prefix'))
    const [key, value] = onUpdate.mock.calls[0]
    expect(key).toBe("column_renames")
    expect(value).toEqual({})
  })
})
