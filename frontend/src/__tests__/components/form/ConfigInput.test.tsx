import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ConfigInput from "../../../components/form/ConfigInput"

afterEach(cleanup)

describe("ConfigInput", () => {
  it("renders with value", () => {
    render(<ConfigInput value="hello" onChange={vi.fn()} />)
    expect(screen.getByDisplayValue("hello")).toBeTruthy()
  })

  it("renders with label", () => {
    render(<ConfigInput value="" onChange={vi.fn()} label="Name" />)
    expect(screen.getByText("Name")).toBeTruthy()
    expect(screen.getByLabelText("Name")).toBeTruthy()
  })

  it("no label element when label prop omitted", () => {
    const { container } = render(<ConfigInput value="" onChange={vi.fn()} />)
    expect(container.querySelector("label")).toBeNull()
  })

  it("calls onChange with new value on input change", () => {
    const onChange = vi.fn()
    render(<ConfigInput value="" onChange={onChange} label="Field" />)
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "new" } })
    expect(onChange).toHaveBeenCalledWith("new")
  })

  it("renders placeholder", () => {
    render(<ConfigInput value="" onChange={vi.fn()} placeholder="Type here..." />)
    expect(screen.getByPlaceholderText("Type here...")).toBeTruthy()
  })

  it("disabled input has disabled attribute", () => {
    render(<ConfigInput value="" onChange={vi.fn()} disabled />)
    const input = screen.getByRole("textbox") as HTMLInputElement
    expect(input.disabled).toBe(true)
  })

  it("uses type='number' when specified", () => {
    render(<ConfigInput value="42" onChange={vi.fn()} type="number" />)
    const input = screen.getByRole("spinbutton") as HTMLInputElement
    expect(input.type).toBe("number")
    expect(input.value).toBe("42")
  })

  it("applies focus styling on focus", () => {
    render(<ConfigInput value="test" onChange={vi.fn()} label="Field" />)
    const input = screen.getByRole("textbox") as HTMLInputElement
    const borderBefore = input.style.borderColor
    fireEvent.focus(input)
    // After focus, border and shadow should change from the default state
    expect(input.style.borderColor).not.toBe(borderBefore)
    expect(input.style.boxShadow).not.toBe("")
    expect(input.style.boxShadow).not.toBe("none")
  })

  it("removes focus styling on blur", () => {
    render(<ConfigInput value="test" onChange={vi.fn()} label="Field" />)
    const input = screen.getByRole("textbox") as HTMLInputElement
    fireEvent.focus(input)
    const borderDuringFocus = input.style.borderColor
    fireEvent.blur(input)
    // After blur, border should change from the focused state and shadow should be removed
    expect(input.style.borderColor).not.toBe(borderDuringFocus)
    expect(input.style.boxShadow).toBe("none")
  })

  it("label htmlFor matches input id", () => {
    const { container } = render(<ConfigInput value="" onChange={vi.fn()} label="MyField" />)
    const label = container.querySelector("label") as HTMLLabelElement
    const input = screen.getByLabelText("MyField") as HTMLInputElement
    expect(label.htmlFor).toBe(input.id)
  })

  it("uses external id when provided", () => {
    render(<ConfigInput value="" onChange={vi.fn()} label="Field" id="custom-id" />)
    const input = screen.getByLabelText("Field") as HTMLInputElement
    expect(input.id).toBe("custom-id")
  })

  it("disabled input has correct styling class", () => {
    render(<ConfigInput value="val" onChange={vi.fn()} disabled />)
    const input = screen.getByRole("textbox") as HTMLInputElement
    expect(input.className).toContain("disabled:opacity-50")
    expect(input.className).toContain("disabled:cursor-not-allowed")
  })

  it("disabled input has disabled attribute preventing interaction", () => {
    render(<ConfigInput value="val" onChange={vi.fn()} disabled />)
    const input = screen.getByRole("textbox") as HTMLInputElement
    expect(input.disabled).toBe(true)
    expect(input.getAttribute("disabled")).toBe("")
  })

  it("number input calls onChange with string value", () => {
    const onChange = vi.fn()
    render(<ConfigInput value="10" onChange={onChange} type="number" />)
    const input = screen.getByRole("spinbutton") as HTMLInputElement
    fireEvent.change(input, { target: { value: "25" } })
    expect(onChange).toHaveBeenCalledWith("25")
  })
})
