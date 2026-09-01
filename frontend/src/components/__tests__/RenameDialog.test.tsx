import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import RenameDialog from "../RenameDialog"

function renderDialog(overrides: Partial<Parameters<typeof RenameDialog>[0]> = {}) {
  const props = {
    defaultValue: "My Node",
    onConfirm: vi.fn(async () => ({ ok: true as const })),
    onCancel: vi.fn(),
    ...overrides,
  }
  return { ...render(<RenameDialog {...props} />), props }
}

describe("RenameDialog", () => {
  afterEach(cleanup)

  it("renders with the correct title", () => {
    renderDialog()
    expect(screen.getByText("Rename Node")).toBeInTheDocument()
  })

  it("has an accessible dialog role and label", () => {
    renderDialog()
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog).toHaveAttribute("aria-label", "Rename node")
  })

  it("populates the input with the default value", () => {
    renderDialog({ defaultValue: "Premium Calc" })
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    expect(input.value).toBe("Premium Calc")
  })

  it("auto-focuses the input on mount", () => {
    renderDialog()
    const input = screen.getByLabelText("Node name")
    expect(input).toHaveFocus()
  })

  it("cancel button calls onCancel", () => {
    const { props } = renderDialog()
    fireEvent.click(screen.getByText("Cancel"))
    expect(props.onCancel).toHaveBeenCalledTimes(1)
  })

  it("backdrop click calls onCancel", () => {
    const { props } = renderDialog()
    const overlay = screen.getByRole("dialog")
    fireEvent.click(overlay)
    expect(props.onCancel).toHaveBeenCalledTimes(1)
  })

  it("clicking inside the dialog does not call onCancel", () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name")
    fireEvent.click(input)
    expect(props.onCancel).not.toHaveBeenCalled()
  })

  it("Escape key calls onCancel", () => {
    const { props } = renderDialog()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onCancel).toHaveBeenCalledTimes(1)
  })

  it("submitting with a valid name calls onConfirm with trimmed value", async () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "  New Name  " } })
    fireEvent.click(screen.getByText("Rename"))
    expect(props.onConfirm).toHaveBeenCalledWith("New Name")
    await screen.findByText("Rename")
  })

  it("submitting via Enter key calls onConfirm", async () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "Enter Name" } })
    fireEvent.submit(input.closest("form")!)
    expect(props.onConfirm).toHaveBeenCalledWith("Enter Name")
    await screen.findByText("Rename")
  })

  it("keeps the dialog open and shows a resolver error", async () => {
    renderDialog({
      onConfirm: vi.fn(async () => ({ ok: false as const, error: "identity service unavailable" })),
    })
    fireEvent.click(screen.getByText("Rename"))
    expect(await screen.findByRole("alert")).toHaveTextContent("identity service unavailable")
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("empty name submission does NOT call onConfirm", () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "   " } })
    fireEvent.click(screen.getByText("Rename"))
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it("whitespace-only name submission does NOT call onConfirm", () => {
    const { props } = renderDialog()
    const input = screen.getByLabelText("Node name") as HTMLInputElement
    fireEvent.change(input, { target: { value: "\t  \n" } })
    fireEvent.click(screen.getByText("Rename"))
    expect(props.onConfirm).not.toHaveBeenCalled()
  })
})
