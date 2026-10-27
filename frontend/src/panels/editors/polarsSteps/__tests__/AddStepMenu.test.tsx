import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"

import AddStepMenu from "../AddStepMenu"
import { exprProblem } from "../catalogue"

const open = (props: Partial<Parameters<typeof AddStepMenu>[0]> = {}) => {
  const onAdd = vi.fn()
  render(<AddStepMenu onAdd={onAdd} {...props} />)
  fireEvent.click(screen.getByRole("button", { name: "Add step" }))
  return { onAdd, search: screen.getByRole("searchbox", { name: "Search steps and functions" }) }
}
const names = () => screen.queryAllByRole("menuitem").map((item) => item.querySelector("span")?.textContent ?? item.textContent)

describe("AddStepMenu", () => {
  afterEach(cleanup)

  it("lists its five sections, the Add column calculations first", () => {
    open()
    const sections = within(screen.getByRole("menu")).getAllByRole("group")
    expect(sections.map((section) => section.getAttribute("aria-label"))).toEqual(["Add column", "Rows", "Columns", "Combine", "Other"])
    expect(within(sections[0]).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Formula", "If-then", "Window", "Number function", "Text function", "Date function"])
    expect(within(sections[4]).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Define variable", "Free code"])
    expect(screen.getAllByRole("menuitem")).toHaveLength(22)
  })

  it("opens on its search box, moves between entries with the arrow keys, and returns focus to the button on Escape", () => {
    const { onAdd, search } = open()
    const items = screen.getAllByRole("menuitem")
    expect(document.activeElement).toBe(search)
    fireEvent.keyDown(search, { key: "ArrowDown" })
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: "ArrowDown" })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(items[1], { key: "ArrowRight" })
    expect(document.activeElement).toBe(items[2])
    fireEvent.keyDown(items[2], { key: "ArrowUp" })
    fireEvent.keyDown(items[1], { key: "ArrowLeft" })
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: "ArrowUp" })
    expect(document.activeElement).toBe(search)
    fireEvent.keyDown(search, { key: "ArrowUp" })
    expect(document.activeElement).toBe(items[items.length - 1])
    fireEvent.keyDown(items[items.length - 1], { key: "ArrowDown" })
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: "Escape" })
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add step" }))
    expect(onAdd).not.toHaveBeenCalled()
  })

  it("closes through its Close action without adding, and hands back the chosen entry with its preset", () => {
    const { onAdd } = open()
    fireEvent.click(screen.getByRole("button", { name: "Close" }))
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(onAdd).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Sort rows" }))
    expect(onAdd).toHaveBeenCalledWith("sort", undefined)
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Number function" }))
    const [kind, preset] = onAdd.mock.calls.at(-1) ?? []
    expect(kind).toBe("with_column")
    expect(preset.expr).toMatchObject({ type: "function", fn: "round" })
    expect(exprProblem(preset.expr, "The expression")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Date function" }))
    expect(onAdd.mock.calls.at(-1)?.[1].expr).toMatchObject({ type: "function", fn: "offset_by" })
  })

  it("searches every calculation, window aggregate and Change types preset, saying which step a hit adds", () => {
    const { onAdd, search } = open()
    fireEvent.change(search, { target: { value: "sum" } })
    expect(names().slice(0, 3)).toEqual(["Window: sum", "Group and aggregate", "Sum across"])
    expect(screen.getByRole("menuitem", { name: /Window: sum/ })).toHaveTextContent("Add column")
    expect(screen.getByRole("menuitem", { name: /Group and aggregate/ })).toHaveTextContent(/^Group and aggregate$/)
    fireEvent.change(search, { target: { value: "trim" } })
    expect(names()[0]).toBe("Trim whitespace")
    fireEvent.change(search, { target: { value: "to_date" } })
    expect(names()).toEqual(["Text to date", "Text to date and time", "Text to date, missing where it doesn't parse", "Text to date and time, missing where it doesn't parse"])
    expect(screen.getAllByRole("menuitem")[0]).toHaveTextContent("Change types")
    fireEvent.keyDown(search, { key: "Enter" })
    expect(onAdd).toHaveBeenLastCalledWith("cast", { casts: [{ column: "", dtype: "Date", format: "%Y-%m-%d" }], strict: true })
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.change(screen.getByRole("searchbox", { name: "Search steps and functions" }), { target: { value: "try_cast" } })
    fireEvent.keyDown(screen.getByRole("searchbox", { name: "Search steps and functions" }), { key: "Enter" })
    expect(onAdd).toHaveBeenLastCalledWith("cast", { casts: [{ column: "", dtype: "Float64" }], strict: false })
  })

  it("ranks a name or Polars call that is the query first, and lists at most ten hits", () => {
    const { search } = open()
    fireEvent.change(search, { target: { value: "join" } })
    expect(names()).toEqual(["Join another input", "Join text"])
    fireEvent.change(search, { target: { value: "round" } })
    expect(names()).toEqual(["Round", "Round down", "Round up", "Number function"])
    fireEvent.change(search, { target: { value: "clip" } })
    expect(names()[0]).toBe("Clip")
    fireEvent.change(search, { target: { value: "e" } })
    expect(screen.getAllByRole("menuitem")).toHaveLength(10)
  })

  it("adds the first hit on Enter and leaves focus to the new step", () => {
    const { onAdd, search } = open()
    fireEvent.change(search, { target: { value: "group_by" } })
    expect(names()).toEqual(["Group and aggregate"])
    fireEvent.change(search, { target: { value: "nulls" } })
    expect(names()).toEqual(["Fill missing values"])
    fireEvent.change(search, { target: { value: "dup" } })
    expect(names()).toEqual(["Remove duplicates"])
    fireEvent.keyDown(search, { key: "Enter" })
    expect(onAdd).toHaveBeenCalledWith("unique", undefined)
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Add step" }))
  })

  it("does not answer to SAS or Excel names", () => {
    const { search } = open()
    fireEvent.change(search, { target: { value: "vlookup" } })
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0)
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument()
  })

  it("shows the focused entry's description and Polars call at its foot, telling Window from Group and aggregate", () => {
    open()
    const footer = screen.getByTestId("add-step-description")
    fireEvent.focus(screen.getByRole("menuitem", { name: "Sort rows" }))
    expect(footer).toHaveTextContent("Order rows by one or more columns")
    expect(within(footer).getByText("sort")).toBeInTheDocument()
    fireEvent.mouseEnter(screen.getByRole("menuitem", { name: "Window" }))
    expect(footer).toHaveTextContent("A group's value on every row")
    expect(within(footer).getByText("over")).toBeInTheDocument()
    fireEvent.mouseEnter(screen.getByRole("menuitem", { name: "Group and aggregate" }))
    expect(footer).toHaveTextContent("One row per group")
    expect(within(footer).getByText("group_by().agg()")).toBeInTheDocument()
  })

  it("stays closed while disabled", () => {
    render(<AddStepMenu onAdd={vi.fn()} disabled />)
    const button = screen.getByRole("button", { name: "Add step" })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  })

  it("withholds only the named kinds and keeps the rest of their section, while searching too", () => {
    const { search } = open({ withhold: new Set(["join", "concat"] as const) })
    const combine = within(screen.getByRole("menu")).getByRole("group", { name: "Combine" })
    expect(within(combine).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Group and aggregate",
      "Pivot to columns",
      "Unpivot to rows",
    ])
    expect(screen.getAllByRole("menuitem")).toHaveLength(20)
    fireEvent.change(search, { target: { value: "join" } })
    expect(names()).toEqual(["Join text"])
  })
})
