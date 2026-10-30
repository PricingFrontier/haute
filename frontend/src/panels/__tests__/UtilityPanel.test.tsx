import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import { answerQuestion, asked } from "../../test-utils/question"
import UtilityPanel from "../UtilityPanel"
import useGitStore from "../../stores/useGitStore"
import useToastStore from "../../stores/useToastStore"

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

  it("does not show a Save button (auto-save mode)", async () => {
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

describe("UtilityPanel auto-save", () => {
  const defaultProps = {
    onClose: vi.fn(),
    onImportAdded: vi.fn(),
    preamble: "from utility.features import *",
    onPreambleChange: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useToastStore.setState({ toasts: [] })
    useGitStore.setState({ uncaptured: [] })
    mockListFiles.mockResolvedValue({
      files: [{ name: "features.py", module: "features" }],
    })
    mockReadFile.mockResolvedValue({
      name: "features.py", module: "features", content: "x = 1\n", revision: "1-1",
    })
  })

  afterEach(() => {
    // Advance timers to flush any pending debounced saves before cleanup
    vi.advanceTimersByTime(1000)
    // Unmount first (triggers clearTimeout in the component) while fake timers are still active
    cleanup()
    vi.useRealTimers()
  })

  it("auto-saves after debounce when code changes", async () => {
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2" })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })

    // Should not have saved yet (within debounce window)
    expect(mockUpdateFile).not.toHaveBeenCalled()

    // Advance past the 500ms debounce
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 2\n", "1-1"))
  })

  it("debounces rapid edits (only saves last value)", async () => {
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2" })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    // Rapid edits
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })
    await act(async () => { vi.advanceTimersByTime(200) })
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 3\n" } })
    await act(async () => { vi.advanceTimersByTime(200) })
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 4\n" } })

    // Advance past debounce for the last edit
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => {
      expect(mockUpdateFile).toHaveBeenCalledTimes(1)
      expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 4\n", "1-1")
    })
  })

  it("flushes the pending debounced save when switching files (no lost edit)", async () => {
    mockListFiles.mockResolvedValue({
      files: [
        { name: "features.py", module: "features" },
        { name: "helpers.py", module: "helpers" },
      ],
    })
    mockReadFile.mockImplementation((module: string) =>
      Promise.resolve({ name: `${module}.py`, module, content: `# ${module}\n`, revision: "1-1" }),
    )
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2" })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toHaveValue("# features\n"))

    // Edit within the debounce window — do NOT advance past the 500ms timer.
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 99\n" } })
    expect(mockUpdateFile).not.toHaveBeenCalled()

    // Switch files immediately. The pending edit must be flushed (persisted),
    // not discarded — this is the regression F139 guards against.
    fireEvent.click(screen.getByText("features"))
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())
    fireEvent.click(screen.getByText("helpers"))

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 99\n", "1-1"))
    // And the switch still completed — the new file was loaded after the flush.
    await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("helpers"))
  })

  it("persists a pending edit when the panel unmounts", async () => {
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2" })

    const { unmount } = render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 5\n" } })
    expect(mockUpdateFile).not.toHaveBeenCalled()

    unmount()
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 5\n", "1-1"))
    expect(mockUpdateFile).toHaveBeenCalledTimes(1)
  })

  it("discards a pending edit when its file is deleted", async () => {
    mockDeleteFile.mockResolvedValue({ status: "ok" })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 6\n" } })
    fireEvent.click(screen.getByTitle("Delete features"))
    expect(asked()).toMatchObject({ title: "Delete features?", confirm: "Delete", danger: true })
    await answerQuestion("confirm")
    await waitFor(() => expect(mockDeleteFile).toHaveBeenCalledWith("features", "1-1"))

    await act(async () => { vi.advanceTimersByTime(600) })
    expect(mockUpdateFile).not.toHaveBeenCalled()
  })

  it("does not switch files when flushing a dirty draft fails", async () => {
    mockListFiles.mockResolvedValue({
      files: [
        { name: "features.py", module: "features" },
        { name: "helpers.py", module: "helpers" },
      ],
    })
    mockReadFile.mockImplementation((module: string) =>
      Promise.resolve({ name: `${module}.py`, module, content: `# ${module}\n`, revision: "1-1" }),
    )
    mockUpdateFile
      .mockRejectedValueOnce(
        new MockApiError("HTTP 400", 400, "Syntax error on line 1: invalid draft"),
      )
      .mockResolvedValueOnce({
        status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2",
      })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toHaveValue("# features\n"))

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "broken draft\n" } })
    fireEvent.click(screen.getByText("features"))
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())
    fireEvent.click(screen.getByText("helpers"))

    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "broken draft\n", "1-1"))
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    expect(screen.getByTestId("code-editor")).toHaveValue("broken draft\n")
    expect(screen.getByText("Syntax error on line 1: invalid draft")).toBeInTheDocument()
    expect(screen.getByText("features")).toBeInTheDocument()

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "fixed = True\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledTimes(2))

    fireEvent.click(screen.getByText("features"))
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())
    fireEvent.click(screen.getByText("helpers"))
    await waitFor(() => expect(mockReadFile).toHaveBeenCalledWith("helpers"))
  })

  it("waits for an in-flight autosave and blocks switching when it fails", async () => {
    mockListFiles.mockResolvedValue({
      files: [
        { name: "features.py", module: "features" },
        { name: "helpers.py", module: "helpers" },
      ],
    })
    mockReadFile.mockImplementation((module: string) =>
      Promise.resolve({ name: `${module}.py`, module, content: `# ${module}\n`, revision: "1-1" }),
    )
    let rejectSave!: (reason: unknown) => void
    mockUpdateFile.mockReturnValue(new Promise((_resolve, reject) => {
      rejectSave = reject
    }))

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toHaveValue("# features\n"))

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "broken draft\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    expect(mockUpdateFile).toHaveBeenCalledWith("features", "broken draft\n", "1-1")

    fireEvent.click(screen.getByText("features"))
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())
    fireEvent.click(screen.getByText("helpers"))
    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")

    await act(async () => {
      rejectSave(new MockApiError("HTTP 400", 400, "Syntax error on line 1: invalid draft"))
    })

    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    expect(screen.getByTestId("code-editor")).toHaveValue("broken draft\n")
    expect(await screen.findByText("Syntax error on line 1: invalid draft")).toBeInTheDocument()
  })

  it("blocks switching after a rejected autosave has fully settled", async () => {
    mockListFiles.mockResolvedValue({
      files: [
        { name: "features.py", module: "features" },
        { name: "helpers.py", module: "helpers" },
      ],
    })
    mockReadFile.mockImplementation((module: string) =>
      Promise.resolve({ name: `${module}.py`, module, content: `# ${module}\n`, revision: "1-1" }),
    )
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 1: invalid draft"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toHaveValue("# features\n"))

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "broken draft\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    expect(
      await screen.findByText("Syntax error on line 1: invalid draft"),
    ).toBeInTheDocument()

    // The request and queue cleanup have both settled before the switch.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    fireEvent.click(screen.getByText("features"))
    await waitFor(() => expect(screen.getByText("helpers")).toBeInTheDocument())
    fireEvent.click(screen.getByText("helpers"))
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(mockReadFile).not.toHaveBeenCalledWith("helpers")
    expect(screen.getByTestId("code-editor")).toHaveValue("broken draft\n")
    expect(screen.getByText("Syntax error on line 1: invalid draft")).toBeInTheDocument()
    expect(screen.getByText("features")).toBeInTheDocument()
  })

  it("shows syntax error from auto-save", async () => {
    // Item #76: backend emits a flat string detail "Syntax error on line N: <msg>".
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 3: unexpected EOF"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "def foo(\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(
      screen.getByText("Syntax error on line 3: unexpected EOF"),
    ).toBeInTheDocument())
  })

  it("clears error when user edits again", async () => {
    mockUpdateFile
      .mockRejectedValueOnce(
        new MockApiError("HTTP 400", 400, "Syntax error on line 3: unexpected EOF"),
      )
      .mockResolvedValueOnce({
        status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2",
      })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    // Trigger error
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "def foo(\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    await waitFor(() => expect(
      screen.getByText("Syntax error on line 3: unexpected EOF"),
    ).toBeInTheDocument())

    // Edit again — error should clear immediately
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "def foo(): pass\n" } })
    expect(screen.queryByText("Syntax error on line 3: unexpected EOF")).toBeNull()
  })

  it("shows error from failed auto-save", async () => {
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 1: Save failed"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(
      screen.getByText("Syntax error on line 1: Save failed"),
    ).toBeInTheDocument())
  })

  it("sends the revision each save returned with the next save", async () => {
    mockUpdateFile
      .mockResolvedValueOnce({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2" })
      .mockResolvedValueOnce({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "3-3" })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 2\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    await waitFor(() => expect(mockUpdateFile).toHaveBeenCalledWith("features", "x = 2\n", "1-1"))
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 3\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 3\n", "2-2"))
  })

  const changedOutside = "utility/features.py changed since you opened it. Open it again, then retry."

  const refuseAsChangedOutside = async () => {
    mockUpdateFile.mockRejectedValueOnce(new MockApiError(changedOutside, 412, changedOutside))
    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 7\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    await waitFor(() => expect(screen.getByTestId("utility-conflict")).toHaveTextContent(changedOutside))
  }

  it("keeps the draft and saves nothing more when the file changed outside the panel", async () => {
    await refuseAsChangedOutside()

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 8\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    expect(screen.getByTestId("code-editor")).toHaveValue("x = 8\n")
    expect(mockUpdateFile).toHaveBeenCalledTimes(1)
  })

  it("reloads the file as it is now, dropping the draft", async () => {
    await refuseAsChangedOutside()
    mockReadFile.mockResolvedValueOnce({
      name: "features.py", module: "features", content: "x = 9\n", revision: "5-5",
    })
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "6-6" })

    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    await waitFor(() => expect(screen.getByTestId("code-editor")).toHaveValue("x = 9\n"))
    expect(screen.queryByTestId("utility-conflict")).not.toBeInTheDocument()

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 10\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })
    await waitFor(() => expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 10\n", "5-5"))
  })

  it("overwrites the file with the draft against the revision it has now", async () => {
    await refuseAsChangedOutside()
    mockReadFile.mockResolvedValueOnce({
      name: "features.py", module: "features", content: "x = 9\n", revision: "5-5",
    })
    mockUpdateFile.mockResolvedValue({ status: "ok", name: "features.py", module: "features", import_line: "", revision: "6-6" })

    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }))

    await waitFor(() => expect(mockUpdateFile).toHaveBeenLastCalledWith("features", "x = 7\n", "5-5"))
    expect(screen.getByTestId("code-editor")).toHaveValue("x = 7\n")
    await waitFor(() => expect(screen.queryByTestId("utility-conflict")).not.toBeInTheDocument())
  })

  it("reports a save's ledger capture as every project write does", async () => {
    mockUpdateFile.mockResolvedValue({
      status: "ok", name: "features.py", module: "features", import_line: "", revision: "2-2",
      git_sha: "abc123", warnings: ["Changes saved; version capture failed (git error — see server log)."],
      identity_required: false, uncaptured: ["utility/features.py"],
    })

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "x = 8\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => expect(useGitStore.getState().uncaptured).toContain("utility/features.py"))
    expect(useToastStore.getState().toasts.map((toast) => toast.text)).toContain(
      "Changes saved; version capture failed (git error — see server log).",
    )
  })

  it("sets error-line attribute on editor when save detail mentions a line", async () => {
    // parseSyntaxError extracts the line number from the flat string via /line (\d+)/.
    mockUpdateFile.mockRejectedValue(
      new MockApiError("HTTP 400", 400, "Syntax error on line 5: unexpected token"),
    )

    render(<UtilityPanel {...defaultProps} />)
    await waitFor(() => expect(screen.getByTestId("code-editor")).toBeInTheDocument())

    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: "bad code\n" } })
    await act(async () => { vi.advanceTimersByTime(600) })

    await waitFor(() => {
      expect(screen.getByTestId("code-editor")).toHaveAttribute("data-error-line", "5")
    })
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
