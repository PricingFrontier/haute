import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import SettingsModal from "../SettingsModal"

describe("SettingsModal", () => {
  afterEach(cleanup)

  it("renders the preamble as the textarea's initial value", () => {
    render(
      <SettingsModal
        preamble="import numpy as np"
        onPreambleChange={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement
    expect(textarea.value).toBe("import numpy as np")
    expect(screen.getByTestId("settings-modal")).toBeInTheDocument()
  })

  it("editing the textarea fires onPreambleChange with the new text", () => {
    const onPreambleChange = vi.fn()
    render(
      <SettingsModal preamble="" onPreambleChange={onPreambleChange} onClose={vi.fn()} />,
    )
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "import catboost" } })
    expect(onPreambleChange).toHaveBeenCalledWith("import catboost")
  })

  it("the close (✕) button calls onClose", () => {
    const onClose = vi.fn()
    render(<SettingsModal preamble="" onPreambleChange={vi.fn()} onClose={onClose} />)
    fireEvent.click(screen.getByLabelText("Close settings"))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("the Done button calls onClose", () => {
    const onClose = vi.fn()
    render(<SettingsModal preamble="" onPreambleChange={vi.fn()} onClose={onClose} />)
    fireEvent.click(screen.getByText("Done"))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("clicking the backdrop itself closes the modal", () => {
    const onClose = vi.fn()
    render(<SettingsModal preamble="" onPreambleChange={vi.fn()} onClose={onClose} />)
    const backdrop = screen.getByTestId("settings-modal")
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("clicking inside the dialog does not close the modal", () => {
    const onClose = vi.fn()
    render(<SettingsModal preamble="" onPreambleChange={vi.fn()} onClose={onClose} />)
    // Clicking the textarea bubbles to the backdrop, but target !== currentTarget.
    fireEvent.click(screen.getByRole("textbox"))
    expect(onClose).not.toHaveBeenCalled()
  })
})
