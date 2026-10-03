import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import SubmodelDialog from "../SubmodelDialog"

function renderDialog(overrides: Partial<Parameters<typeof SubmodelDialog>[0]> = {}) {
  const props = {
    nodeCount: 5,
    onClose: vi.fn(),
    onSubmit: vi.fn(async () => ({ ok: true as const })),
    ...overrides,
  }
  return { ...render(<SubmodelDialog {...props} />), props }
}

describe("SubmodelDialog", () => {
  afterEach(cleanup)
  it("renders node count in description", () => {
    renderDialog({ nodeCount: 3 })
    expect(screen.getByText(/3 selected nodes/)).toBeInTheDocument()
  })

  it("cancel button calls onClose", () => {
    const { props } = renderDialog()
    fireEvent.click(screen.getByText("Cancel"))
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("backdrop click calls onClose", () => {
    const { props } = renderDialog()
    const overlay = screen.getByRole("dialog")
    fireEvent.click(overlay)
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("empty name submission does NOT call onSubmit", () => {
    const { props } = renderDialog()
    fireEvent.click(screen.getByText("Create"))
    expect(props.onSubmit).not.toHaveBeenCalled()
  })

  it("valid name submission calls onSubmit with trimmed name", () => {
    const { props } = renderDialog()
    const input = screen.getByPlaceholderText("e.g. model_scoring")
    fireEvent.change(input, { target: { value: "  my_submodel  " } })
    fireEvent.click(screen.getByText("Create"))
    expect(props.onSubmit).toHaveBeenCalledWith("my_submodel")
  })

  it("a refused name keeps the dialog's typed name and shows why", async () => {
    const error = "Nodes 'pricing' (the pipeline) and 'pricing' (submodel 'pricing') take one name, `pricing`."
    const { props } = renderDialog({ onSubmit: vi.fn(async () => ({ ok: false as const, error })) })
    const input = screen.getByPlaceholderText("e.g. model_scoring")
    fireEvent.change(input, { target: { value: "pricing" } })
    fireEvent.click(screen.getByText("Create"))

    expect(await screen.findByRole("alert")).toHaveTextContent(error)
    expect(input).toHaveValue("pricing")
    expect(props.onClose).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByText("Create")).not.toBeDisabled())
  })

  it("Escape key calls onClose", () => {
    const { props } = renderDialog()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("non-Escape keys do NOT call onClose", () => {
    const { props } = renderDialog()
    fireEvent.keyDown(document, { key: "Enter" })
    fireEvent.keyDown(document, { key: "a" })
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("Escape handler is cleaned up on unmount", () => {
    const { props, unmount } = renderDialog()
    unmount()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).not.toHaveBeenCalled()
  })
})
