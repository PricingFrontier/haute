import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"

import AddStepMenu from "../AddStepMenu"

describe("AddStepMenu", () => {
  afterEach(cleanup)

  it("moves between kinds with the arrow keys, wraps at the ends, and returns focus to the button on Escape", () => {
    const onAdd = vi.fn()
    render(<AddStepMenu onAdd={onAdd} />)
    const button = screen.getByRole("button", { name: "Add step" })
    button.focus()
    fireEvent.click(button)
    const items = screen.getAllByRole("menuitem")
    expect(document.activeElement).toBe(items[0])
    fireEvent.keyDown(items[0], { key: "ArrowDown" })
    expect(document.activeElement).toBe(items[1])
    fireEvent.keyDown(items[1], { key: "ArrowRight" })
    expect(document.activeElement).toBe(items[2])
    fireEvent.keyDown(items[2], { key: "ArrowUp" })
    fireEvent.keyDown(items[1], { key: "ArrowLeft" })
    fireEvent.keyDown(items[0], { key: "ArrowUp" })
    expect(document.activeElement).toBe(items[items.length - 1])
    fireEvent.keyDown(items[items.length - 1], { key: "Escape" })
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

  it("stays closed while disabled", () => {
    render(<AddStepMenu onAdd={vi.fn()} disabled />)
    const button = screen.getByRole("button", { name: "Add step" })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  })

  it("withholds only the named kinds and keeps the rest of their section", () => {
    render(<AddStepMenu onAdd={vi.fn()} withhold={new Set(["join", "concat"] as const)} />)
    fireEvent.click(screen.getByRole("button", { name: "Add step" }))
    const combine = within(screen.getByRole("menu")).getByRole("group", { name: "Combine" })
    expect(within(combine).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Group and aggregate",
      "Pivot to columns",
      "Unpivot to rows",
    ])
    expect(screen.getAllByRole("menuitem")).toHaveLength(15)
  })
})
