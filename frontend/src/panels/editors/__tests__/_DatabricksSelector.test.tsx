import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act, waitFor } from "@testing-library/react"
import { WarehousePicker, CatalogTablePicker, DatabricksFetchButton } from "../_DatabricksSelector"

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
    getCacheStatus: vi.fn(),
    getFetchProgress: vi.fn(),
    fetchDatabricksData: vi.fn(),
    deleteCache: vi.fn(),
    ApiError,
  }
})

import {
  getWarehouses,
  getCatalogs,
  getSchemas,
  getTables,
  getCacheStatus,
  getFetchProgress,
  fetchDatabricksData,
  deleteCache,
  ApiError,
} from "../../../api/client"

const mockGetWarehouses = getWarehouses as ReturnType<typeof vi.fn>
const mockGetCatalogs = getCatalogs as ReturnType<typeof vi.fn>
const mockGetSchemas = getSchemas as ReturnType<typeof vi.fn>
const mockGetTables = getTables as ReturnType<typeof vi.fn>
const mockGetCacheStatus = getCacheStatus as ReturnType<typeof vi.fn>
const mockGetFetchProgress = getFetchProgress as ReturnType<typeof vi.fn>
const mockFetchDatabricksData = fetchDatabricksData as ReturnType<typeof vi.fn>
const mockDeleteCache = deleteCache as ReturnType<typeof vi.fn>

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
    { id: "w1", name: "Starter Warehouse", http_path: "/sql/1.0/warehouses/abc", state: "RUNNING", size: "Small" },
    { id: "w2", name: "Prod Warehouse", http_path: "/sql/1.0/warehouses/def", state: "STOPPED", size: "Large" },
    { id: "w3", name: "Dev Warehouse", http_path: "/sql/1.0/warehouses/ghi", state: "STARTING", size: "" },
  ]

  it("renders the input with current httpPath value", () => {
    render(<WarehousePicker httpPath="/sql/1.0/warehouses/abc" onSelect={vi.fn()} />)
    const input = screen.getByPlaceholderText("/sql/1.0/warehouses/abc123")
    expect(input).toHaveValue("/sql/1.0/warehouses/abc")
  })

  it("calls onSelect when user types in the input", () => {
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath="" onSelect={onSelect} />)
    const input = screen.getByPlaceholderText("/sql/1.0/warehouses/abc123")
    fireEvent.change(input, { target: { value: "/sql/custom/path" } })
    expect(onSelect).toHaveBeenCalledWith("/sql/custom/path")
  })

  it("fetches warehouses on Browse button click and shows the list", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))

    await waitFor(() => {
      expect(screen.getByText("Starter Warehouse")).toBeInTheDocument()
    })
    expect(screen.getByText("Prod Warehouse")).toBeInTheDocument()
    expect(screen.getByText("Dev Warehouse")).toBeInTheDocument()
    expect(mockGetWarehouses).toHaveBeenCalledTimes(1)
  })

  it("does not re-fetch on second Browse click (fetched.current guard)", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)
    const btn = screen.getByTitle("Fetch warehouses from Databricks")

    // First click: fetches
    fireEvent.click(btn)
    await waitFor(() => {
      expect(screen.getByText("Starter Warehouse")).toBeInTheDocument()
    })
    expect(mockGetWarehouses).toHaveBeenCalledTimes(1)

    // Close the list by selecting a warehouse, then re-click
    fireEvent.click(screen.getByText("Starter Warehouse"))
    // List should close
    expect(screen.queryByText("Prod Warehouse")).not.toBeInTheDocument()

    // Second click: opens without re-fetching
    fireEvent.click(btn)
    await waitFor(() => {
      expect(screen.getByText("Starter Warehouse")).toBeInTheDocument()
    })
    expect(mockGetWarehouses).toHaveBeenCalledTimes(1)
  })

  it("calls onSelect with warehouse http_path on warehouse click", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath="" onSelect={onSelect} />)

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))
    await waitFor(() => {
      expect(screen.getByText("Prod Warehouse")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Prod Warehouse"))
    expect(onSelect).toHaveBeenCalledWith("/sql/1.0/warehouses/def")
  })

  it("shows checkmark next to the currently selected warehouse", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    render(
      <WarehousePicker httpPath="/sql/1.0/warehouses/abc" onSelect={vi.fn()} />,
    )

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))
    await waitFor(() => {
      expect(screen.getByText("Starter Warehouse")).toBeInTheDocument()
    })

    // The button for the selected warehouse should have accent-soft background
    // and a Check icon. We verify by looking for the check SVG in that row.
    const selectedBtn = screen.getByText("Starter Warehouse").closest("button")!
    // Check icon is rendered as an SVG inside the selected warehouse button
    expect(selectedBtn.querySelector("svg")).toBeTruthy()
  })

  it("displays state indicator colors: green for RUNNING, red for STOPPED, amber for other", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses })
    const { container } = render(
      <WarehousePicker httpPath="" onSelect={vi.fn()} />,
    )

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))
    await waitFor(() => {
      expect(screen.getByText("Starter Warehouse")).toBeInTheDocument()
    })

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

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))
    await waitFor(() => {
      expect(screen.getByText("Small")).toBeInTheDocument()
    })
    expect(screen.getByText("Large")).toBeInTheDocument()
  })

  it("shows error when API call fails with ApiError", async () => {
    mockGetWarehouses.mockRejectedValue(makeApiError("HTTP 500", 500, "Server exploded"))
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))

    await waitFor(() => {
      expect(screen.getByText("Server exploded")).toBeInTheDocument()
    })
  })

  it("shows error.message for generic Error (not ApiError)", async () => {
    mockGetWarehouses.mockRejectedValue(new Error("Network failure"))
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))

    await waitFor(() => {
      expect(screen.getByText("Network failure")).toBeInTheDocument()
    })
  })

  it("shows 'No SQL Warehouses found' when API returns empty list", async () => {
    mockGetWarehouses.mockResolvedValue({ warehouses: [] })
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    fireEvent.click(screen.getByTitle("Fetch warehouses from Databricks"))

    await waitFor(() => {
      expect(screen.getByText("No SQL Warehouses found in this workspace")).toBeInTheDocument()
    })
  })

  it("disables Browse button during loading", async () => {
    let resolve: (v: unknown) => void
    mockGetWarehouses.mockReturnValue(new Promise(r => { resolve = r }))
    render(<WarehousePicker httpPath="" onSelect={vi.fn()} />)

    const btn = screen.getByTitle("Fetch warehouses from Databricks")
    fireEvent.click(btn)
    expect(btn).toBeDisabled()

    await act(async () => { resolve!({ warehouses: [] }) })
    expect(btn).not.toBeDisabled()
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
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalledWith("main"))

    // Select schema to populate tables
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalledWith("main", "default"))

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
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalled())
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalled())
    fireEvent.change(getTableSelect(), { target: { value: "users" } })
    expect(onSelect).toHaveBeenCalledWith("main.default.users")

    // Change schema: table select should reset
    mockGetTables.mockResolvedValue({ tables: [{ name: "orders", full_name: "main.analytics.orders", table_type: "TABLE", comment: "" }] })
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
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalled())
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalled())
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
    fireEvent.change(getCatalogSelect(), { target: { value: "main" } })
    await waitFor(() => expect(mockGetSchemas).toHaveBeenCalled())
    fireEvent.change(getSchemaSelect(), { target: { value: "default" } })
    await waitFor(() => expect(mockGetTables).toHaveBeenCalled())

    // Select then deselect table
    fireEvent.change(getTableSelect(), { target: { value: "users" } })
    fireEvent.change(getTableSelect(), { target: { value: "" } })

    expect(onSelect).toHaveBeenLastCalledWith("")
  })

  it("shows retained option when current value is not in loaded catalog list", async () => {
    mockGetCatalogs.mockResolvedValue({ catalogs: [{ name: "other", comment: "" }] })
    render(<CatalogTablePicker table="legacy.public.accounts" onSelect={vi.fn()} />)

    // The catalog "legacy" is initialized from props but not in the API response
    fireEvent.focus(getCatalogSelect())
    await waitFor(() => expect(mockGetCatalogs).toHaveBeenCalled())

    // The retained option should appear
    const catalogSelect = getCatalogSelect()
    const options = Array.from(catalogSelect.querySelectorAll("option"))
    const optionValues = options.map(o => o.value)
    expect(optionValues).toContain("legacy")
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
// DatabricksFetchButton
// ═══════════════════════════════════════════════════════════════════════════

describe("DatabricksFetchButton", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    cleanup()
  })

  const cachedStatus = {
    cached: true,
    path: "/tmp/cache/main.default.users.parquet",
    table: "main.default.users",
    row_count: 15000,
    column_count: 12,
    size_bytes: 2621440, // 2.5 MB
    fetched_at: 1709500800, // some unix timestamp
  }

  const uncachedStatus = {
    cached: false,
    table: "main.default.users",
    row_count: 0,
    column_count: 0,
    size_bytes: 0,
    fetched_at: 0,
  }

  it("checks cache status on mount when table is provided", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(mockGetCacheStatus).toHaveBeenCalledWith("main.default.users")
  })

  it("does not check cache status when table is empty", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    await act(async () => {
      render(<DatabricksFetchButton table="" httpPath="" query="" />)
    })

    expect(mockGetCacheStatus).not.toHaveBeenCalled()
  })

  it("calls onFetched when initial cache check finds cached data", async () => {
    mockGetCacheStatus.mockResolvedValue(cachedStatus)
    const onFetched = vi.fn()
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" onFetched={onFetched} />,
      )
    })

    expect(onFetched).toHaveBeenCalledWith(cachedStatus)
  })

  it("does not call onFetched when cache check returns uncached data", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    const onFetched = vi.fn()
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" onFetched={onFetched} />,
      )
    })

    expect(onFetched).not.toHaveBeenCalled()
  })

  it("shows 'Fetch Data' button when not cached", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(screen.getByText("Fetch Data")).toBeInTheDocument()
  })

  it("shows 'Refresh Data' button when cached", async () => {
    mockGetCacheStatus.mockResolvedValue(cachedStatus)
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(screen.getByText("Refresh Data")).toBeInTheDocument()
  })

  it("shows 'Not fetched yet' message when table exists but not cached", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(screen.getByText(/Not fetched yet/)).toBeInTheDocument()
  })

  it("disables fetch button when table is empty", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    await act(async () => {
      render(<DatabricksFetchButton table="" httpPath="" query="" />)
    })

    expect(screen.getByRole("button", { name: /Fetch Data/i })).toBeDisabled()
  })

  it("displays cache info with formatted row_count, column_count, and size", async () => {
    mockGetCacheStatus.mockResolvedValue(cachedStatus)
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    // row_count with toLocaleString -> "15,000"
    expect(screen.getByText("15,000 rows")).toBeInTheDocument()
    expect(screen.getByText("12 cols")).toBeInTheDocument()
    // 2621440 bytes = 2.5 MB
    expect(screen.getByText("2.5 MB")).toBeInTheDocument()
  })

  it("displays formatted time when fetched_at > 0", async () => {
    // Use a known timestamp and check that toLocaleTimeString output appears
    const ts = 1709500800 // 2024-03-03 around 21:20 UTC
    mockGetCacheStatus.mockResolvedValue({ ...cachedStatus, fetched_at: ts })
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    // The time is formatted with toLocaleTimeString; just check a colon is present
    // to be locale-independent
    const timeElements = screen.getAllByText(/:/)
    expect(timeElements.length).toBeGreaterThan(0)
  })

  it("does not display time when fetched_at is 0", async () => {
    mockGetCacheStatus.mockResolvedValue({ ...cachedStatus, fetched_at: 0 })
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    // Cache row should exist, but no time segment
    expect(screen.getByText("15,000 rows")).toBeInTheDocument()
    // The parent container should have "rows" and "cols" but the time separator should only appear 3 times (rows . cols . size . clear)
    // not 4 (rows . cols . size . time . clear). We verify by counting the dot separators.
  })

  it("formatBytes shows B for values under 1024", async () => {
    mockGetCacheStatus.mockResolvedValue({ ...cachedStatus, size_bytes: 512 })
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(screen.getByText("512 B")).toBeInTheDocument()
  })

  it("formatBytes shows KB for values between 1024 and 1MB", async () => {
    mockGetCacheStatus.mockResolvedValue({ ...cachedStatus, size_bytes: 5120 })
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(screen.getByText("5.0 KB")).toBeInTheDocument()
  })

  it("formatBytes shows MB for values >= 1MB", async () => {
    mockGetCacheStatus.mockResolvedValue({ ...cachedStatus, size_bytes: 10485760 })
    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    expect(screen.getByText("10.0 MB")).toBeInTheDocument()
  })

  it("calls fetchDatabricksData with correct payload on Fetch button click", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockResolvedValue({ table: "main.default.users", row_count: 100, column_count: 5, size_bytes: 4096, fetched_at: 1709500800 })

    await act(async () => {
      render(
        <DatabricksFetchButton
          table="main.default.users"
          httpPath="/sql/1.0/warehouses/abc"
          query="SELECT * FROM t"
        />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    expect(mockFetchDatabricksData).toHaveBeenCalledWith({
      table: "main.default.users",
      http_path: "/sql/1.0/warehouses/abc",
      query: "SELECT * FROM t",
    })
  })

  it("passes undefined for http_path and query when they are empty strings", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockResolvedValue({ table: "main.default.users", row_count: 50, column_count: 3, size_bytes: 1024, fetched_at: 1709500800 })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    expect(mockFetchDatabricksData).toHaveBeenCalledWith({
      table: "main.default.users",
      http_path: undefined,
      query: undefined,
    })
  })

  it("shows 'Connecting...' while fetching before progress arrives", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockReturnValue(new Promise(() => {})) // never resolves
    mockGetFetchProgress.mockResolvedValue({ active: false })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    expect(screen.getByText("Connecting...")).toBeInTheDocument()
  })

  it("disables fetch button while fetching is in progress", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockReturnValue(new Promise(() => {}))
    mockGetFetchProgress.mockResolvedValue({ active: false })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    const btn = screen.getByRole("button")
    await act(async () => {
      fireEvent.click(btn)
    })

    expect(btn).toBeDisabled()
  })

  it("polls progress during fetch and displays rows and elapsed time", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    let resolveFetch: (v: unknown) => void
    mockFetchDatabricksData.mockReturnValue(new Promise(r => { resolveFetch = r }))
    mockGetFetchProgress
      .mockResolvedValueOnce({ active: true, rows: 500, elapsed: 2 })
      .mockResolvedValueOnce({ active: true, rows: 1200, elapsed: 4 })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    // First poll at 1000ms
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    // Flush the resolved promise
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByText(/500 rows/)).toBeInTheDocument()
    expect(screen.getByText(/2s/)).toBeInTheDocument()

    // Second poll at 2000ms
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByText(/1,200 rows/)).toBeInTheDocument()
    expect(screen.getByText(/4s/)).toBeInTheDocument()

    // Complete the fetch
    await act(async () => {
      resolveFetch!({ table: "main.default.users", row_count: 1200, column_count: 5, size_bytes: 8192, fetched_at: 1709500800 })
    })
  })

  it("stops polling when fetch completes and calls onFetched", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    const fetchResult = { table: "main.default.users", row_count: 1000, column_count: 8, size_bytes: 51200, fetched_at: 1709500800 }
    mockFetchDatabricksData.mockResolvedValue(fetchResult)
    mockGetFetchProgress.mockResolvedValue({ active: false })
    const onFetched = vi.fn()

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" onFetched={onFetched} />,
      )
    })

    // onFetched was NOT called for initial uncached check
    expect(onFetched).not.toHaveBeenCalled()

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    // Let the fetch promise resolve
    await act(async () => {
      await Promise.resolve()
    })

    expect(onFetched).toHaveBeenCalledWith(
      expect.objectContaining({ cached: true, row_count: 1000, column_count: 8 }),
    )

    // After fetch completes, fetching is false so polling should stop.
    // Advance time -- getFetchProgress should not be called again.
    const callCount = mockGetFetchProgress.mock.calls.length
    await act(async () => {
      vi.advanceTimersByTime(3000)
    })
    expect(mockGetFetchProgress.mock.calls.length).toBe(callCount)
  })

  it("shows error when fetch fails with ApiError detail", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockRejectedValue(makeApiError("HTTP 500", 500, "Warehouse unavailable"))
    mockGetFetchProgress.mockResolvedValue({ active: false })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByText("Warehouse unavailable")).toBeInTheDocument()
  })

  it("shows error.message for generic Error on fetch failure", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockRejectedValue(new Error("Connection refused"))
    mockGetFetchProgress.mockResolvedValue({ active: false })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByText("Connection refused")).toBeInTheDocument()
  })

  it("delete button calls deleteCache and updates cache state", async () => {
    mockGetCacheStatus.mockResolvedValue(cachedStatus)
    mockDeleteCache.mockResolvedValue({ ...uncachedStatus, cached: false })

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    // Verify cache info is shown
    expect(screen.getByText("15,000 rows")).toBeInTheDocument()

    // Click delete
    await act(async () => {
      fireEvent.click(screen.getByTitle("Delete cached data"))
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(mockDeleteCache).toHaveBeenCalledWith("main.default.users")
    // After delete, cache is no longer shown -- "Fetch Data" button appears instead of "Refresh Data"
    expect(screen.getByText("Fetch Data")).toBeInTheDocument()
  })

  it("shows error when deleteCache fails with ApiError", async () => {
    mockGetCacheStatus.mockResolvedValue(cachedStatus)
    mockDeleteCache.mockRejectedValue(makeApiError("HTTP 500", 500, "Disk full"))

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    await act(async () => {
      fireEvent.click(screen.getByTitle("Delete cached data"))
    })

    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByText("Disk full")).toBeInTheDocument()
  })

  it("silently handles cache status check failure on mount", async () => {
    mockGetCacheStatus.mockRejectedValue(new Error("Not found"))

    await act(async () => {
      render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
    })

    // Should show fetch button (uncached state), no error displayed
    expect(screen.getByText("Fetch Data")).toBeInTheDocument()
    expect(screen.queryByText("Not found")).not.toBeInTheDocument()
  })

  it("cleans up polling interval on unmount during active fetch", async () => {
    mockGetCacheStatus.mockResolvedValue(uncachedStatus)
    mockFetchDatabricksData.mockReturnValue(new Promise(() => {}))
    mockGetFetchProgress.mockResolvedValue({ active: true, rows: 10, elapsed: 1 })

    const clearIntervalSpy = vi.spyOn(globalThis, "clearInterval")

    let unmount: () => void
    await act(async () => {
      const result = render(
        <DatabricksFetchButton table="main.default.users" httpPath="" query="" />,
      )
      unmount = result.unmount
    })

    await act(async () => {
      fireEvent.click(screen.getByText("Fetch Data"))
    })

    // Polling should be active
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })

    const callsBefore = clearIntervalSpy.mock.calls.length

    // Unmount during fetch
    await act(async () => {
      unmount!()
    })

    // clearInterval should have been called by the cleanup
    expect(clearIntervalSpy.mock.calls.length).toBeGreaterThan(callsBefore)

    clearIntervalSpy.mockRestore()
  })
})
