/**
 * Render tests for ExternalFileEditor.
 *
 * Tests: file type toggle, model type toggles for catboost,
 * file path label + FileBrowser, code editor, placeholder changes.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ExternalFileEditor from "../../panels/editors/ExternalFileEditor"

vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    CodeEditor: ({ defaultValue, onChange, placeholder }: { defaultValue: string; onChange?: (v: string) => void; placeholder?: string }) => (
      <textarea
        data-testid="code-editor"
        defaultValue={defaultValue}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
      />
    ),
    FileBrowser: ({ currentPath, onSelect }: { currentPath?: string; onSelect: (path: string) => void }) => (
      <div data-testid="file-browser">
        <span data-testid="current-path">{currentPath || ""}</span>
        <button data-testid="select-file" onClick={() => onSelect("model.pkl")}>
          Select
        </button>
      </div>
    ),
  }
})

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

  it("shows all four file type buttons", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("PICKLE")).toBeTruthy()
    expect(screen.getByText("JSON")).toBeTruthy()
    expect(screen.getByText("JOBLIB")).toBeTruthy()
    expect(screen.getByText("CATBOOST")).toBeTruthy()
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

  it("calls onUpdate when clicking CATBOOST button", () => {
    const onUpdate = vi.fn()
    render(<ExternalFileEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("CATBOOST"))
    expect(onUpdate).toHaveBeenCalledWith("fileType", "catboost")
  })

  it("does not show Model Type toggles when file type is pickle (default)", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.queryByText("Model Type")).toBeNull()
    expect(screen.queryByText("Classifier")).toBeNull()
    expect(screen.queryByText("Regressor")).toBeNull()
  })

  it("shows Model Type toggles only when catboost is selected", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} config={{ fileType: "catboost" }} />)
    expect(screen.getByText("Model Type")).toBeTruthy()
    expect(screen.getByText("Classifier")).toBeTruthy()
    expect(screen.getByText("Regressor")).toBeTruthy()
  })

  it("calls onUpdate when clicking Regressor model type", () => {
    const onUpdate = vi.fn()
    render(<ExternalFileEditor {...DEFAULT_PROPS} onUpdate={onUpdate} config={{ fileType: "catboost" }} />)
    fireEvent.click(screen.getByText("Regressor"))
    expect(onUpdate).toHaveBeenCalledWith("modelClass", "regressor")
  })

  it("renders File Path section with FileBrowser", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("File Path")).toBeTruthy()
    expect(screen.getByTestId("file-browser")).toBeTruthy()
  })

  it("passes current path from config to FileBrowser", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} config={{ path: "models/my_model.pkl" }} />)
    expect(screen.getByTestId("current-path").textContent).toBe("models/my_model.pkl")
  })

  it("calls onUpdate when file is selected in FileBrowser", () => {
    const onUpdate = vi.fn()
    render(<ExternalFileEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByTestId("select-file"))
    expect(onUpdate).toHaveBeenCalledWith("path", "model.pkl")
  })

  it("renders Code label", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Code")).toBeTruthy()
  })

  it("renders CodeEditor with default value from config", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} config={{ code: "df = pl.DataFrame()" }} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("df = pl.DataFrame()")
  })

  it("does not synthesize file loading scaffold in the code editor", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} config={{ path: "models/model.pkl" }} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
    expect(editor.defaultValue).not.toContain("load_external_object")
    expect(editor.defaultValue).not.toContain("with open")
  })

  it("shows empty placeholder for code editor", () => {
    render(<ExternalFileEditor {...DEFAULT_PROPS} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.placeholder).toBe("")
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

  it("reflects external modelClass config changes when catboost (B22 fix)", () => {
    const { rerender } = render(
      <ExternalFileEditor {...DEFAULT_PROPS} config={{ fileType: "catboost", modelClass: "classifier" }} />
    )
    expect(screen.getByText("Model Type")).toBeTruthy()
    const classifierBtn = screen.getByText("Classifier").closest("button")!
    expect(classifierBtn.style.background).toContain("147")

    // Simulate external config change to regressor
    rerender(<ExternalFileEditor {...DEFAULT_PROPS} config={{ fileType: "catboost", modelClass: "regressor" }} />)
    const regressorBtn = screen.getByText("Regressor").closest("button")!
    expect(regressorBtn.style.background).toContain("147")
  })
})
