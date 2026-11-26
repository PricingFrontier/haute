/**
 * Validation in a toolbar (specs/workbench): outlined green or red as the quote on the
 * sheets passes or fails the checks marking it final makes, and the box listing those it fails.
 */
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import fixture from "../../utils/__tests__/fixtures/quoteChecks.json"
import ValidationButton from "../ValidationButton"

const form = fixture.form as unknown as FormSpec
const PASSING = { values: { policy: [{ region: "south", age: "40", agreed: true }] }, tables: { policy: [{ premium: 250, premium_ok: true }] } }

afterEach(cleanup)

const lines = (testId: string) => within(screen.getByTestId(testId)).getAllByRole("listitem").map((item) => item.textContent)

describe("ValidationButton", () => {
  it("is outlined green while every check passes, its box saying nothing is failing and listing no check", () => {
    render(<ValidationButton form={form} {...PASSING} />)
    const button = screen.getByRole("button", { name: "Validation" })
    expect(button).toHaveAttribute("data-state", "passing")
    expect(button.style.boxShadow).toContain("var(--success)")
    expect(screen.queryByTestId("validation-box")).toBeNull()

    fireEvent.click(button)
    expect(screen.getByTestId("validation-box")).toHaveTextContent(/^Nothing is failing\.$/)
    expect(screen.queryByRole("listitem")).toBeNull()
  })

  it("is outlined red, counting the checks that fail, and lists those alone with the rows failing", () => {
    render(
      <ValidationButton
        form={form}
        values={{ policy: [{ region: "south", age: "16", agreed: true }], drivers: [{ name: "Ana" }, { age: "30" }] }}
        tables={{ policy: [{ premium: 250, premium_ok: false }], drivers: [{ row_number: 1, loading: 1, licence_ok: true }, { row_number: 2, loading: 1, licence_ok: true }] }}
      />,
    )
    const button = screen.getByRole("button", { name: "Validation (3)" })
    expect(button).toHaveAttribute("data-state", "failing")
    expect(button.style.boxShadow).toContain("var(--danger)")

    fireEvent.click(button)
    expect(lines("validation-failing")).toEqual(["policy · Age 17 to 99", "policy · Premium check The premium is below the minimum", "drivers · Name Needs a value · Row 2"])
    expect(screen.getByText("Failing (3)")).toBeInTheDocument()
    // What passes is not listed.
    expect(screen.getAllByRole("listitem")).toHaveLength(3)
    expect(screen.getByTestId("validation-box")).not.toHaveTextContent("Region")
  })

  it("fails a priced quote's output checks while it has no price", () => {
    render(<ValidationButton form={form} values={PASSING.values} tables={null} />)
    fireEvent.click(screen.getByRole("button", { name: "Validation (2)" }))
    expect(lines("validation-failing")).toEqual(["policy · Premium Needs a value", "policy · Premium check The premium is below the minimum"])
  })

  it("closes its box on Escape, the focus back on the button, and on a press outside", () => {
    render(<ValidationButton form={form} {...PASSING} />)
    const button = screen.getByRole("button", { name: "Validation" })
    fireEvent.click(button)
    fireEvent.keyDown(screen.getByTestId("validation-box"), { key: "Escape" })
    expect(screen.queryByTestId("validation-box")).toBeNull()
    expect(button).toHaveFocus()

    fireEvent.click(button)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByTestId("validation-box")).toBeNull()
  })

  it("is green for a form with no rules", () => {
    const plain: FormSpec = { ...form, schema: { tables: [] }, pages: [] }
    render(<ValidationButton form={plain} values={{}} tables={null} />)
    const button = screen.getByRole("button", { name: "Validation" })
    expect(button).toHaveAttribute("data-state", "passing")
    fireEvent.click(button)
    expect(screen.getByTestId("validation-box")).toHaveTextContent("Nothing is failing.")
  })

  it("is plain and opens nothing while disabled, or before the form is read", () => {
    const { rerender } = render(<ValidationButton form={form} values={{}} tables={null} disabled />)
    const button = screen.getByRole("button", { name: "Validation" })
    expect(button).toBeDisabled()
    expect(button.style.boxShadow).toBe("")

    rerender(<ValidationButton form={null} values={{}} tables={null} />)
    expect(screen.getByRole("button", { name: "Validation" })).toBeDisabled()
  })
})
