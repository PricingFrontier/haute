/**
 * The side panels' shared controls (specs/frontend-shared): a whole number checked against
 * its least and greatest, a labelled row and a select row, a blank-label row, and the
 * quiet "+ Add" action.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Trash2 } from "lucide-react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { LabelledRow, QuietAction, SelectRow, wholeNumber } from ".."

describe("panel controls", () => {
  afterEach(cleanup)

  it("checks a whole number against a range, or a least number of a unit, never past a safe one", () => {
    const columns = wholeNumber({ min: 1, max: 12 })
    expect(columns("4")).toBeNull()
    expect(columns(" 12 ")).toBeNull()
    for (const text of ["0", "13", "2.5", "-1", "", "x"]) expect(columns(text)).toBe("Enter a whole number from 1 to 12.")

    const width = wholeNumber({ min: 240, unit: "pixels" })
    expect(width("1280")).toBeNull()
    expect(width("200")).toBe("Enter a whole number of pixels, 240 or more.")
    expect(width("9".repeat(20))).toBe("Enter a whole number of pixels, 240 or more.")
    expect(wholeNumber({ min: 0 })("-3")).toBe("Enter a whole number, 0 or more.")
  })

  it("labels a control in a row, and leaves the label's column blank for a row of tick boxes", () => {
    render(
      <>
        <LabelledRow label="Width">
          <input />
        </LabelledRow>
        <LabelledRow>
          <span>ticks</span>
        </LabelledRow>
      </>,
    )
    expect(screen.getByRole("textbox", { name: "Width" })).toBeInTheDocument()
    expect(screen.getByText("ticks").parentElement?.tagName).toBe("DIV")
  })

  it("chooses one of its choices in a select named by its label", () => {
    const onChange = vi.fn()
    render(<SelectRow label="Density" value="compact" choices={[{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }]} onChange={onChange} />)
    fireEvent.change(screen.getByRole("combobox", { name: "Density" }), { target: { value: "comfortable" } })
    expect(onChange).toHaveBeenCalledWith("comfortable")
  })

  it("is a quiet action with a plus, or the icon given, and passes its props on", () => {
    const onClick = vi.fn()
    render(
      <>
        <QuietAction onClick={onClick} className="self-start">
          Add spacer
        </QuietAction>
        <QuietAction icon={Trash2} disabled>
          Remove
        </QuietAction>
      </>,
    )
    const add = screen.getByRole("button", { name: "Add spacer" })
    expect(add).toHaveAttribute("type", "button")
    expect(add).toHaveClass("quiet-action", "self-start")
    fireEvent.click(add)
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled()
  })
})
