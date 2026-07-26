import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act, waitFor } from "@testing-library/react"
import { WarehousePicker, CatalogTablePicker } from "../_DatabricksSelector"

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
    { id: "w1", name: "Starter Warehouse", http_path: "/sql/1.0/warehouses/abc", state: "RUNNING", size: "Small" },
    { id: "w2", name: "Prod Warehouse", http_path: "/sql/1.0/warehouses/def", state: "STOPPED", size: "Large" },
    { id: "w3", name: "Dev Warehouse", http_path: "/sql/1.0/warehouses/ghi", state: "STARTING", size: "" },
  ]

  it("renders the input with current httpPath value", () => {
    render(<WarehousePicker httpPath="/sql/1.0/warehouses/abc" onSelect={vi.fn()} />)
    const input = screen.getByPlaceholderText("/sql/1.0/warehouses/abc123")
    expect(input).toHaveValue("/sql/1.0/warehouses/abc")
  })

  it("commits a typed path once on blur, not per keystroke (undo-atomicity)", () => {
    const onSelect = vi.fn()
    render(<WarehousePicker httpPath="" onSelect={onSelect} />)
    const input = screen.getByPlaceholderText("/sql/1.0/warehouses/abc123")
    fireEvent.change(input, { target: { value: "/sql/custom/path" } })
    // Typing buffers locally — nothing committed yet.
    expect(onSelect).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(onSelect).toHaveBeenCalledTimes(1)
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
