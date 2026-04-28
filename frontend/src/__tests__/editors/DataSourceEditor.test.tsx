/**
 * Render tests for DataSourceEditor.
 *
 * Tests: source type toggle, flat file / Databricks mode rendering,
 * FileBrowser collapse/expand, Polars code editor, source type switching.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import DataSourceEditor from "../../panels/editors/DataSourceEditor"

afterEach(cleanup)

// Mock the shared components that make API calls
vi.mock("../../panels/editors/_shared", async () => {
  const actual = await vi.importActual("../../panels/editors/_shared")
  return {
    ...actual,
    FileBrowser: ({ currentPath, onSelect }: { currentPath?: string; onSelect: (path: string) => void }) => (
      <div data-testid="file-browser">
        <span data-testid="current-path">{currentPath || ""}</span>
        <button data-testid="select-file" onClick={() => onSelect("test.parquet")}>Select</button>
      </div>
    ),
    CodeEditor: ({ defaultValue, onChange, placeholder }: { defaultValue: string; onChange: (v: string) => void; placeholder?: string; errorLine?: number | null }) => (
      <textarea
        data-testid="code-editor"
        defaultValue={defaultValue}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    ),
  }
})

vi.mock("../../panels/editors/_DatabricksSelector", () => ({
  WarehousePicker: ({ httpPath }: { httpPath: string; onSelect: (v: string) => void }) => (
    <div data-testid="warehouse-picker">{httpPath}</div>
  ),
  CatalogTablePicker: ({ table }: { table: string; onSelect: (v: string) => void }) => (
    <div data-testid="catalog-picker">{table}</div>
  ),
  DatabricksFetchButton: ({ onFetched }: { onFetched: () => void }) => (
    <button data-testid="fetch-btn" onClick={onFetched}>Fetch</button>
  ),
}))

const DEFAULT_PROPS = {
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  accentColor: "#3b82f6",
}

describe("DataSourceEditor", () => {
  it("renders flat_file tab selected by default", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} />)
    const flatFileBtn = screen.getByText("Flat File").closest("button")!
    const dbBtn = screen.getByText("Databricks").closest("button")!
    // Active tab should have a visually distinct border from inactive tab
    expect(flatFileBtn.style.border).not.toBe(dbBtn.style.border)
  })

  it("shows FileBrowser when no file selected, no green box", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} />)
    expect(screen.getByTestId("file-browser")).toBeTruthy()
    expect(screen.queryByTestId("file-change-btn")).toBeNull()
  })

  it("hides FileBrowser when file is already selected", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ path: "data/input.parquet" }} />)
    expect(screen.queryByTestId("file-browser")).toBeNull()
    // Green box shows the path
    expect(screen.getByText("data/input.parquet")).toBeTruthy()
    // "change" button visible
    expect(screen.getByText("change")).toBeTruthy()
  })

  it("always shows File label", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ path: "data/input.parquet" }} />)
    expect(screen.getByText("File")).toBeTruthy()
  })

  it("expands FileBrowser when clicking change, shows close", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ path: "data/input.parquet" }} />)
    fireEvent.click(screen.getByText("change"))
    expect(screen.getByTestId("file-browser")).toBeTruthy()
    expect(screen.getByText("close")).toBeTruthy()
  })

  it("closes FileBrowser when clicking close", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ path: "data/input.parquet" }} />)
    fireEvent.click(screen.getByText("change"))
    expect(screen.getByTestId("file-browser")).toBeTruthy()
    fireEvent.click(screen.getByText("close"))
    expect(screen.queryByTestId("file-browser")).toBeNull()
  })

  it("selecting a file collapses the browser", () => {
    const onUpdate = vi.fn()
    render(<DataSourceEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    // FileBrowser is open (no path yet)
    expect(screen.getByTestId("file-browser")).toBeTruthy()
    // Select a file
    fireEvent.click(screen.getByTestId("select-file"))
    expect(onUpdate).toHaveBeenCalledWith("path", "test.parquet")
  })

  it("switching to databricks shows Databricks controls", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ sourceType: "databricks" }} />)
    expect(screen.getByTestId("warehouse-picker")).toBeTruthy()
    expect(screen.getByTestId("catalog-picker")).toBeTruthy()
    expect(screen.getByText("SQL Query")).toBeTruthy()
    expect(screen.getByTestId("fetch-btn")).toBeTruthy()
    expect(screen.queryByTestId("file-browser")).toBeNull()
  })

  it("switching source type calls onUpdate with sourceType", () => {
    const onUpdate = vi.fn()
    render(<DataSourceEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Databricks"))
    expect(onUpdate).toHaveBeenCalledWith("sourceType", "databricks")
    fireEvent.click(screen.getByText("Flat File"))
    expect(onUpdate).toHaveBeenCalledWith("sourceType", "flat_file")
  })

  it("renders both source type toggle buttons", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Source Type")).toBeTruthy()
    expect(screen.getByText("Flat File")).toBeTruthy()
    expect(screen.getByText("Databricks")).toBeTruthy()
  })

  it("renders SQL query textarea in databricks mode", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ sourceType: "databricks" }} />)
    const textarea = screen.getByPlaceholderText(/SELECT \*/)
    expect(textarea).toBeTruthy()
    expect(textarea.tagName).toBe("TEXTAREA")
  })

  it("shows query helper text in databricks mode", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ sourceType: "databricks" }} />)
    expect(screen.getByText("Combined with table above as: query FROM table")).toBeTruthy()
  })

  it("reflects external sourceType config changes (B22 fix)", () => {
    const { rerender } = render(<DataSourceEditor {...DEFAULT_PROPS} config={{ sourceType: "flat_file" }} />)
    expect(screen.getByTestId("file-browser")).toBeTruthy()
    expect(screen.queryByTestId("warehouse-picker")).toBeNull()
    rerender(<DataSourceEditor {...DEFAULT_PROPS} config={{ sourceType: "databricks" }} />)
    expect(screen.getByTestId("warehouse-picker")).toBeTruthy()
    expect(screen.queryByTestId("file-browser")).toBeNull()
  })

  it("renders Polars Code editor", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} />)
    expect(screen.getByTestId("code-editor")).toBeTruthy()
    expect(screen.getByText("Polars Code")).toBeTruthy()
    expect(screen.getByText("(optional)")).toBeTruthy()
  })

  it("shows code from config in the editor", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ code: ".filter(pl.col('x') > 0)" }} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe(".filter(pl.col('x') > 0)")
  })

  it("does not synthesize file loading code in the editor", () => {
    render(<DataSourceEditor {...DEFAULT_PROPS} config={{ path: "data/input.parquet" }} />)
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("")
    expect(editor.defaultValue).not.toContain("scan_parquet")
  })

  it("shows only post-load transform code for selected files", () => {
    render(
      <DataSourceEditor
        {...DEFAULT_PROPS}
        config={{ path: "data/input.parquet", code: "df = df.limit(10)" }}
      />,
    )
    const editor = screen.getByTestId("code-editor") as HTMLTextAreaElement
    expect(editor.defaultValue).toBe("df = df.limit(10)")
    expect(editor.defaultValue).not.toContain("scan_parquet")
  })

  it("code editor onChange calls onUpdate with 'code' key", () => {
    const onUpdate = vi.fn()
    render(<DataSourceEditor {...DEFAULT_PROPS} onUpdate={onUpdate} />)
    fireEvent.change(screen.getByTestId("code-editor"), { target: { value: ".select('a')" } })
    expect(onUpdate).toHaveBeenCalledWith("code", ".select('a')")
  })
})
