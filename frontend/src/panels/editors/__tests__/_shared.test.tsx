import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react"
import type { EditorView } from "@codemirror/view"
import { CodeEditor, FileBrowser, MlflowStatusBadge, SchemaPreview } from "../_shared"
import type { SchemaInfo } from "../_shared"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/client", () => ({
  listFiles: vi.fn(),
}))

// Provide a minimal settings store with file-list cache helpers
const mockGetFileListCache = vi.fn<(key: string) => unknown[] | null>().mockReturnValue(null)
const mockSetFileListCache = vi.fn()
type MockMlflowStatus = {
  mlflowStatus: "loading" | "connected" | "error"
  mlflowBackend: string
  mlflowInstalled: boolean | null
  mlflowImportable: boolean | null
  mlflowTrackingConfigured: boolean | null
  mlflowDetail: string
}
const mockMlflowStatus = vi.hoisted(() => ({
  current: {
    mlflowStatus: "connected",
    mlflowBackend: "local",
    mlflowInstalled: true,
    mlflowImportable: true,
    mlflowTrackingConfigured: true,
    mlflowDetail: "",
  } as MockMlflowStatus,
}))

vi.mock("../../../stores/useSettingsStore", () => {
  const store = (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      getFileListCache: mockGetFileListCache,
      setFileListCache: mockSetFileListCache,
    })
  store.getState = () => ({
    getFileListCache: mockGetFileListCache,
    setFileListCache: mockSetFileListCache,
  })
  store.setState = vi.fn()
  store.subscribe = vi.fn()
  return {
    __esModule: true,
    default: store,
    useMlflowStatus: () => mockMlflowStatus.current,
  }
})

import { listFiles } from "../../../api/client"
const mockListFiles = listFiles as ReturnType<typeof vi.fn>

// ═══════════════════════════════════════════════════════════════════════════
// CodeEditor (CodeMirror 6)
// ═══════════════════════════════════════════════════════════════════════════

describe("MlflowStatusBadge", () => {
  afterEach(cleanup)

  beforeEach(() => {
    mockMlflowStatus.current = {
      mlflowStatus: "connected",
      mlflowBackend: "local",
      mlflowInstalled: true,
      mlflowImportable: true,
      mlflowTrackingConfigured: true,
      mlflowDetail: "",
    }
  })

  it("shows configured tracking backend when MLflow tracking config is healthy", () => {
    render(<MlflowStatusBadge />)

    expect(screen.getByRole("status")).toHaveTextContent("MLflow tracking configured (local)")
  })

  it("does not imply scoring is unavailable when only tracking is not configured", () => {
    mockMlflowStatus.current = {
      mlflowStatus: "error",
      mlflowBackend: "",
      mlflowInstalled: true,
      mlflowImportable: true,
      mlflowTrackingConfigured: false,
      mlflowDetail: "tracking backend misconfigured",
    }

    render(<MlflowStatusBadge />)

    const badge = screen.getByRole("status")
    expect(badge).toHaveTextContent("MLflow tracking not configured")
    expect(badge).toHaveAttribute("title", "tracking backend misconfigured")
    expect(badge).not.toHaveTextContent("MLflow not available")
  })

  it("distinguishes an import failure from tracking configuration failures", () => {
    mockMlflowStatus.current = {
      mlflowStatus: "error",
      mlflowBackend: "",
      mlflowInstalled: true,
      mlflowImportable: false,
      mlflowTrackingConfigured: false,
      mlflowDetail: "MLflow package import failed: broken dependency",
    }

    render(<MlflowStatusBadge />)

    expect(screen.getByRole("status")).toHaveTextContent("MLflow package failed to load")
  })

  it("distinguishes a missing MLflow package from tracking failures", () => {
    mockMlflowStatus.current = {
      mlflowStatus: "error",
      mlflowBackend: "",
      mlflowInstalled: false,
      mlflowImportable: false,
      mlflowTrackingConfigured: false,
      mlflowDetail: "MLflow package is not installed",
    }

    render(<MlflowStatusBadge />)

    expect(screen.getByRole("status")).toHaveTextContent("MLflow package missing")
  })

  it("reports status check failures without claiming MLflow itself is absent", () => {
    mockMlflowStatus.current = {
      mlflowStatus: "error",
      mlflowBackend: "",
      mlflowInstalled: null,
      mlflowImportable: null,
      mlflowTrackingConfigured: null,
      mlflowDetail: "MLflow check timed out after 5s",
    }

    render(<MlflowStatusBadge />)

    expect(screen.getByRole("status")).toHaveTextContent("MLflow status unavailable")
  })
})

describe("CodeEditor", () => {
  afterEach(cleanup)

  /** Helper: get the CM6 content element */
  function getEditorContent(container: HTMLElement) {
    return container.querySelector(".cm-content") as HTMLElement | null
  }

  /** Helper: get the full document text from the CM6 editor */
  function getEditorText(container: HTMLElement) {
    const content = getEditorContent(container)
    if (!content) return ""
    // CM6 renders lines as individual elements inside .cm-content
    // The textContent of .cm-content gives us the full document
    return content.textContent ?? ""
  }

  it("renders with default value", () => {
    const { container } = render(
      <CodeEditor defaultValue="hello world" onChange={vi.fn()} />,
    )
    expect(getEditorText(container)).toContain("hello world")
  })

  it("renders the wrapper div with test id", () => {
    render(<CodeEditor defaultValue="" onChange={vi.fn()} />)
    expect(screen.getByTestId("code-editor-wrapper")).toBeInTheDocument()
  })

  it("renders line numbers", () => {
    const { container } = render(
      <CodeEditor defaultValue="line1\nline2\nline3" onChange={vi.fn()} />,
    )
    const gutters = container.querySelector(".cm-lineNumbers")
    expect(gutters).toBeTruthy()
  })

  it("renders with placeholder when empty", () => {
    const { container } = render(
      <CodeEditor defaultValue="" onChange={vi.fn()} placeholder="Type code here..." />,
    )
    const ph = container.querySelector(".cm-placeholder")
    expect(ph).toBeTruthy()
    expect(ph?.textContent).toBe("Type code here...")
  })

  it("does not show placeholder when there is content", () => {
    const { container } = render(
      <CodeEditor defaultValue="x = 1" onChange={vi.fn()} placeholder="Type code here..." />,
    )
    const ph = container.querySelector(".cm-placeholder")
    expect(ph).toBeNull()
  })

  it("applies Python syntax highlighting", () => {
    const { container } = render(
      <CodeEditor defaultValue="def foo():\n    return 42" onChange={vi.fn()} />,
    )
    // CM6 with Python should produce syntax spans
    const content = getEditorContent(container)
    expect(content).toBeTruthy()
    // "def" should be in a highlighted span (not just raw text)
    const spans = content!.querySelectorAll("span")
    expect(spans.length).toBeGreaterThan(0)
  })

  it("mounts without error when onChange is provided", () => {
    const onChange = vi.fn()
    const { container } = render(
      <CodeEditor defaultValue="x = 1" onChange={onChange} />,
    )
    // Editor mounts and renders content — onChange wiring is internal to CM6's
    // updateListener and cannot be exercised via jsdom (no real contenteditable
    // input support). Integration coverage for the debounced callback requires
    // a browser-based test (e.g. Playwright).
    expect(getEditorContent(container)).toBeTruthy()
  })

  it("applies external updates while focused when the buffer matches the last prop", async () => {
    let view: EditorView | null = null
    const onChange = vi.fn()
    const { container, rerender } = render(
      <CodeEditor
        defaultValue="import math"
        onChange={onChange}
        onEditorView={(editorView) => { view = editorView }}
      />,
    )

    act(() => {
      view?.focus()
    })

    rerender(
      <CodeEditor
        defaultValue="import math\nimport statistics as websocket_sync_probe"
        onChange={onChange}
        onEditorView={(editorView) => { view = editorView }}
      />,
    )

    await waitFor(() => {
      expect(getEditorText(container)).toContain("websocket_sync_probe")
    })
    expect(onChange).not.toHaveBeenCalled()
  })

  it("does not overwrite focused local edits with an external refresh", async () => {
    let view: EditorView | null = null
    const onChange = vi.fn()
    const { container, rerender } = render(
      <CodeEditor
        defaultValue="import math"
        onChange={onChange}
        onEditorView={(editorView) => { view = editorView }}
      />,
    )

    act(() => {
      view?.focus()
      view?.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: "import local_edit" },
      })
    })

    rerender(
      <CodeEditor
        defaultValue="import statistics as websocket_sync_probe"
        onChange={onChange}
        onEditorView={(editorView) => { view = editorView }}
      />,
    )

    await waitFor(() => {
      expect(getEditorText(container)).toContain("import local_edit")
    })
    expect(getEditorText(container)).not.toContain("websocket_sync_probe")
  })

  it("mounts the CodeMirror editor DOM structure", () => {
    const { container } = render(
      <CodeEditor defaultValue="x = 1" onChange={vi.fn()} />,
    )
    expect(container.querySelector(".cm-editor")).toBeTruthy()
    expect(container.querySelector(".cm-scroller")).toBeTruthy()
    expect(container.querySelector(".cm-content")).toBeTruthy()
    expect(container.querySelector(".cm-gutters")).toBeTruthy()
  })

  it("cleans up editor on unmount", () => {
    const { container, unmount } = render(
      <CodeEditor defaultValue="test" onChange={vi.fn()} />,
    )
    expect(container.querySelector(".cm-editor")).toBeTruthy()
    unmount()
    expect(container.querySelector(".cm-editor")).toBeNull()
  })

  it("renders multiline content with line numbers in gutter", () => {
    const code = "line1\nline2\nline3\nline4\nline5"
    const { container } = render(
      <CodeEditor defaultValue={code} onChange={vi.fn()} />,
    )
    const gutterElements = container.querySelectorAll(".cm-lineNumbers .cm-gutterElement")
    expect(gutterElements.length).toBeGreaterThan(0)
    // The gutter should contain the text "5" (line number for the 5th line)
    const allText = Array.from(gutterElements).map((el) => el.textContent?.trim())
    expect(allText).toContain("1")
    expect(allText).toContain("5")
  })

  it("renders lint gutter", () => {
    const { container } = render(
      <CodeEditor defaultValue="x = 1" onChange={vi.fn()} />,
    )
    expect(container.querySelector(".cm-gutter-lint")).toBeTruthy()
  })

  it("does not crash when errorLine exceeds document lines", () => {
    const { container } = render(
      <CodeEditor defaultValue="x = 1" onChange={vi.fn()} errorLine={999} />,
    )
    // Should render without throwing — error is clamped to last line
    expect(container.querySelector(".cm-editor")).toBeTruthy()
  })

  it("does not show lint markers when errorLine is null", () => {
    const { container } = render(
      <CodeEditor defaultValue="x = 1" onChange={vi.fn()} errorLine={null} />,
    )
    expect(container.querySelector(".cm-lint-marker-error")).toBeNull()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// FileBrowser
// ═══════════════════════════════════════════════════════════════════════════

describe("FileBrowser", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetFileListCache.mockReturnValue(null)
  })

  afterEach(cleanup)

  it("shows loading spinner initially then renders file list", async () => {
    mockListFiles.mockResolvedValue({
      items: [
        { name: "data.csv", path: "data.csv", type: "file", size: 2048 },
        { name: "subdir", path: "subdir", type: "directory" },
      ],
    })
    render(<FileBrowser onSelect={vi.fn()} />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText("data.csv")).toBeInTheDocument()
    })
    expect(screen.getByText("subdir")).toBeInTheDocument()
  })

  it("uses cache on second load", async () => {
    const cached = [
      { name: "cached.csv", path: "cached.csv", type: "file" as const },
    ]
    mockGetFileListCache.mockReturnValue(cached)
    render(<FileBrowser onSelect={vi.fn()} />)
    // Should use cache, not call listFiles
    expect(mockListFiles).not.toHaveBeenCalled()
    expect(screen.getByText("cached.csv")).toBeInTheDocument()
  })

  it("clicking a directory navigates into it", async () => {
    mockListFiles.mockResolvedValueOnce({
      items: [
        { name: "subdir", path: "subdir", type: "directory" },
      ],
    })
    mockListFiles.mockResolvedValueOnce({
      items: [
        { name: "nested.csv", path: "subdir/nested.csv", type: "file" },
      ],
    })
    render(<FileBrowser onSelect={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText("subdir")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText("subdir"))
    await waitFor(() => {
      expect(screen.getByText("nested.csv")).toBeInTheDocument()
    })
  })

  it("shows error state when API call fails", async () => {
    mockListFiles.mockRejectedValue(new Error("Network error"))
    render(<FileBrowser onSelect={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeInTheDocument()
    })
  })

  it("shows empty directory message", async () => {
    mockListFiles.mockResolvedValue({ items: [] })
    render(<FileBrowser onSelect={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText("No matching files")).toBeInTheDocument()
    })
  })

  it("clicking a file calls onSelect with its path", async () => {
    const onSelect = vi.fn()
    mockListFiles.mockResolvedValue({
      items: [
        { name: "data.csv", path: "data/data.csv", type: "file" },
      ],
    })
    render(<FileBrowser onSelect={onSelect} />)
    await waitFor(() => {
      expect(screen.getByText("data.csv")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText("data.csv"))
    expect(onSelect).toHaveBeenCalledWith("data/data.csv")
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// SchemaPreview
// ═══════════════════════════════════════════════════════════════════════════

describe("SchemaPreview", () => {
  afterEach(cleanup)

  const sampleSchema: SchemaInfo = {
    path: "data.csv",
    columns: [
      { name: "id", dtype: "Int64" },
      { name: "name", dtype: "Utf8" },
    ],
    row_count: 100,
    column_count: 2,
    preview: [
      { id: 1, name: "Alice" },
      { id: 2, name: "Bob" },
    ],
  }

  it("renders column names and types from schema", () => {
    render(<SchemaPreview schema={sampleSchema} />)
    expect(screen.getByText("id")).toBeInTheDocument()
    expect(screen.getByText("name")).toBeInTheDocument()
    expect(screen.getByText("Int64")).toBeInTheDocument()
    expect(screen.getByText("Utf8")).toBeInTheDocument()
  })

  it("shows row and column counts", () => {
    render(<SchemaPreview schema={sampleSchema} />)
    expect(screen.getByText("2 cols / 100 rows")).toBeInTheDocument()
  })

  it("toggles preview table on button click", () => {
    render(<SchemaPreview schema={sampleSchema} />)
    expect(screen.getByText("Show preview")).toBeInTheDocument()
    expect(screen.queryByText("Alice")).not.toBeInTheDocument()
    fireEvent.click(screen.getByText("Show preview"))
    expect(screen.getByText("Hide preview")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Bob")).toBeInTheDocument()
  })

  it("renders nothing when schema is null", () => {
    const { container } = render(<SchemaPreview schema={null} />)
    expect(container.innerHTML).toBe("")
  })
})
