import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ConfigSelect from "../../../components/form/ConfigSelect"

afterEach(cleanup)

const OBJECT_OPTIONS = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta" },
  { value: "c", label: "Gamma" },
]

const STRING_OPTIONS = ["red", "green", "blue"]

describe("ConfigSelect", () => {
  it("renders all options (object format)", () => {
    render(<ConfigSelect value="a" onChange={vi.fn()} options={OBJECT_OPTIONS} />)
    const opts = screen.getAllByRole("option") as HTMLOptionElement[]
    expect(opts).toHaveLength(3)
    expect(opts[0].textContent).toBe("Alpha")
    expect(opts[1].textContent).toBe("Beta")
    expect(opts[2].textContent).toBe("Gamma")
  })

  it("renders all options (string[] format)", () => {
    render(<ConfigSelect value="red" onChange={vi.fn()} options={STRING_OPTIONS} />)
    const opts = screen.getAllByRole("option") as HTMLOptionElement[]
    expect(opts).toHaveLength(3)
    expect(opts[0].textContent).toBe("red")
    expect(opts[1].textContent).toBe("green")
    expect(opts[2].textContent).toBe("blue")
  })

  it("selected value is reflected", () => {
    render(<ConfigSelect value="b" onChange={vi.fn()} options={OBJECT_OPTIONS} />)
    const select = screen.getByRole("combobox") as HTMLSelectElement
    expect(select.value).toBe("b")
  })

  it("calls onChange when selecting new option", () => {
    const onChange = vi.fn()
    render(<ConfigSelect value="a" onChange={onChange} options={OBJECT_OPTIONS} />)
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "c" } })
    expect(onChange).toHaveBeenCalledWith("c")
  })

  it("renders label when provided", () => {
    render(<ConfigSelect value="a" onChange={vi.fn()} options={OBJECT_OPTIONS} label="Pick one" />)
    expect(screen.getByText("Pick one")).toBeTruthy()
    expect(screen.getByLabelText("Pick one")).toBeTruthy()
  })

  it("no label when label prop omitted", () => {
    const { container } = render(
      <ConfigSelect value="a" onChange={vi.fn()} options={OBJECT_OPTIONS} />,
    )
    expect(container.querySelector("label")).toBeNull()
  })

  it("empty options array renders no options", () => {
    const { container } = render(
      <ConfigSelect value="" onChange={vi.fn()} options={[]} />,
    )
    const select = container.querySelector("select")!
    expect(select.querySelectorAll("option")).toHaveLength(0)
  })

  it("disabled select is disabled", () => {
    render(<ConfigSelect value="a" onChange={vi.fn()} options={OBJECT_OPTIONS} disabled />)
    const select = screen.getByRole("combobox") as HTMLSelectElement
    expect(select.disabled).toBe(true)
  })

  it("disabled select has disabled attribute preventing interaction", () => {
    render(<ConfigSelect value="a" onChange={vi.fn()} options={OBJECT_OPTIONS} disabled />)
    const select = screen.getByRole("combobox") as HTMLSelectElement
    expect(select.disabled).toBe(true)
    expect(select.className).toContain("disabled:cursor-not-allowed")
    expect(select.className).toContain("disabled:opacity-50")
  })
})
