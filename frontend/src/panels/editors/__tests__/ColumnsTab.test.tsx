import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ColumnsTab from "../ColumnsTab"

const COLUMNS = [
  { name: "a", dtype: "Int64" },
  { name: "b", dtype: "Float64" },
  { name: "c", dtype: "String" },
]

describe("ColumnsTab", () => {
  afterEach(cleanup)

  it("renders all columns with checkboxes checked by default", () => {
    render(
      <ColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    expect(screen.getByText("a")).toBeInTheDocument()
    expect(screen.getByText("b")).toBeInTheDocument()
    expect(screen.getByText("c")).toBeInTheDocument()
    const checkboxes = screen.getAllByRole("checkbox")
    expect(checkboxes).toHaveLength(3)
    checkboxes.forEach((cb) => expect(cb).toBeChecked())
  })

  it("shows unchecked columns when selected_columns excludes them", () => {
    render(
      <ColumnsTab
        config={{ selected_columns: ["a", "c"] }}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS.filter((c) => c.name !== "b")}
      />,
    )
    const checkboxes = screen.getAllByRole("checkbox")
    // Order: a (checked), b (unchecked), c (checked)
    expect(checkboxes[0]).toBeChecked()
    expect(checkboxes[1]).not.toBeChecked()
    expect(checkboxes[2]).toBeChecked()
  })

  it("calls onUpdate when unchecking a column via row click", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    // Click the row for column "b" to deselect it
    fireEvent.click(screen.getByText("b"))
    expect(onUpdate).toHaveBeenCalledWith("selected_columns", ["a", "c"])
  })

  it("ticking the last remaining box back on restores implicit all", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{ selected_columns: ["a", "c"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS.filter((c) => c.name !== "b")}
      />,
    )
    // Click row for "b" to re-add it — now all selected → empty
    fireEvent.click(screen.getByText("b"))
    expect(onUpdate).toHaveBeenCalledWith("selected_columns", [])
  })

  it("select all button clears selected_columns", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{ selected_columns: ["a"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS.filter((c) => c.name === "a")}
      />,
    )
    const allButtons = screen.getAllByRole("button", { name: /^All$/i })
    // Pick the enabled one
    const enabled = allButtons.find((b) => !(b as HTMLButtonElement).disabled)!
    fireEvent.click(enabled)
    expect(onUpdate).toHaveBeenCalledWith("selected_columns", [])
  })

  it("None unticks every box without touching config", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /^None$/i }))
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      expect(checkbox).not.toBeChecked()
    })
    expect(screen.getByText("0 / 3")).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent(
      "Select at least one column to apply.",
    )
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("first tick after None commits exactly that column", () => {
    const onUpdate = vi.fn()
    const { rerender } = render(
      <ColumnsTab
        config={{}}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: /^None$/i }))
    fireEvent.click(screen.getByText("b"))

    expect(onUpdate).toHaveBeenCalledOnce()
    expect(onUpdate).toHaveBeenCalledWith("selected_columns", ["b"])

    rerender(
      <ColumnsTab
        config={{ selected_columns: ["b"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={[]}
      />,
    )
    expect(screen.getByText("a")).toBeInTheDocument()
    expect(screen.getByText("b")).toBeInTheDocument()
    expect(screen.getByText("c")).toBeInTheDocument()
    expect(screen.queryByText(/Preview or run/)).not.toBeInTheDocument()
  })

  it("unticking the final committed column enters draft, not select-all", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{ selected_columns: ["a"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={[COLUMNS[0]]}
      />,
    )

    fireEvent.click(screen.getByText("a"))

    screen.getAllByRole("checkbox").forEach((checkbox) => {
      expect(checkbox).not.toBeChecked()
    })
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("re-creating the identical selection does not re-commit", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{ selected_columns: ["a"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS}
        columns={[COLUMNS[0]]}
      />,
    )

    fireEvent.click(screen.getByText("a"))
    fireEvent.click(screen.getByText("a"))

    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("does not mistake a stale selection plus one current column for all", () => {
    const onUpdate = vi.fn()
    render(
      <ColumnsTab
        config={{ selected_columns: ["missing"] }}
        onUpdate={onUpdate}
        availableColumns={COLUMNS.slice(0, 2)}
        columns={[]}
      />,
    )

    fireEvent.click(screen.getByText("a"))

    expect(onUpdate).toHaveBeenCalledWith(
      "selected_columns",
      ["missing", "a"],
    )
  })

  it("shows empty state when no columns available", () => {
    render(
      <ColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={[]}
        columns={[]}
      />,
    )
    expect(screen.getByText(/Preview or run/)).toBeInTheDocument()
  })

  it("filters columns by search", () => {
    render(
      <ColumnsTab
        config={{}}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText("Filter columns..."), {
      target: { value: "b" },
    })
    expect(screen.queryByText("a")).not.toBeInTheDocument()
    expect(screen.getByText("b")).toBeInTheDocument()
    expect(screen.queryByText("c")).not.toBeInTheDocument()
  })

  it("shows count badge", () => {
    render(
      <ColumnsTab
        config={{ selected_columns: ["a", "b"] }}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS.filter((c) => c.name !== "c")}
      />,
    )
    expect(screen.getByText("2 / 3")).toBeInTheDocument()
  })

  it("does not show implementation detail when columns are deselected", () => {
    render(
      <ColumnsTab
        config={{ selected_columns: ["a"] }}
        onUpdate={vi.fn()}
        availableColumns={COLUMNS}
        columns={COLUMNS.filter((c) => c.name === "a")}
      />,
    )
    expect(screen.queryByText(/\.select\(\)/)).not.toBeInTheDocument()
  })
})
