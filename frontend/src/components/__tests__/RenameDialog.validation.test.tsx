/**
 * Phase 1 Package 1H — Item #36: RenameDialog must be a controlled input
 * with explicit validation.
 *
 * Pre-fix issues:
 *   1. `defaultValue` is used (uncontrolled).  Consumers cannot programmatically
 *      sync the input value, and React cannot trigger re-validation on the
 *      dialog state.
 *   2. Submission accepts any non-empty trimmed string — no length cap, no
 *      sanitization of characters that would break downstream Python code
 *      generation (e.g. backticks, semicolons, newlines).
 *
 * Fix: the input should be controlled (`value` + `onChange`), enforce a
 * sensible max length (we use 200 chars as a reasonable upper bound), and
 * reject names with unsafe characters by either disabling the submit button
 * or surfacing a validation message.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import RenameDialog from "../RenameDialog"

afterEach(cleanup)

function renderDialog(overrides: Partial<Parameters<typeof RenameDialog>[0]> = {}) {
  const props = {
    defaultValue: "My Node",
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  }
  return { ...render(<RenameDialog {...props} />), props }
}

describe("RenameDialog — controlled input & validation (#36)", () => {
  it("empty name submission is rejected", () => {
    // Baseline regression: existing behaviour that we want to preserve.
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("whitespace-only submission is rejected", () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "   \t  " } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("name longer than 200 chars is rejected", () => {
    // Catches: very long labels break the breadcrumb bar, context menu,
    // and code generation.  The dialog should refuse submission rather
    // than silently truncating.
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    const tooLong = "a".repeat(201)
    fireEvent.change(input, { target: { value: tooLong } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("exactly 200-char name is accepted", () => {
    // Boundary: 200 is allowed (not 201).
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    const exactly200 = "a".repeat(200)
    fireEvent.change(input, { target: { value: exactly200 } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).toHaveBeenCalledWith(exactly200)
  })

  it("rejects names with newline characters (breaks code-gen)", () => {
    // A node label with a newline would corrupt the generated Python
    // function name and comments. Must be rejected.
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "first\nsecond" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("rejects names with backticks (breaks markdown + code-gen)", () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "bad`name" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("rejects names with control characters", () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "with\u0000null" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("accepts names with spaces, dashes, underscores, numbers, mixed case", () => {
    // Positive case: normal labels work.  Our sanitisation happens at
    // code-gen time (sanitizeName), not in the display label — so the
    // dialog should accept human-readable labels freely.
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "My Transform 3-B" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).toHaveBeenCalledWith("My Transform 3-B")
  })

  it("accepts names with unicode letters (non-ASCII)", () => {
    // Policy: unicode letters are allowed in labels (sanitization strips
    // them at code-gen time; that's fine for the display label).
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "Calcul\u00e9 Net" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).toHaveBeenCalledWith("Calcul\u00e9 Net")
  })

  it("exposes a controlled value: typing updates the input value prop", () => {
    // Pre-fix: `defaultValue` is uncontrolled, so React has no way to
    // observe or reset the value.  Post-fix: input should be controlled.
    //
    // This test verifies that what the user types is reflected in the
    // input's rendered value.  It also catches the case where a
    // controlled input without an onChange would throw a React warning.
    renderDialog({ defaultValue: "initial" })
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    expect(input.value).toBe("initial")
    fireEvent.change(input, { target: { value: "updated" } })
    expect(input.value).toBe("updated")
  })

  it("submits trimmed value (leading/trailing whitespace stripped)", () => {
    // Regression: existing behaviour — confirm the fix preserves trim.
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "  trimmed  " } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).toHaveBeenCalledWith("trimmed")
  })
})
