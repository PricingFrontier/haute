/**
 * Render tests for ExternalFileEditor.
 *
 * Tests: file type toggle (the loaders Load File has), the picker's
 * extensions, file path label + FileBrowser.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ExternalFileEditor from "../../panels/editors/ExternalFileEditor"

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    FileBrowser: ({
      currentPath,
      onSelect,
      extensions,
    }: {
      currentPath?: string
      onSelect: (path: string) => void
      extensions?: string
    }) => (
      <div data-testid="file-browser" data-extensions={extensions}>
        <span data-testid="current-path">{currentPath || ""}</span>
        <button data-testid="select-file" onClick={() => onSelect("model.pkl")}>
          Select
        </button>
      </div>
    ),
  }
})

vi.mock("../../panels/editors/CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange, placeholder }: { defaultValue: string; onChange?: (v: string) => void; placeholder?: string }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
      placeholder={placeholder}
    />
  ),
}))

afterEach(cleanup)

const DEFAULT_PROPS = {
  config: {},
  onUpdate: vi.fn(),
  inputSources: [],
  accentColor: "#93c5fd",
}

describe("ExternalFileEditor", () => {
  it("renders File Type label", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("File Type")).toBeTruthy()
  })

  it("offers the three loaders and no model file type", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("PICKLE")).toBeTruthy()
    expect(screen.getByText("JSON")).toBeTruthy()
    expect(screen.getByText("JOBLIB")).toBeTruthy()
    expect(screen.queryByText("CATBOOST")).toBeNull()
    expect(screen.queryByText("Model Type")).toBeNull()
  })

  it("browses only files a loader reads", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByTestId("file-browser").dataset.extensions).toBe(".pkl,.pickle,.json,.joblib")
  })

  it("defaults to pickle file type", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    const pickleBtn = screen.getByText("PICKLE").closest("button")!
    // Active button has accent background tint (jsdom renders #93c5fd tint as rgba(147, 197, 253, 0.1))
    expect(pickleBtn.style.background).toContain("147")
  })

  it("calls onUpdate when clicking JSON button", () => {
    const onUpdate = vi.fn()
    render(<ExternalFileEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("JSON"))
    expect(onUpdate).toHaveBeenCalledWith("fileType", "json")
  })

  it("calls onUpdate when clicking JOBLIB button", () => {
    const onUpdate = vi.fn()
    render(<ExternalFileEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("JOBLIB"))
    expect(onUpdate).toHaveBeenCalledWith("fileType", "joblib")
  })

  it("renders File Path section with FileBrowser", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("File Path")).toBeTruthy()
    expect(screen.getByTestId("file-browser")).toBeTruthy()
  })

  it("shows the configured path and passes it to FileBrowser after change", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} config={{ path: "models/my_model.pkl" }} />)
    expect(screen.getByText("models/my_model.pkl")).toBeTruthy()
    fireEvent.click(screen.getByTestId("file-change-btn"))
    expect(screen.getByTestId("current-path").textContent).toBe("models/my_model.pkl")
  })

  it("calls onUpdate when file is selected in FileBrowser", () => {
    const onUpdate = vi.fn()
    render(<ExternalFileEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByTestId("select-file"))
    expect(onUpdate).toHaveBeenCalledWith("path", "model.pkl")
  })

  it("reflects external fileType config changes (B22 fix)", () => {
    const { rerender } = render(<ExternalFileEditor {...DEFAULT_PROPS} config={{ fileType: "pickle" }} />)
    const pickleBtn = screen.getByText("PICKLE").closest("button")!
    // Pickle is active
    expect(pickleBtn.style.background).toContain("147")

    // Simulate external config change to json
    rerender(<ExternalFileEditor {...DEFAULT_PROPS} config={{ fileType: "json" }} />)
    const jsonBtn = screen.getByText("JSON").closest("button")!
    expect(jsonBtn.style.background).toContain("147")
  })
})
