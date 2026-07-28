import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import PathPickerField from "../PathPickerField"

vi.mock("../../_shared", () => ({
  FileBrowser: ({
    currentPath,
    onSelect,
    showSelectionSummary = true,
  }: {
    currentPath?: string
    onSelect: (path: string) => void
    showSelectionSummary?: boolean
  }) => (
    <div data-testid="file-browser">
      {showSelectionSummary && (
        <span data-testid="browser-current-path">{currentPath ?? ""}</span>
      )}
      <button type="button" onClick={() => onSelect("selected.json")}>Select file</button>
    </div>
  ),
}))

afterEach(cleanup)

describe("PathPickerField", () => {
  it("shows the file browser immediately when no path is selected", () => {
    render(<PathPickerField label="Preview Data" value="" onSelect={vi.fn()} />)

    expect(screen.getByTestId("file-browser")).toBeInTheDocument()
  })

  it("collapses to the selected-path pill when a path is present", () => {
    render(<PathPickerField label="Preview Data" value="data/preview.json" onSelect={vi.fn()} />)

    expect(screen.getByText("data/preview.json")).toBeInTheDocument()
    expect(screen.getByTestId("file-change-btn")).toHaveTextContent("change")
    expect(screen.queryByTestId("file-browser")).not.toBeInTheDocument()
  })

  it("opens on change and selects a file before collapsing", () => {
    const onSelect = vi.fn()
    render(<PathPickerField label="Preview Data" value="data/preview.json" onSelect={onSelect} />)

    fireEvent.click(screen.getByTestId("file-change-btn"))
    expect(screen.getByTestId("file-change-btn")).toHaveTextContent("close")
    expect(screen.getAllByText("data/preview.json")).toHaveLength(1)
    expect(screen.queryByTestId("browser-current-path")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Select file" }))

    expect(onSelect).toHaveBeenCalledWith("selected.json")
    expect(screen.queryByTestId("file-browser")).not.toBeInTheDocument()
  })

  it("commits a manually entered path", () => {
    const onSelect = vi.fn()
    render(<PathPickerField label="Preview Data" value="" onSelect={onSelect} manualEntry />)

    fireEvent.change(screen.getByRole("textbox", { name: "Preview Data" }), { target: { value: "typed.json" } })
    fireEvent.blur(screen.getByRole("textbox", { name: "Preview Data" }))

    expect(onSelect).toHaveBeenCalledWith("typed.json")
  })
})
