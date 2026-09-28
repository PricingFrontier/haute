import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"

import AddStepMenu from "../AddStepMenu"

describe("AddStepMenu", () => {
  afterEach(cleanup)

  it("opens on its search box, moves between kinds with the arrow keys, and returns focus to the button on Escape", () => {
    const onAdd = vi.fn()
    render(<AddStepMenu onAdd={onAdd} />)
    const button = screen.getByRole("button", { name: "Add step" })
    button.focus()
    fireEvent.click(button)
    const search = screen.getByRole("searchbox", { name: "Search step kinds" })
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

  it("closes through its Close action without adding, and adds the chosen kind", () => {
    const onAdd = vi.fn()
    render(<AddStepMenu onAdd={onAdd} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("button", { name: "Close" }))
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(onAdd).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.click(screen.getByRole("menuitem", { name: "Sort rows" }))
    expect(onAdd).toHaveBeenCalledWith("sort")
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  })

  it("filters by label, description or Polars call, adds the first match on Enter and leaves focus to the new step", () => {
    const onAdd = vi.fn()
    render(<AddStepMenu onAdd={onAdd} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    const search = screen.getByRole("searchbox", { name: "Search step kinds" })
    fireEvent.change(search, { target: { value: "group_by" } })
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Group and aggregate"])
    fireEvent.change(search, { target: { value: "nulls" } })
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Fill missing values"])
    fireEvent.change(search, { target: { value: "dup" } })
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Remove duplicates"])
    fireEvent.keyDown(search, { key: "Enter" })
    expect(onAdd).toHaveBeenCalledWith("unique")
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Add step" }))
  })

  it("does not answer to SAS or Excel names", () => {
    render(<AddStepMenu onAdd={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    fireEvent.change(screen.getByRole("searchbox", { name: "Search step kinds" }), { target: { value: "vlookup" } })
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0)
    expect(screen.getByText(/No step kind matches/)).toBeInTheDocument()
  })

  it("shows the focused kind's description and Polars call at its foot", () => {
    render(<AddStepMenu onAdd={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    const footer = screen.getByTestId("add-step-description")
    expect(footer).toHaveTextContent("Point at a kind to see what it does.")
    fireEvent.focus(screen.getByRole("menuitem", { name: "Sort rows" }))
    expect(footer).toHaveTextContent("Order rows by one or more columns")
    expect(within(footer).getByText("sort")).toBeInTheDocument()
    fireEvent.mouseEnter(screen.getByRole("menuitem", { name: "Group and aggregate" }))
    expect(footer).toHaveTextContent("Summarise rows per group")
    expect(within(footer).getByText("group_by().agg()")).toBeInTheDocument()
  })

  it("stays closed while disabled", () => {
    render(<AddStepMenu onAdd={vi.fn()} disabled />)
    const button = screen.getByRole("button", { name: "Add step" })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  })

  it("withholds only the named kinds and keeps the rest of their section, while filtering too", () => {
    render(<AddStepMenu onAdd={vi.fn()} withhold={new Set(["join", "concat"] as const)} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    const combine = within(screen.getByRole("menu")).getByRole("group", { name: "Combine" })
    expect(within(combine).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Group and aggregate",
      "Pivot to columns",
      "Unpivot to rows",
    ])
    expect(screen.getAllByRole("menuitem")).toHaveLength(15)
    fireEvent.change(screen.getByRole("searchbox", { name: "Search step kinds" }), { target: { value: "join" } })
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0)
  })
})
