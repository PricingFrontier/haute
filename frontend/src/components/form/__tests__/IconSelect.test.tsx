import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Hash } from "lucide-react"
import IconSelect from "../IconSelect"

const options = [
  { value: "number", label: "Number" },
  { value: "text", label: "Text" },
] as const

describe("IconSelect", () => {
  afterEach(cleanup)

  it("shows the chosen option as its icon over a native select of the options", () => {
    const onChange = vi.fn()
    render(
      <IconSelect icon={Hash} value="number" options={options} onChange={onChange} ariaLabel="Type" title="Number (change the type)" className="w-6" />,
    )

    const select = screen.getByRole("combobox", { name: "Type" })
    expect(select).toHaveValue("number")
    expect(Array.from((select as HTMLSelectElement).options).map((option) => option.textContent)).toEqual(["Number", "Text"])
    expect(select.parentElement).toHaveAttribute("title", "Number (change the type)")
    expect(select.parentElement).toHaveClass("w-6")
    expect(select.parentElement?.querySelector("svg")).toHaveAttribute("aria-hidden", "true")

    fireEvent.change(select, { target: { value: "text" } })
    expect(onChange).toHaveBeenCalledWith("text")
  })
})
