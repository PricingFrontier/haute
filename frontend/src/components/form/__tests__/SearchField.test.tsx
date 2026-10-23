/** The form kit's search box (specs/frontend-shared): its text, Clear while it holds any, and Escape. */
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import SearchField from "../SearchField"

afterEach(cleanup)

describe("SearchField", () => {
  it("is named by its label, its placeholder too, and passes on what is typed", () => {
    const onChange = vi.fn()
    render(<SearchField value="" onChange={onChange} label="Search fields" />)
    const input = screen.getByRole("textbox", { name: "Search fields" })
    expect(input).toHaveAttribute("placeholder", "Search fields")
    expect(screen.queryByRole("button", { name: "Clear search fields" })).toBeNull()

    fireEvent.change(input, { target: { value: "lim" } })
    expect(onChange).toHaveBeenCalledWith("lim")
  })

  it("empties by Clear or Escape while it holds text, Escape then heard by nothing else", () => {
    const onChange = vi.fn()
    const { rerender } = render(<SearchField value="lim" onChange={onChange} label="Search fields" />)
    fireEvent.click(screen.getByRole("button", { name: "Clear search fields" }))
    expect(onChange).toHaveBeenLastCalledWith("")

    const escape = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })
    screen.getByRole("textbox", { name: "Search fields" }).dispatchEvent(escape)
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(escape.defaultPrevented).toBe(true)

    // Empty, Escape is left to whatever else hears it.
    rerender(<SearchField value="" onChange={onChange} label="Search fields" />)
    const empty = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })
    screen.getByRole("textbox", { name: "Search fields" }).dispatchEvent(empty)
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(empty.defaultPrevented).toBe(false)
  })
})
