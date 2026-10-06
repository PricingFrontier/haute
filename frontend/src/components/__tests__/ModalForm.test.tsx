import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { GitIdentityFields, ModalFormActions, ModalFormHeader } from "../ModalForm"

afterEach(cleanup)

describe("ModalForm blocks", () => {
  it("renders a heading with its line of context", () => {
    render(<ModalFormHeader title="Choose a branch">Saves go here.</ModalFormHeader>)
    expect(screen.getByRole("heading", { name: "Choose a branch" })).toBeInTheDocument()
    expect(screen.getByText("Saves go here.")).toBeInTheDocument()
  })

  it("reports each identity field's change under the caller's test ids", () => {
    const onNameChange = vi.fn()
    const onEmailChange = vi.fn()
    const onSetGlobalChange = vi.fn()
    render(
      <GitIdentityFields
        testIdPrefix="who"
        name=""
        email=""
        setGlobal={false}
        onNameChange={onNameChange}
        onEmailChange={onEmailChange}
        onSetGlobalChange={onSetGlobalChange}
        autoFocus
      />,
    )

    fireEvent.change(screen.getByTestId("who-name"), { target: { value: "Jane" } })
    fireEvent.change(screen.getByTestId("who-email"), { target: { value: "jane@example.com" } })
    fireEvent.click(screen.getByRole("checkbox", { name: /global git config/ }))

    expect(onNameChange).toHaveBeenCalledWith("Jane")
    expect(onEmailChange).toHaveBeenCalledWith("jane@example.com")
    expect(onSetGlobalChange).toHaveBeenCalledWith(true)
    expect(screen.getByTestId("who-email")).toHaveAttribute("type", "email")
    expect(document.activeElement).toBe(screen.getByTestId("who-name"))
  })

  it("cancels, and shows the busy label on a disabled submit", () => {
    const onCancel = vi.fn()
    const { rerender } = render(
      <ModalFormActions
        onCancel={onCancel}
        submitLabel="Continue"
        busyLabel="Working…"
        busy={false}
        disabled={false}
        submitTestId="go"
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("go")).toHaveTextContent("Continue")
    expect(screen.getByTestId("go")).toHaveAttribute("type", "submit")

    rerender(
      <ModalFormActions
        onCancel={onCancel}
        submitLabel="Continue"
        busyLabel="Working…"
        busy
        disabled
        submitTestId="go"
      />,
    )
    expect(screen.getByTestId("go")).toHaveTextContent("Working…")
    expect(screen.getByTestId("go")).toBeDisabled()
  })
})
