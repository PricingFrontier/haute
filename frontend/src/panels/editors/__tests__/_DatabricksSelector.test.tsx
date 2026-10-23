import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act, waitFor, within } from "@testing-library/react"
import { WarehousePicker, CatalogTablePicker, TargetTablePicker } from "../_DatabricksSelector"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/client", () => {
  class ApiError extends Error {
    detail?: string
    status: number
    constructor(message: string, status: number, detail?: string) {
      super(message)
      this.name = "ApiError"
      this.status = status
      this.detail = detail
    }
  }
  return {
    getWarehouses: vi.fn(),
    getCatalogs: vi.fn(),
    getSchemas: vi.fn(),
    getTables: vi.fn(),
    ApiError,
  }
})

import {
  getWarehouses,
  getCatalogs,
  getSchemas,
  getTables,
  ApiError,
} from "../../../api/client"

const mockGetWarehouses = getWarehouses as ReturnType<typeof vi.fn>
const mockGetCatalogs = getCatalogs as ReturnType<typeof vi.fn>
const mockGetSchemas = getSchemas as ReturnType<typeof vi.fn>
const mockGetTables = getTables as ReturnType<typeof vi.fn>

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ApiErrorCtor = ApiError as any
function makeApiError(message: string, status: number, detail?: string): Error {
  return new ApiErrorCtor(message, status, detail)
}

// ═══════════════════════════════════════════════════════════════════════════
// WarehousePicker
// ═══════════════════════════════════════════════════════════════════════════

describe("WarehousePicker", () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(cleanup)

  const warehouses = [
    { id: "w1", name: "Starter Warehouse", http_path: "/sql/1.0/warehouses/abc", state: "RUNNING", size: "Small", serverless: true },
    { id: "w2", name: "Prod Warehouse", http_path: "/sql/1.0/warehouses/def", state: "STOPPED", size: "Large", serverless: false },
    { id: "w3", name: "Dev Warehouse", http_path: "/sql/1.0/warehouses/ghi", state: "STARTING", size: "", serverless: false },
  ]
  const clusterPath = "/sql/protocolv1/o/123/0101-abc"

  const openList = () =>
    fireEvent.click(screen.getByRole("button", { expanded: false, name: /warehouse|\/sql/i }))

  it("prompts for a warehouse instead of showing a path-like placeholder", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    expect(await screen.findByText("Select SQL warehouse...")).toBeInTheDocument()
    expect(screen.getByText("Required. Haute runs the read on this warehouse.")).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/warehouses\/abc123/)).not.toBeInTheDocument()
  })

  it("fetches warehouses on mount and lists them when opened", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    openList()

    const list = screen.getByRole("listbox", { name: "SQL warehouses" })
    expect(within(list).getAllByRole("option").map((option) => option.textContent)).toEqual([
      expect.stringContaining("Starter Warehouse"),
      expect.stringContaining("Prod Warehouse"),
      expect.stringContaining("Dev Warehouse"),
    ])
    expect(mockGetWarehouses).toHaveBeenCalledTimes(1)
  })

  it("tags serverless warehouses", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    openList()

    expect(within(screen.getByRole("option", { name: /Starter Warehouse/ })).getByText("Serverless")).toBeInTheDocument()
    expect(within(screen.getByRole("option", { name: /Prod Warehouse/ })).queryByText("Serverless")).not.toBeInTheDocument()
  })

  it("selects a warehouse's http_path and closes the list", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath="" onSelect={onSelect} />)

    await screen.findByText("Select SQL warehouse...")
    openList()
    fireEvent.click(screen.getByRole("option", { name: /Prod Warehouse/ }))

    expect(onSelect).toHaveBeenCalledWith("/sql/1.0/warehouses/def")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })

  it("shows the saved warehouse by name and marks it selected", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="/sql/1.0/warehouses/abc" onSelect={vi.fn()} />)

    const trigger = await screen.findByRole("button", { name: /Starter Warehouse/ })
    fireEvent.click(trigger)

    expect(screen.getByRole("option", { name: /Starter Warehouse/ })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("option", { name: /Prod Warehouse/ })).toHaveAttribute("aria-selected", "false")
  })

  it("auto-selects the only warehouse when none is set", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses: [warehouses[0]] })
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath="" onSelect={onSelect} />)

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("/sql/1.0/warehouses/abc"))
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it("does not auto-select when there are several warehouses or one is already set", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath="" onSelect={onSelect} />)
    await screen.findByText("Select SQL warehouse...")
    cleanup()

    mockGetWarehouses.mockResolvedValue({ warehouses: [warehouses[0]] })
    render(<WarehousePicker httpPath="/sql/1.0/warehouses/def" onSelect={onSelect} />)
    await waitFor(() => expect(mockGetWarehouses).toHaveBeenCalledTimes(2))
    await screen.findByText(/not in the workspace's list/)

    expect(onSelect).not.toHaveBeenCalled()
  })

  it("warns when the saved warehouse is not in the workspace's list", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="/sql/1.0/warehouses/gone" onSelect={vi.fn()} />)

    expect(await screen.findByText(/not in the workspace's list/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /\/sql\/1.0\/warehouses\/gone/ })).toBeInTheDocument()
  })

  it("opens a cluster path in manual mode without fetching warehouses", () => {
    render(<WarehousePicker httpPath={clusterPath} onSelect={vi.fn()} />)

    expect(screen.getByLabelText("HTTP path")).toHaveValue(clusterPath)
    expect(mockGetWarehouses).not.toHaveBeenCalled()
  })

  it("commits a typed path once on blur, not per keystroke (undo-atomicity)", () => {
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath={clusterPath} onSelect={onSelect} />)
    const input = screen.getByLabelText("HTTP path")
    fireEvent.change(input, { target: { value: " /sql/custom/path " } })
    // Typing buffers locally — nothing committed yet.
    expect(onSelect).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith("/sql/custom/path")
  })

  it("switches to manual entry and back without re-fetching", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    openList()
    fireEvent.click(screen.getByRole("button", { name: "Enter HTTP path manually..." }))
    expect(screen.getByLabelText("HTTP path")).toHaveAttribute("placeholder", "Paste an HTTP path")

    fireEvent.click(screen.getByRole("button", { name: "Choose from warehouse list" }))
    expect(screen.getByText("Select SQL warehouse...")).toBeInTheDocument()
    expect(mockGetWarehouses).toHaveBeenCalledTimes(1)
  })

  it("fetches the list when leaving manual mode for the first time", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath={clusterPath} onSelect={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Choose from warehouse list" }))
    await waitFor(() => expect(mockGetWarehouses).toHaveBeenCalledTimes(1))
  })

  it("refreshes the list on demand", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    fireEvent.click(screen.getByRole("button", { name: "Refresh warehouses" }))
    await waitFor(() => expect(mockGetWarehouses).toHaveBeenCalledTimes(2))
  })

  it("closes the list on Escape and outside click", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    openList()
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" })
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()

    openList()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
  })

  it("displays state indicator colors: green for RUNNING, red for STOPPED, amber for other", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    const { container } = render(
      <WarehousePicker httpPath="" onSelect={vi.fn()} />,
    )

    await screen.findByText("Select SQL warehouse...")
    openList()

    const dots = container.querySelectorAll<HTMLSpanElement>('[title]')
    const runningDot = Array.from(dots).find(d => d.getAttribute("title") === "RUNNING")
    const stoppedDot = Array.from(dots).find(d => d.getAttribute("title") === "STOPPED")
    const startingDot = Array.from(dots).find(d => d.getAttribute("title") === "STARTING")

    // Each status should have a distinct color indicator
    expect(runningDot?.style.background).toBeTruthy()
    expect(stoppedDot?.style.background).toBeTruthy()
    expect(startingDot?.style.background).toBeTruthy()
    // All three states must be visually distinguishable from each other
    expect(runningDot?.style.background).not.toBe(stoppedDot?.style.background)
    expect(runningDot?.style.background).not.toBe(startingDot?.style.background)
    expect(stoppedDot?.style.background).not.toBe(startingDot?.style.background)
  })

  it("shows size label when warehouse has a size", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    openList()
    expect(screen.getByText("Small")).toBeInTheDocument()
    expect(screen.getByText("Large")).toBeInTheDocument()
  })

  it("shows error when API call fails with ApiError", async () => {
    mockGetWarehouses.mockRejectedValue(makeApiError("HTTP 500", 500, "Server exploded"))
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    expect(await screen.findByText("Server exploded")).toBeInTheDocument()
  })

  it("shows error.message for generic Error (not ApiError) and still offers manual entry", async () => {
    mockGetWarehouses.mockRejectedValue(new Error("Network failure"))
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    expect(await screen.findByText("Network failure")).toBeInTheDocument()
    openList()
    expect(screen.getByRole("button", { name: "Enter HTTP path manually..." })).toBeInTheDocument()
  })

  it("shows 'No SQL Warehouses found' when API returns empty list", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses: [] })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    await screen.findByText("Select SQL warehouse...")
    openList()
    expect(screen.getByText("No SQL Warehouses found in this workspace")).toBeInTheDocument()
  })

  it("shows loading while the list is fetched", async () => {
    let resolve: (v: unknown) => void
    mockGetWarehouses.mockReturnValue(new Promise(r => { resolve = r }))
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    expect(screen.getByText("Loading warehouses...")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Refresh warehouses" })).toBeDisabled()

    await act(async () => { resolve!({ warehouses: [] }) })
    expect(screen.getByText("Select SQL warehouse...")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Refresh warehouses" })).not.toBeDisabled()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// CatalogTablePicker
// ═══════════════════════════════════════════════════════════════════════════

describe("CatalogTablePicker", () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(cleanup)

  const catalogItems = [
    { name: "main", comment: "Main catalog" },
    { name: "staging", comment: "" },
  ]
  const schemaItems = [
    { name: "default", comment: "Default schema" },
    { name: "analytics", comment: "" },
  ]
  const tableItems = [
    { name: "users", full_name: "main.default.users", table_type: "TABLE", comment: "User data" },
    { name: "events", full_name: "main.default.events", table_type: "VIEW", comment: "" },
  ]

  function getCatalogSelect() {
    return screen.getAllByRole("combobox")[0] as HTMLSelectElement
  }
  function getSchemaSelect() {
    return screen.getAllByRole("combobox")[1] as HTMLSelectElement
  }
  function getTableSelect() {
    return screen.getAllByRole("combobox")[2] as HTMLSelectElement
  }
  async function waitForCatalogOption(value: string) {
    await waitFor(() => {
      expect(
        Array.from(getCatalogSelect().options).some(option => option.value === value),
      ).toBe(true)
    })
  }
  async function waitForSchemaOption(value: string) {
    await waitFor(() => {
      expect(
        Array.from(getSchemaSelect().options).some(option => option.value === value),
      ).toBe(true)
    })
  }
  async function waitForTableOption(value: string) {
    await waitFor(() => {
      expect(
        Array.from(getTableSelect().options).some(option => option.value === value),
      ).toBe(true)
    })
  }

  it("renders three dropdowns with correct default placeholders", () => {
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)
    const selects = screen.getAllByRole("combobox")
    expect(selects).toHaveLength(3)
    expect(screen.getByText("Select catalog...")).toBeInTheDocument()
    expect(screen.getByText("Select catalog first")).toBeInTheDocument()
    expect(screen.getByText("Select schema first")).toBeInTheDocument()
  })

  it("schema select is disabled when no catalog is selected", () => {
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)
    expect(getSchemaSelect()).toBeDisabled()
  })

  it("table select is disabled when no schema is selected", () => {
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)
    expect(getTableSelect()).toBeDisabled()
  })

  it("fetches catalogs on catalog select focus", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)

    fireEvent.focus(getCatalogSelect())

    await waitFor(() => {
      expect(mockGetCatalogs).toHaveBeenCalledTimes(1)
    })
  })

  it("shows Loading... placeholder during catalog fetch", async () => {
    let resolve: (v: unknown) => void
    mockGetCatalogs.mockReturnValue(new Promise(r => { resolve = r }))
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)

    fireEvent.focus(getCatalogSelect())

    expect(screen.getByText("Loading...")).toBeInTheDocument()

    await act(async () => { resolve!({ catalogs: catalogItems }) })
  })

  it("selecting a catalog fetches schemas and clears schema/table", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockResolvedValue({ schemas: schemaItems })
    const onSelect = vi.fn()

    render(<CatalogTablePicker table="" onSelect={onSelect} />)

    // Focus to load catalogs
    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")

    // Select a catalog
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })

    await waitFor(() => {
      expect(mockGetSchemas).toHaveBeenCalledWith("main")
    })
    // onSelect("") called on catalog change to clear the full table name
    expect(onSelect).toHaveBeenCalledWith("")
  })

  it("selecting a new catalog resets schema, table, schemas list, and tables list", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockResolvedValue({ schemas: schemaItems })
    mockGetTables.mockResolvedValue({ tables: tableItems })
    const onSelect = vi.fn()

    render(<CatalogTablePicker table="" onSelect={onSelect} />)

    // Load catalogs and select one
    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalledWith("main"))
    await waitForSchemaOption("default")

    // Select schema to populate tables
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalledWith("main", "default"))
    await waitForTableOption("users")

    // Select table
    fireEvent.change(getTableSelect(), { target: { value: "users" } })
    expect(onSelect).toHaveBeenCalledWith("main.default.users")

    // Now change catalog -- schema and table selects should reset
    mockGetSchemas.mockResolvedValue({ schemas: [{ name: "other", comment: "" }] })
    fireEvent.change(getCatalogSelect(), { target: { value: "staging" } })

    // Schema and table selects should show empty values
    expect(getSchemaSelect().value).toBe("")
    expect(getTableSelect().value).toBe("")
  })

  it("selecting a new schema clears table and tables list", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockResolvedValue({ schemas: schemaItems })
    mockGetTables.mockResolvedValue({ tables: tableItems })
    const onSelect = vi.fn()

    render(<CatalogTablePicker table="" onSelect={onSelect} />)

    // Select catalog -> schema -> table
    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalled())
    await waitForSchemaOption("default")
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalled())
    await waitForTableOption("users")
    fireEvent.change(getTableSelect(), { target: { value: "users" } })
    expect(onSelect).toHaveBeenCalledWith("main.default.users")

    // Change schema: table select should reset
    mockGetTables.mockResolvedValue({ tables: [{ name: "orders", full_name: "main.analytics.orders", table_type: "TABLE", comment: "" }] })
    await waitForSchemaOption("analytics")
    fireEvent.change(getSchemaSelect(), { target: { value: "analytics" } })

    expect(getTableSelect().value).toBe("")
    expect(onSelect).toHaveBeenCalledWith("") // called again with empty
  })

  it("selecting a table calls onSelect with catalog.schema.table", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockResolvedValue({ schemas: schemaItems })
    mockGetTables.mockResolvedValue({ tables: tableItems })
    const onSelect = vi.fn()

    render(<CatalogTablePicker table="" onSelect={onSelect} />)

    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalled())
    await waitForSchemaOption("default")
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalled())
    await waitForTableOption("events")
    fireEvent.change(getTableSelect(), { target: { value: "events" } })

    expect(onSelect).toHaveBeenCalledWith("main.default.events")
  })

  it("selecting empty table value calls onSelect with empty string", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockResolvedValue({ schemas: schemaItems })
    mockGetTables.mockResolvedValue({ tables: tableItems })
    const onSelect = vi.fn()

    render(<CatalogTablePicker table="" onSelect={onSelect} />)

    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalled())
    await waitForSchemaOption("default")
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalled())
    await waitForTableOption("users")

    // Select then deselect table
    fireEvent.change(getTableSelect(), { target: { value: "users" } })
    fireEvent.change(getTableSelect(), { target: { value: "" } })

    expect(onSelect).toHaveBeenLastCalledWith("")
  })

  it("shows retained option when current value is not in loaded catalog list", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: [{ name: "other", comment: "" }] })
    render(<CatalogTablePicker table="unlisted.public.accounts" onSelect={vi.fn()} />)

    // The catalog "unlisted" is initialized from props but not in the API response
    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())

    // The retained option should appear
    const catalogSelect = getCatalogSelect()
    const options = Array.from(catalogSelect.querySelectorAll("option"))
    const optionValues = options.map(o => o.value)
    expect(optionValues).toContain("unlisted")
    expect(optionValues).toContain("other")
  })

  it("shows the full table name below selects when table prop is set", () => {
    render(<CatalogTablePicker table="main.default.users" onSelect={vi.fn()} />)
    expect(screen.getByText("main.default.users")).toBeInTheDocument()
  })

  it("shows error when catalog fetch fails with ApiError", async () => {
    mockGetCatalogs.mockRejectedValue(makeApiError("HTTP 403", 403, "Access denied"))
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)

    fireEvent.focus(getCatalogSelect())

    await waitFor(() => {
      expect(screen.getByText("Access denied")).toBeInTheDocument()
    })
  })

  it("shows error.message for generic Error on schema fetch", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockRejectedValue(new Error("Timeout"))
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)

    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })

    await waitFor(() => {
      expect(screen.getByText("Timeout")).toBeInTheDocument()
    })
  })

  it("fetches schemas on schema select focus when catalog is set", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: catalogItems })
    mockGetSchemas.mockResolvedValue({ schemas: schemaItems })
    render(<CatalogTablePicker table="" onSelect={vi.fn()} />)

    // Select a catalog first
    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())
    await waitForCatalogOption("main")
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalledTimes(1))

    // Focus schema select again to re-fetch
    mockGetSchemas.mockClear()
    fireEvent.focus(getSchemaSelect())
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalledWith("main"))
  })

  it("fetches tables on table select focus when catalog and schema are set", async () => {
    // Start with catalog and schema already set via the table prop
    mockGetTables.mockResolvedValue({ tables: tableItems })
    render(<CatalogTablePicker table="main.default." onSelect={vi.fn()} />)

    // With catalog="main" and schema="default" pre-set from the table prop,
    // focusing the table select should trigger refreshTables
    fireEvent.focus(getTableSelect())
    await waitFor(() => expect(mockGetTables).toHaveBeenCalledWith("main", "default"))
  })
})

// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// TargetTablePicker
// ═══════════════════════════════════════════════════════════════════════════

describe("TargetTablePicker", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetCatalogs.mockResolvedValue({ catalogs: [{ name: "workspace", comment: "" }] })
    mockGetSchemas.mockResolvedValue({ schemas: [{ name: "haute-data", comment: "" }, { name: "other", comment: "" }] })
    mockGetTables.mockResolvedValue({
      tables: [{ name: "claims", full_name: "workspace.haute-data.claims", table_type: "MANAGED", comment: "" }],
    })
  })
  afterEach(cleanup)

  const chooseSchema = async () => {
    fireEvent.focus(screen.getByLabelText("Catalog"))
    await screen.findByRole("option", { name: "workspace" })
    fireEvent.change(screen.getByLabelText("Catalog"), { target: { value: "workspace" } })
    await screen.findByRole("option", { name: "haute-data" })
    fireEvent.change(screen.getByLabelText("Schema"), { target: { value: "haute-data" } })
  }

  it("names a new table once catalog, schema and name are set", async () => {
    const onSelect = vi.fn()
    const { rerender } = render(<TargetTablePicker table="" onSelect={onSelect} />)

    expect(screen.getByLabelText("Table name")).toBeDisabled()
    await chooseSchema()
    const name = screen.getByLabelText("Table name")
    expect(name).toBeEnabled()
    expect(name).toHaveAttribute("placeholder", "New or existing table name")
    fireEvent.change(name, { target: { value: "scored" } })
    fireEvent.blur(name)

    expect(onSelect).toHaveBeenLastCalledWith("workspace.haute-data.scored")
    rerender(<TargetTablePicker table="workspace.haute-data.scored" onSelect={onSelect} />)
    expect(await screen.findByText(/new table, created on first write/)).toBeInTheDocument()
  })

  it("recognises an existing table and suggests the schema's tables", async () => {
    const { container } = render(
      <TargetTablePicker table="workspace.haute-data.claims" onSelect={vi.fn()} />,
    )

    expect(await screen.findByText(/existing table/)).toBeInTheDocument()
    expect(mockGetTables).toHaveBeenCalledWith("workspace", "haute-data")
    const suggestions = container.querySelectorAll("datalist option")
    expect(Array.from(suggestions).map((option) => option.getAttribute("value"))).toEqual(["claims"])
  })

  it("refuses a name Databricks cannot take without quoting", async () => {
    const onSelect = vi.fn()
    render(<TargetTablePicker table="" onSelect={onSelect} />)
    await chooseSchema()
    onSelect.mockClear()

    const name = screen.getByLabelText("Table name")
    fireEvent.change(name, { target: { value: "scored policies" } })
    fireEvent.blur(name)

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Use only letters, digits, underscores and hyphens.",
    )
    expect(onSelect).not.toHaveBeenCalled()
  })

  it("keeps the name when the schema changes", async () => {
    const onSelect = vi.fn()
    render(<TargetTablePicker table="workspace.haute-data.scored" onSelect={onSelect} />)

    fireEvent.focus(screen.getByLabelText("Schema"))
    await screen.findByRole("option", { name: "other" })
    fireEvent.change(screen.getByLabelText("Schema"), { target: { value: "other" } })

    expect(onSelect).toHaveBeenLastCalledWith("workspace.other.scored")
  })

  it("clears the table when the catalog changes", async () => {
    const onSelect = vi.fn()
    render(<TargetTablePicker table="workspace.haute-data.scored" onSelect={onSelect} />)

    fireEvent.change(screen.getByLabelText("Catalog"), { target: { value: "" } })

    expect(onSelect).toHaveBeenLastCalledWith("")
  })
})
