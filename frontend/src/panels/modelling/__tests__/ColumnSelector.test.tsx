import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ColumnSelector } from "../ColumnSelector"

const columns = [
  { name: "claim", dtype: "Int64" },
  { name: "region", dtype: "Utf8" },
  { name: "weight", dtype: "Float64" },
]

describe("ColumnSelector", () => {
  it("searches and selects only known numeric columns with the keyboard", () => {
    const onChange = vi.fn()
    render(<ColumnSelector label="Weight column" value="" columns={columns.filter((column) => column.dtype !== "Utf8")} onChange={onChange} optional />)

    fireEvent.click(screen.getByRole("button", { name: "Weight column" }))
    const search = screen.getByRole("combobox", { name: "Search Weight column" })
    fireEvent.change(search, { target: { value: "wei" } })
    expect(screen.queryByRole("option", { name: /region/i })).toBeNull()
    fireEvent.keyDown(search, { key: "ArrowDown" })
    fireEvent.keyDown(search, { key: "Enter" })

    expect(onChange).toHaveBeenCalledWith("weight")
  })

  it("shows an option's label after its name and finds it by its label", () => {
    const onChange = vi.fn()
    const labelled = [
      { name: "policy.br_period", dtype: "int", label: "Reporting period" },
      { name: "policy.state", dtype: "str", label: "State" },
    ]
    render(<ColumnSelector compact trigger="add" label="Add a cell" value="" placeholder="Add a cell" columns={labelled} onChange={onChange} />)
    fireEvent.click(screen.getByRole("button", { name: "Add a cell" }))
    const search = screen.getByRole("combobox", { name: "Search Add a cell" })
    fireEvent.change(search, { target: { value: "reporting per" } })
    const [option, ...rest] = screen.getAllByRole("option")
    expect(rest).toEqual([])
    expect(option).toHaveTextContent("policy.br_periodReporting periodint")
    expect(option).toHaveAttribute("title", "policy.br_period: Reporting period")
    fireEvent.keyDown(search, { key: "Enter" })
    expect(onChange).toHaveBeenCalledWith("policy.br_period")
  })

  it("keeps a saved unavailable value visible without making it selectable", () => {
    render(<ColumnSelector label="Offset column" value="removed_column" columns={columns} onChange={vi.fn()} optional />)
    expect(screen.getByRole("button", { name: "Offset column" })).toHaveTextContent("removed_column")
    expect(screen.getByRole("alert")).toHaveTextContent("removed_column is unavailable")
    fireEvent.click(screen.getByRole("button", { name: "Offset column" }))
    expect(screen.queryByRole("option", { name: /removed_column/i })).toBeNull()
  })

  it("announces the highlighted option and returns focus after Escape or selection", async () => {
    const onChange = vi.fn()
    render(<ColumnSelector label="Target column" value="" columns={columns} onChange={onChange} />)
    const trigger = screen.getByRole("button", { name: "Target column" })
    fireEvent.click(trigger)
    const search = screen.getByRole("combobox", { name: "Search Target column" })
    await waitFor(() => expect(search).toHaveFocus())
    const activeId = search.getAttribute("aria-activedescendant")
    expect(activeId).toBeTruthy()
    expect(document.getElementById(activeId!)).toHaveAttribute("role", "option")
    fireEvent.keyDown(search, { key: "ArrowDown" })
    expect(search.getAttribute("aria-activedescendant")).not.toBe(activeId)
    fireEvent.keyDown(search, { key: "Escape" })
    await waitFor(() => expect(trigger).toHaveFocus())
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Search Target column" }), { key: "Enter" })
    expect(onChange).toHaveBeenCalledWith("claim")
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})

afterEach(cleanup)

describe("a compact ColumnSelector's trigger looks", () => {
  afterEach(cleanup)

  it("draws an add button reading its placeholder, whose menu adds the column chosen", () => {
    const onChange = vi.fn()
    render(<ColumnSelector compact trigger="add" label="Add factor" value="" placeholder="Add factor" columns={columns} onChange={onChange} />)
    const trigger = screen.getByRole("button", { name: "Add factor" })
    expect(trigger).toHaveTextContent("Add factor")
    expect(trigger).toHaveClass("add-row-btn")

    fireEvent.click(trigger)
    fireEvent.click(screen.getAllByRole("option").find((option) => option.firstElementChild?.textContent === "region")!)
    expect(onChange).toHaveBeenCalledWith("region")
  })

  it("draws a quiet text action reading its placeholder, whose menu adds the column chosen", () => {
    const onChange = vi.fn()
    render(<ColumnSelector compact trigger="quiet" label="Add a cell" value="" placeholder="Add a cell" columns={columns} onChange={onChange} />)
    const trigger = screen.getByRole("button", { name: "Add a cell" })
    expect(trigger).toHaveTextContent("Add a cell")
    expect(trigger).toHaveClass("quiet-action")
    expect(trigger).not.toHaveClass("add-row-btn")

    fireEvent.click(trigger)
    fireEvent.click(screen.getAllByRole("option").find((option) => option.firstElementChild?.textContent === "weight")!)
    expect(onChange).toHaveBeenCalledWith("weight")
  })

  it("opens under its trigger, or over it near the window's foot where it would not fit below", () => {
    // jsdom lays nothing out: the menu is 250px tall, the window 768px.
    const height = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(250)
    const openAt = (top: number) => {
      render(<ColumnSelector compact label="Factor 1" value="" columns={columns} onChange={vi.fn()} />)
      const trigger = screen.getByRole("button", { name: "Factor 1" })
      vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue(new DOMRect(100, top, 200, 24))
      fireEvent.click(trigger)
      return screen.getByRole("listbox", { name: "Factor 1 options" }).parentElement!.style
    }
    try {
      const under = openAt(100)
      expect([under.top, under.bottom]).toEqual(["128px", ""])
      cleanup()

      const over = openAt(720)
      expect([over.top, over.bottom]).toEqual(["", "52px"])
    } finally {
      height.mockRestore()
    }
  })

  it("draws a form field that marks a saved column the list lacks and names the type of a known one", () => {
    const { rerender } = render(<ColumnSelector compact trigger="field" label="Factor 1" value="removed" columns={columns} onChange={vi.fn()} />)
    const trigger = screen.getByRole("button", { name: "Factor 1" })
    expect(trigger).toHaveClass("rounded-lg")
    expect(trigger).toHaveAttribute("title", "removed is not in the input's columns")
    expect(screen.queryByRole("alert")).toBeNull()

    rerender(<ColumnSelector compact trigger="field" label="Factor 1" value="claim" columns={columns} onChange={vi.fn()} />)
    expect(trigger).toHaveAttribute("title", "claim (Int64)")
  })
})

describe("ColumnSelector before the columns load", () => {
  afterEach(cleanup)

  it("shows the saved value without flagging it", () => {
    render(<ColumnSelector label="Target column" value="claims" columns={[]} onChange={vi.fn()} />)
    expect(screen.getByRole("button", { name: "Target column" })).toHaveTextContent("claims")
    expect(screen.queryByRole("alert")).toBeNull()
  })
})
