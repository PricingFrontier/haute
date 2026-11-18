/**
 * The side panels' shared controls (specs/frontend-shared): a whole number checked against
 * its least and greatest, a labelled row and a select row, a blank-label row, and the
 * quiet "+ Add" action.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Trash2 } from "lucide-react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { LabelledRow, QuietAction, SelectRow } from ".."
import { readNumberDraft } from "../../../utils/numberRule"

describe("panel controls", () => {
  afterEach(cleanup)

  it("checks a whole number against a range, or a least number of a unit, never past a safe one", () => {
    const columns = (text: string) => readNumberDraft(text, { integer: true, required: true, min: 1, max: 12 })
    expect(columns("4")).toEqual({ ok: true, value: 4 })
    expect(columns(" 12 ")).toEqual({ ok: true, value: 12 })
    for (const text of ["0", "13", "2.5", "-1", "", "x"]) expect(columns(text)).toEqual({ ok: false, problem: "Enter a whole number from 1 to 12." })

    const width = (text: string) => readNumberDraft(text, { integer: true, required: true, min: 240, unit: "pixels" })
    expect(width("1,280")).toEqual({ ok: true, value: 1280 })
    expect(width("200")).toEqual({ ok: false, problem: "Enter a whole number of pixels, 240 or more." })
    expect(width("9".repeat(20))).toEqual({ ok: false, problem: "Enter a whole number of pixels, 240 or more." })
    expect(readNumberDraft("-3", { integer: true, min: 0 })).toEqual({ ok: false, problem: "Enter a whole number, 0 or more." })
    // A comma that could be a decimal mark is never guessed.
    expect(width("1,5")).toEqual({ ok: false, problem: "Use a point for decimals." })
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
