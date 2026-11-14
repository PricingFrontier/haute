import { createRef } from "react"
import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { Input, Select, TextArea } from ".."

describe("haute-ui's fields", () => {
  it("draws a text box as a field, a caller's class after the kit's, and passes its props and ref through", () => {
    const onChange = vi.fn()
    const ref = createRef<HTMLInputElement>()
    render(<Input ref={ref} aria-label="Name" value="Fleet" onChange={onChange} className="w-full" placeholder="A name" />)
    const input = screen.getByRole("textbox", { name: "Name" })

    expect(input.tagName).toBe("INPUT")
    expect(input.className).toBe("field w-full")
    expect(input).toHaveAttribute("placeholder", "A name")
    expect(ref.current).toBe(input)
    fireEvent.change(input, { target: { value: "Fleet renewal" } })
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it("draws the large size with a second class", () => {
    render(<Input fieldSize="large" aria-label="Branch" defaultValue="main" />)
    expect(screen.getByRole("textbox", { name: "Branch" }).className).toBe("field field-large")
  })

  it("draws the compact size with a second class", () => {
    render(<Input fieldSize="compact" aria-label="Column" defaultValue="age" />)
    expect(screen.getByRole("textbox", { name: "Column" }).className).toBe("field field-compact")
  })

  it("draws a text box of several lines the same way", () => {
    render(<TextArea aria-label="Note" defaultValue="One" className="resize-y" />)
    const area = screen.getByRole("textbox", { name: "Note" })
    expect(area.tagName).toBe("TEXTAREA")
    expect(area.className).toBe("field resize-y")
  })

  it("draws a native dropdown the same way, its options its children", () => {
    const onChange = vi.fn()
    render(
      <Select fieldSize="compact" aria-label="Kind" value="a" onChange={onChange}>
        <option value="a">First</option>
        <option value="b">Second</option>
      </Select>,
    )
    const select = screen.getByRole("combobox", { name: "Kind" })
    expect(select.tagName).toBe("SELECT")
    expect(select.className).toBe("field field-compact")
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["First", "Second"])
    fireEvent.change(select, { target: { value: "b" } })
    expect(onChange).toHaveBeenCalledTimes(1)
  })
})
