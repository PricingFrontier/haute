import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"
import { useState } from "react"

import { completionMatches } from "../completion"
import { ColumnListField, ColumnPicker, NumberField } from "../fields"

const COLUMNS = ["premium", "premium_net", "region", "Rate"]

function optionTexts(): string[] {
  return within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)
}

describe("column-name completion", () => {
  afterEach(cleanup)

  it("matches by prefix regardless of case, leaves out the exact match, and caps the list", () => {
    expect(completionMatches(COLUMNS, "pre")).toEqual(["premium", "premium_net"])
    expect(completionMatches(COLUMNS, "PRE")).toEqual(["premium", "premium_net"])
    expect(completionMatches(COLUMNS, "premium")).toEqual(["premium_net"])
    expect(completionMatches(COLUMNS, "", ["region"])).toEqual(["premium", "premium_net", "Rate"])
    expect(completionMatches(Array.from({ length: 20 }, (_, i) => `c${i}`), "c")).toHaveLength(8)
  })

  it("a column picker lists matches while typing, moves with the arrows and completes with Tab", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Sort key 1 column" />)
    const input = screen.getByRole("combobox", { name: "Sort key 1 column" })
    fireEvent.focus(input)
    expect(optionTexts()).toEqual(COLUMNS)
    fireEvent.change(input, { target: { value: "pre" } })
    expect(optionTexts()).toEqual(["premium", "premium_net"])
    fireEvent.keyDown(input, { key: "ArrowDown" })
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: "premium_net" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "ArrowUp" })
    fireEvent.keyDown(input, { key: "ArrowUp" })
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: "premium_net" })).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(input, { key: "Tab" })
    expect(onCommit).toHaveBeenLastCalledWith("premium_net")
    expect(input).toHaveValue("premium_net")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })

  it("a column picker still accepts a free-text name and closes the list on Escape", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Cast 1 column" />)
    const input = screen.getByRole("combobox", { name: "Cast 1 column" })
    fireEvent.change(input, { target: { value: "re" } })
    expect(screen.getByRole("listbox")).toBeInTheDocument()
    fireEvent.keyDown(input, { key: "Escape" })
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    fireEvent.change(input, { target: { value: "brand_new" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onCommit).toHaveBeenLastCalledWith("brand_new")
  })

  it("a column list adds the completed name as a chip and never suggests a chip already present", () => {
    const onChange = vi.fn()
    render(<ColumnListField columns={["region"]} onChange={onChange} suggestions={COLUMNS} ariaLabel="Group by" />)
    const input = screen.getByRole("combobox", { name: "Group by: add" })
    fireEvent.focus(input)
    expect(optionTexts()).toEqual(["premium", "premium_net", "Rate"])
    fireEvent.change(input, { target: { value: "pr" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(onChange).toHaveBeenLastCalledWith(["region", "premium"])
    expect(input).toHaveValue("")
    fireEvent.change(input, { target: { value: "typed" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onChange).toHaveBeenLastCalledWith(["region", "typed"])
  })

  it("clicking a completion picks it", () => {
    const onCommit = vi.fn()
    render(<ColumnPicker value="" onCommit={onCommit} suggestions={COLUMNS} ariaLabel="Window column" />)
    const input = screen.getByRole("combobox", { name: "Window column" })
    fireEvent.change(input, { target: { value: "r" } })
    fireEvent.mouseDown(within(screen.getByRole("listbox")).getByRole("option", { name: "Rate" }))
    expect(onCommit).toHaveBeenLastCalledWith("Rate")
  })

  it("keeps focus in the box through a completion and a commit", () => {
    function Picker() {
      const [value, setValue] = useState("")
      return <ColumnPicker value={value} onCommit={setValue} suggestions={COLUMNS} ariaLabel="Probe" />
    }
    render(<Picker />)
    const input = screen.getByRole("combobox", { name: "Probe" })
    input.focus()
    fireEvent.change(input, { target: { value: "pre" } })
    fireEvent.keyDown(input, { key: "Tab" })
    expect(input).toHaveValue("premium")
    expect(document.activeElement).toBe(input)
    fireEvent.change(input, { target: { value: "typed" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(input).toHaveValue("typed")
    expect(document.activeElement).toBe(input)
  })
})

describe("number fields", () => {
  afterEach(cleanup)

  it("validates the whole value before committing and recovers after a valid edit", () => {
    const onCommit = vi.fn()
    const { rerender } = render(<NumberField value={2} integer min={1} onCommit={onCommit} ariaLabel="Count" />)
    const input = screen.getByRole("spinbutton", { name: "Count" })
    const reject = (value: string) => {
      fireEvent.change(input, { target: { value } })
      fireEvent.blur(input)
      expect(onCommit).not.toHaveBeenCalled()
      expect(input).toHaveAttribute("aria-invalid", "true")
      expect(screen.getByRole("alert")).toBeInTheDocument()
    }
    reject("1.5")
    rerender(<NumberField value={3} integer min={1} onCommit={onCommit} ariaLabel="Count" />)
    expect(input).not.toHaveAttribute("aria-invalid")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    reject("")
    reject("Infinity")
    reject("0")
    fireEvent.change(input, { target: { value: "1e3" } })
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(1000)
    rerender(<NumberField value={1000} integer min={1} onCommit={onCommit} ariaLabel="Count" />)
    expect(input).not.toHaveAttribute("aria-invalid")
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("accepts decimals when the field is not an integer", () => {
    const onCommit = vi.fn()
    render(<NumberField value={2} onCommit={onCommit} ariaLabel="Rate" />)
    const input = screen.getByRole("spinbutton", { name: "Rate" })
    fireEvent.change(input, { target: { value: "1.5" } })
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(1.5)
  })
})
