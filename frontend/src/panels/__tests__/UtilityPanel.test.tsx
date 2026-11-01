import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import { answerQuestion, asked } from "../../test-utils/question"
import UtilityPanel from "../UtilityPanel"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"
import useUIStore from "../../stores/useUIStore"

// Mock the CodeEditor (heavy CodeMirror dependency)
vi.mock("../editors/CodeEditor", () => ({
  CodeEditor: ({ defaultValue, onChange, placeholder, errorLine }: { defaultValue?: string; onChange?: (v: string) => void; placeholder?: string; errorLine?: number }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
      placeholder={placeholder}
      data-error-line={errorLine}
    />
  ),
}))

// Mock API client — vi.hoisted runs before vi.mock hoisting
const { mockListFiles, mockReadFile, mockCreateFile, mockUpdateFile, mockDeleteFile, MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  }
  return {
    mockListFiles: vi.fn(),
    mockReadFile: vi.fn(),
    mockCreateFile: vi.fn(),
    mockUpdateFile: vi.fn(),
    mockDeleteFile: vi.fn(),
    MockApiError,
  }
})

vi.mock("../../api/client", () => ({
  ApiError: MockApiError,
  listUtilityFiles: (...args: unknown[]) => mockListFiles(...args),
  readUtilityFile: (...args: unknown[]) => mockReadFile(...args),
  createUtilityFile: (...args: unknown[]) => mockCreateFile(...args),
  updateUtilityFile: (...args: unknown[]) => mockUpdateFile(...args),
  deleteUtilityFile: (...args: unknown[]) => mockDeleteFile(...args),
}))

describe("UtilityPanel", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
    preamble: "from utility.features import *",
    onPreambleChange: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockListFiles.mockResolvedValue({ files: [] })
  })

  afterEach(cleanup)

  it("renders header and close button", () => {
    render(<UtilityPanel {...defaultProps} />)
    expect(screen.getByText("Utility Scripts")).toBeInTheDocument()
    expect(screen.getByTitle("Close")).toBeInTheDocument()
  })

  describe("Imports entry", () => {
    const openImports = async () => {
      render(<UtilityPanel {...defaultProps} />)
      await waitFor(() => expect(mockListFiles).toHaveBeenCalled())
      fireEvent.click(screen.getByText("No files"))
      fireEvent.click(await screen.findByTestId("utility-imports-entry"))
      return screen.findByTestId("utility-imports")
    }

    it("is listed first and shows the preamble", async () => {
      mockListFiles.mockResolvedValue({ files: [{ module: "features", path: "utility/features.py" }] })
      mockReadFile.mockResolvedValue({ module: "features", content: "def f(): ..." })
      render(<UtilityPanel {...defaultProps} />)
      await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("features"))
      fireEvent.click(screen.getByText("features"))
      const imports = await screen.findByTestId("utility-imports-entry")
      expect(imports.previousElementSibling).toBeNull()
      expect(imports.nextElementSibling?.textContent).toBe("features")
      fireEvent.click(screen.getByTestId("utility-imports-entry"))
      await screen.findByTestId("utility-imports")
      expect(screen.getByTestId("code-editor")).toHaveValue("from utility.features import *")
      expect(screen.queryByTitle("Delete features")).not.toBeInTheDocument()
    })

    it("edits the preamble without the utility file routes", async () => {
      await openImports()
      fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "import numpy as np" } })
      expect(defaultProps.onPreambleChange).toHaveBeenCalledWith("import numpy as np")
      expect(mockUpdateFile).not.toHaveBeenCalled()
      expect(mockReadFile).not.toHaveBeenCalled()
    })

    it("notes the imports that are always included", async () => {
      await openImports()
      expect(screen.getByText(/import polars as pl/)).toBeInTheDocument()
      expect(screen.getByText(/import haute/)).toBeInTheDocument()
    })
  })

  it("close button calls onClose", () => {
    render(<UtilityPanel {...defaultProps} />)
    fireEvent.click(screen.getByTitle("Close"))
    expect(defaultProps.onClose).toHaveBeenCalledOnce()
  })

  it("shows empty state when no files", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("No utility files yet.")).toBeInTheDocument())
  })

  it("shows create button in empty state", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("Create one")).toBeInTheDocument())
  })

  it("loads files on mount", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n", revision: "1-1",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(mockListFiles).toHaveBeenCalledOnce())
  })

  it("auto-selects and loads first file", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "def foo(): pass\n", revision: "1-1",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("features"))
    expect(screen.getByTestId("code-editor")).toHaveValue("def foo(): pass\n")
  })

  it("shows module name in dropdown (no utility/ prefix or .py suffix)", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n", revision: "1-1",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => {
      expect(screen.getByText("features")).toBeInTheDocument()
      expect(screen.queryByText("utility/features.py")).toBeNull()
      expect(screen.queryByText("features.py")).toBeNull()
    })
  })

  it("does not show a Save button (it saves on close and Ctrl+S)", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n", revision: "1-1",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())
    expect(screen.queryByText("Save")).toBeNull()
    expect(screen.queryByText("Unsaved changes")).toBeNull()
    expect(screen.queryByText("Saved")).toBeNull()
  })

  it("shows a refused create in the empty panel, keeping the typed name", async () => {
    mockListFiles.mockResolvedValue({ files: [] })
    mockCreateFile.mockRejectedValue(
      new MockApiError("Invalid module name: 'class' is a Python keyword", 400),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("No utility files yet.")).toBeInTheDocument())
    fireEvent.click(screen.getByText("Create one"))
    const input = screen.getByPlaceholderText("module_name")
    fireEvent.change(input, { target: { value: "class" } })
    fireEvent.submit(input.closest("form")!)

    expect(await screen.findByTestId("utility-create-error")).toHaveTextContent("is a Python keyword")
    expect(screen.getByPlaceholderText("module_name")).toHaveValue("class")
    fireEvent.blur(screen.getByPlaceholderText("module_name"))
    expect(screen.getByPlaceholderText("module_name")).toHaveValue("class")
  })

  it("calls onImportAdded on create", async () => {
    mockListFiles.mockResolvedValue({ files: [] })
    mockCreateFile.mockResolvedValue({
      status: "ok", name: "helpers.py", module: "helpers",
      import_line: "from utility.helpers import *",
      revision: "1-1",
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("No utility files yet.")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Create one"))

    const input = screen.getByPlaceholderText("module_name")
    fireEvent.change(input, { target: { value: "helpers" } })
    fireEvent.submit(input.closest("form")!)

    await waitFor(() => {
      expect(mockCreateFile).toHaveBeenCalledWith({ name: "helpers" })
      expect(defaultProps.onImportAdded).toHaveBeenCalledWith("from utility.helpers import *")
    })
  })
})

describe("UtilityPanel saving", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
    preamble: "from utility.features import *",
    onPreambleChange: vi.fn(),
  }
  const saved = (content: string, revision: string, extra: Record<string, unknown> = {}) => ({
    status: "ok", name: "features.py", module: "features", import_line: "", content, revision, ...extra,
  })
  const editor = () => screen.getByTestId("code-editor")
  const type = (value: string) => fireEvent.change(editor(), { target: { value } })
  const pressSave = () => fireEvent.keyDown(editor(), { key: "s", ctrlKey: true })
  const twoFiles = () => {
    mockListFiles.mockResolvedValue({
      files: [
        { name: "features.py", module: "features" },
        { name: "helpers.py", module: "helpers" },
      ],
    })
    mockReadFile.mockImplementation((module: string) =>
      Promise.resolve({ name: `${module}.py`, module, content: `# ${module}\n`, revision: "1-1" }),
    )
  }
  const switchTo = async (module: string) => {
    fireEvent.click(screen.getByTestId("utility-file-selector"))
    fireEvent.click(await screen.findByText(module))
  }

  // The right-hand panel as App chooses it: the Git panel above the utility panel.
  function RightPanel() {
    const gitOpen = useUIStore((s) => s.gitOpen)
    const utilityOpen = useUIStore((s) => s.utilityOpen)
    if (gitOpen) return <div data-testid="git-panel" />
    if (utilityOpen) {
      return <UtilityPanel {...defaultProps} onClose={() => useUIStore.getState().setUtilityOpen(false)} />
    }
    return <div data-testid="no-panel" />
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useToastStore.setState({ toasts: [] })
    useGitStore.setState({ uncaptured: [] })
    useUIStore.setState({
      utilityOpen: true, gitOpen: false, utilityUnsaved: false, utilityLeave: null,
      utilityLeaveActions: [],
    })
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n", revision: "1-1",
    })
  })

  afterEach(cleanup)

  it("saves nothing while typing", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    type("x = 3\n")
    // Longer than the 500 ms the panel once waited before saving on its own.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 700)) })

    expect(mockUpdateFile).not.toHaveBeenCalled()
  })

  it("saves the draft when the panel closes, then closes", async () => {
    mockUpdateFile.mockResolvedValue(saved("x = 2\n", "2-2"))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    fireEvent.click(screen.getByTitle("Close"))

    await waitFor(() => expect(screen.getByTestId("no-panel")).toBeInTheDocument())
    expect(mockUpdateFile).toHaveBeenCalledExactlyOnceWith("features", "x = 2\n", "1-1")
  })

  it("closes at once when nothing is unsaved", async () => {
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    fireEvent.click(screen.getByTitle("Close"))

    expect(screen.getByTestId("no-panel")).toBeInTheDocument()
    expect(mockUpdateFile).not.toHaveBeenCalled()
  })

  it("holds another panel opening in its place until the draft is saved", async () => {
    let finish: (value: unknown) => void = () => {}
    mockUpdateFile.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    act(() => useUIStore.getState().setGitOpen(true))

    expect(screen.getByTestId("utility-panel")).toBeInTheDocument()
    expect(screen.queryByTestId("git-panel")).not.toBeInTheDocument()
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 2\n", "1-1"))
    await act(async () => { finish(saved("x = 2\n", "2-2")) })

    await waitFor(() => expect(screen.getByTestId("git-panel")).toBeInTheDocument())
  })

  it("stays open showing why when the save is refused, and Discard closes without saving", async () => {
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 1: invalid draft"),
    )
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("def broken(\n")
    act(() => useUIStore.getState().setGitOpen(true))

    expect(await screen.findByText("Syntax error on line 1: invalid draft")).toBeInTheDocument()
    expect(editor()).toHaveValue("def broken(\n")
    expect(screen.queryByTestId("git-panel")).not.toBeInTheDocument()
    expect(useUIStore.getState().utilityLeave).toBeNull()

    fireEvent.click(screen.getByTestId("utility-discard"))

    expect(screen.getByTestId("no-panel")).toBeInTheDocument()
    expect(mockUpdateFile).toHaveBeenCalledOnce()
  })

  it("saves on Ctrl+S without closing, and shows the file as the save left it", async () => {
    mockUpdateFile.mockResolvedValue(saved("x = 2\n# formatted\n", "2-2"))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    pressSave()

    await waitFor(() => expect(editor()).toHaveValue("x = 2\n# formatted\n"))
    expect(mockUpdateFile).toHaveBeenCalledExactlyOnceWith("features", "x = 2\n", "1-1")
    expect(screen.getByTestId("utility-panel")).toBeInTheDocument()

    // Nothing is left unsaved, so closing saves nothing more.
    fireEvent.click(screen.getByTitle("Close"))
    expect(screen.getByTestId("no-panel")).toBeInTheDocument()
    expect(mockUpdateFile).toHaveBeenCalledOnce()
  })

  it("keeps text typed while a save was under way, and saves it next with the revision returned", async () => {
    let finish: (value: unknown) => void = () => {}
    mockUpdateFile
      .mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
      .mockResolvedValueOnce(saved("x = 3\n", "3-3"))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    pressSave()
    type("x = 3\n")
    await act(async () => { finish(saved("x = 2\n", "2-2")) })

    expect(editor()).toHaveValue("x = 3\n")
    fireEvent.click(screen.getByTitle("Close"))
    await waitFor(() => expect(screen.getByTestId("no-panel")).toBeInTheDocument())
    expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 3\n", "2-2")
  })

  it("asks, saving nothing more, when the file changed during a save and text was typed since", async () => {
    let finish: (value: unknown) => void = () => {}
    mockUpdateFile.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    pressSave()
    type("x = 3\n")
    // A project hook, or another editor, changed the file while it was saved.
    await act(async () => { finish(saved("x = 2\n# formatted\n", "2-2")) })

    expect(await screen.findByText(/utility\/features\.py changed while it was saved/)).toBeInTheDocument()
    expect(editor()).toHaveValue("x = 3\n")
    fireEvent.click(screen.getByTitle("Close"))
    await waitFor(() => expect(useUIStore.getState().utilityLeave).toBeNull())
    expect(screen.getByTestId("utility-panel")).toBeInTheDocument()
    expect(mockUpdateFile).toHaveBeenCalledOnce()
  })

  it("sends a draft again after a save that failed", async () => {
    mockUpdateFile
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(saved("x = 2\n", "2-2"))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    pressSave()
    await waitFor(() => expect(screen.getByText("Failed to save")).toBeInTheDocument())
    pressSave()

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledTimes(2))
    expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 2\n", "1-1")
  })

  it("leaves Ctrl+S to the pipeline while the Imports entry is open", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())
    fireEvent.click(screen.getByTestId("utility-file-selector"))
    fireEvent.click(screen.getByTestId("utility-imports-entry"))
    await waitFor(() => expect(screen.getByTestId("utility-imports")).toBeInTheDocument())

    const key = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true })
    editor().dispatchEvent(key)

    expect(key.defaultPrevented).toBe(false)
  })

  it("saves the draft before opening another file", async () => {
    twoFiles()
    mockUpdateFile.mockResolvedValue(saved("x = 99\n", "2-2"))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toHaveValue("# features\n"))

    type("x = 99\n")
    await switchTo("helpers")

    await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("helpers"))
    expect(mockUpdateFile).toHaveBeenCalledExactlyOnceWith("features", "x = 99\n", "1-1")
  })

  it("does not open another file while the draft's save is refused", async () => {
    twoFiles()
    mockUpdateFile
      .mockRejectedValueOnce(new MockApiError("HTTP 400", 400, "Syntax error on line 1: invalid draft"))
      .mockResolvedValueOnce(saved("fixed = True\n", "2-2"))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toHaveValue("# features\n"))

    type("broken draft\n")
    await switchTo("helpers")

    expect(await screen.findByText("Syntax error on line 1: invalid draft")).toBeInTheDocument()
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    expect(editor()).toHaveValue("broken draft\n")

    type("fixed = True\n")
    await switchTo("helpers")
    await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("helpers"))
    expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "fixed = True\n", "1-1")
  })

  it("waits for a save under way and opens no other file when it is refused", async () => {
    twoFiles()
    let refuse: (reason: unknown) => void = () => {}
    mockUpdateFile.mockReturnValueOnce(new Promise((_resolve, reject) => { refuse = reject }))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toHaveValue("# features\n"))

    type("broken draft\n")
    pressSave()
    await switchTo("helpers")
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    await act(async () => {
      refuse(new MockApiError("HTTP 400", 400, "Syntax error on line 1: invalid draft"))
    })

    expect(await screen.findByText("Syntax error on line 1: invalid draft")).toBeInTheDocument()
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    expect(mockUpdateFile).toHaveBeenCalledOnce()
  })

  it("asks before the tab closes while a draft is unsaved", async () => {
    mockUpdateFile.mockResolvedValue(saved("x = 2\n", "2-2"))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())
    const unload = () => {
      const event = new Event("beforeunload", { cancelable: true })
      window.dispatchEvent(event)
      return event.defaultPrevented
    }

    expect(unload()).toBe(false)
    type("x = 2\n")
    expect(unload()).toBe(true)
    pressSave()
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalled())
    await waitFor(() => expect(unload()).toBe(false))
  })

  it("still saves a draft when the panel goes away by another path", async () => {
    mockUpdateFile.mockResolvedValue(saved("x = 5\n", "2-2"))
    const { unmount } = render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 5\n")
    unmount()

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledExactlyOnceWith("features", "x = 5\n", "1-1"))
    expect(useUIStore.getState().utilityUnsaved).toBe(false)
  })

  it("keeps a refused draft when the delete fails, and opens no other file over it", async () => {
    twoFiles()
    mockUpdateFile.mockRejectedValue(new MockApiError("HTTP 400", 400, "Syntax error on line 1: bad"))
    mockDeleteFile.mockRejectedValue(new Error("file in use"))
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("def broken(\n")
    pressSave()
    await screen.findByText("Syntax error on line 1: bad")
    fireEvent.click(screen.getByTitle("Delete features"))
    await answerQuestion("confirm")
    await waitFor(() => expect(mockDeleteFile).toHaveBeenCalled())
    await switchTo("helpers")

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledTimes(2))
    expect(editor()).toHaveValue("def broken(\n")
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    expect(useUIStore.getState().utilityUnsaved).toBe(true)
  })

  it("drops the draft when its file is deleted", async () => {
    mockDeleteFile.mockResolvedValue({ status: "ok" })
    const { unmount } = render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 6\n")
    fireEvent.click(screen.getByTitle("Delete features"))
    expect(asked()).toMatchObject({ title: "Delete features?", confirm: "Delete", danger: true })
    await answerQuestion("confirm")
    await waitFor(() => expect(mockDeleteFile).toHaveBeenCalledWith("features", "1-1"))
    unmount()

    expect(mockUpdateFile).not.toHaveBeenCalled()
  })

  it("shows a refused save's syntax error and marks its line, until the next edit", async () => {
    // The backend's flat string detail "Syntax error on line N: <msg>".
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 3: unexpected EOF"),
    )
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("def foo(\n")
    pressSave()

    expect(await screen.findByText("Syntax error on line 3: unexpected EOF")).toBeInTheDocument()
    expect(editor()).toHaveAttribute("data-error-line", "3")
    type("def foo(): pass\n")
    expect(screen.queryByText("Syntax error on line 3: unexpected EOF")).toBeNull()
  })

  it("sends the revision each save returned with the next save", async () => {
    mockUpdateFile
      .mockResolvedValueOnce(saved("x = 2\n", "2-2"))
      .mockResolvedValueOnce(saved("x = 3\n", "3-3"))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 2\n")
    pressSave()
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 2\n", "1-1"))
    type("x = 3\n")
    pressSave()

    await waitFor(() => expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 3\n", "2-2"))
  })

  const changedOutside = "utility/features.py changed since you opened it. Open it again, then retry."

  const refuseAsChangedOutside = async () => {
    mockUpdateFile.mockRejectedValueOnce(new MockApiError(changedOutside, 412, changedOutside))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())
    type("x = 7\n")
    pressSave()
    await waitFor(() => expect(screen.getByTestId("utility-conflict")).toHaveTextContent(changedOutside))
  }

  it("keeps the draft and saves nothing more when the file changed outside the panel", async () => {
    await refuseAsChangedOutside()

    type("x = 8\n")
    pressSave()
    await act(async () => {})

    expect(editor()).toHaveValue("x = 8\n")
    expect(mockUpdateFile).toHaveBeenCalledTimes(1)
  })

  it("reloads the file as it is now, dropping the draft", async () => {
    await refuseAsChangedOutside()
    mockReadFile.mockResolvedValueOnce({
      name: "features.py", module: "features", content: "x = 9\n", revision: "5-5",
    })
    mockUpdateFile.mockResolvedValue(saved("x = 10\n", "6-6"))

    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    await waitFor(() => expect(editor()).toHaveValue("x = 9\n"))
    expect(screen.queryByTestId("utility-conflict")).not.toBeInTheDocument()
    expect(useUIStore.getState().utilityUnsaved).toBe(false)

    type("x = 10\n")
    pressSave()
    await waitFor(() => expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 10\n", "5-5"))
  })

  it("overwrites the file with the draft against the revision it has now", async () => {
    await refuseAsChangedOutside()
    mockReadFile.mockResolvedValueOnce({
      name: "features.py", module: "features", content: "x = 9\n", revision: "5-5",
    })
    mockUpdateFile.mockResolvedValue(saved("x = 7\n", "6-6"))

    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }))

    await waitFor(() => expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 7\n", "5-5"))
    expect(editor()).toHaveValue("x = 7\n")
    await waitFor(() => expect(screen.queryByTestId("utility-conflict")).not.toBeInTheDocument())
    await waitFor(() => expect(useUIStore.getState().utilityUnsaved).toBe(false))
  })

  it("offers Discard beside the conflict, closing without saving", async () => {
    render(<RightPanel />)
    await waitFor(() => expect(editor()).toBeInTheDocument())
    mockUpdateFile.mockRejectedValueOnce(new MockApiError(changedOutside, 412, changedOutside))
    type("x = 7\n")
    fireEvent.click(screen.getByTitle("Close"))
    await waitFor(() => expect(screen.getByTestId("utility-conflict")).toBeInTheDocument())

    fireEvent.click(screen.getByTestId("utility-discard"))

    expect(screen.getByTestId("no-panel")).toBeInTheDocument()
    expect(mockUpdateFile).toHaveBeenCalledOnce()
  })

  // A save under way while more is typed: the save is refused as changed outside.
  const refuseWithMoreTyped = async () => {
    let refuse: (err: unknown) => void = () => {}
    mockUpdateFile.mockImplementationOnce(() => new Promise((_resolve, reject) => { refuse = reject }))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())
    type("x = 7\n")
    pressSave()
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledTimes(1))
    type("x = 8\n")
    await act(async () => { refuse(new MockApiError(changedOutside, 412, changedOutside)) })
    await waitFor(() => expect(screen.getByTestId("utility-conflict")).toBeInTheDocument())
    mockReadFile.mockResolvedValueOnce({
      name: "features.py", module: "features", content: "x = 9\n", revision: "5-5",
    })
    mockUpdateFile.mockResolvedValue(saved("x = 11\n", "6-6"))
  }

  it("lets nothing typed during the refused save land after a reload", async () => {
    await refuseWithMoreTyped()

    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    await waitFor(() => expect(editor()).toHaveValue("x = 9\n"))
    cleanup()

    expect(mockUpdateFile).toHaveBeenCalledTimes(1)
  })

  it("overwrites with the latest text, once", async () => {
    await refuseWithMoreTyped()
    type("x = 11\n")

    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }))
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 11\n", "5-5"))
    cleanup()

    expect(mockUpdateFile.mock.calls.map(([, content]) => content)).toEqual(["x = 7\n", "x = 11\n"])
  })

  it("recovers one way at a time and applies no read the panel has moved on from", async () => {
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }, { name: "helpers.py", module: "helpers" }],
    })
    await refuseAsChangedOutside()
    const answers: Array<(read: unknown) => void> = []
    mockReadFile.mockImplementation((module: string) => module === "helpers"
      ? Promise.resolve({ name: "helpers.py", module, content: "y = 1\n", revision: "1-1" })
      : new Promise((resolve) => { answers.push(resolve) }))
    const read = (content: string) => ({ name: "features.py", module: "features", content, revision: "5-5" })

    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    await act(async () => {})
    expect(answers).toHaveLength(1)
    expect(screen.getByRole("button", { name: "Overwrite" })).toBeDisabled()
    // The file cannot be left while its conflict stands.
    await switchTo("helpers")
    await act(async () => { answers[0](read("x = 9\n")) })
    await waitFor(() => expect(editor()).toHaveValue("x = 9\n"))
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")

    await switchTo("helpers")
    await waitFor(() => expect(editor()).toHaveValue("y = 1\n"))
    await act(async () => { for (const answer of answers.slice(1)) answer(read("x = 5\n")) })
    expect(editor()).toHaveValue("y = 1\n")
  })

  it("reports a save's ledger capture as every project write does", async () => {
    mockUpdateFile.mockResolvedValue(saved("x = 8\n", "2-2", {
      git_sha: "abc123", warnings: ["Changes saved; version capture failed (git error — see server log)."],
      identity_required: false, uncaptured: ["utility/features.py"],
    }))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(editor()).toBeInTheDocument())

    type("x = 8\n")
    pressSave()

    await waitFor(() => expect(useGitStore.getState().uncaptured).toContain("utility/features.py"))
    expect(useToastStore.getState().toasts.map((toast) => toast.text)).toContain(
      "Changes saved; version capture failed (git error — see server log).",
    )
  })
})

// ── U1: dropdown closes on outside click ───────────────────────────

describe("UtilityPanel dropdown close-on-outside-click", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
    preamble: "from utility.features import *",
    onPreambleChange: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockListFiles.mockResolvedValue({
      files: [
        { name: "helpers.py", module: "helpers" },
        { name: "utils.py", module: "utils" },
      ],
    })
    mockReadFile.mockResolvedValue({
      name: "helpers.py", module: "helpers", content: "x = 1\n", revision: "1-1",
    })
  })

  afterEach(cleanup)

  it("closes dropdown when clicking outside", async () => {
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())

    // Open the dropdown
    fireEvent.click(screen.getByText("helpers"))
    await waitFor(() => expect(screen.getByText("utils")).toBeInTheDocument())

    // Click outside the dropdown (on the document body)
    fireEvent.pointerDown(document.body)

    // Dropdown items should disappear
    await waitFor(() => {
      // "utils" only appears in the dropdown, not as active module
      expect(screen.queryByText("utils")).not.toBeInTheDocument()
    })
  })
})
