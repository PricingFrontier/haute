/**
 * Schema database status (specs/workbench): a compact selector reads each database once,
 * field-local status and rename choices follow the current form, and retained fields stay
 * in their table. Session snapshots and explicit choices survive pane closure.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchStoreCatalogue: vi.fn(),
  fetchStoreCompare: vi.fn(),
  fetchStoreSql: vi.fn(),
}))

import { ApiError } from "../../api/client"
import type { WorkbenchCatalogueTable, WorkbenchStoreCompareResponse } from "../../api/types"
import { fetchStoreCatalogue, fetchStoreCompare } from "../../api/workbench"
import useStoreCompareStore from "../../stores/useStoreCompareStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { updateSchemaTable } from "../../utils/workbenchForm"
import SchemaPane from "../SchemaPane"
import { detailsTable, sheetForm } from "./fixtures"

const TABLES: WorkbenchCatalogueTable[] = [
  {
    name: "policy",
    columns: [
      { name: "region", kind: "string", type: "TEXT", nullable: true, collation: null, quotes: 3 },
      { name: "year", kind: "string", type: "TEXT", nullable: false, collation: null, quotes: 3 },
    ],
    primary_key: ["quote_reference", "quote_option"],
    refers: true,
    quotes: 4,
  },
]

const COMPARED: WorkbenchStoreCompareResponse = {
  differences: [
    { table: "policy", column: "state", kind: "missing", message: "the database has no state" },
    { table: "policy", column: "year", kind: "type", message: "the database holds it as TEXT, not BIGINT (3 quotes hold values in it)" },
    { table: "equipment", column: null, kind: "missing", message: "the database has no equipment" },
    { table: null, column: null, kind: "missing", message: "the database has no haute_activity" },
  ],
  extras: [
    { table: "policy", column: "region", form_table: "policy", quotes: 3 },
    { table: "policy", column: "legacy", form_table: "policy", quotes: null },
  ],
  renames: [],
  matches: false,
  sql: "BEGIN;\nALTER TABLE policy ADD COLUMN state TEXT;\nCOMMIT;\n",
  provider: "sqlite", changes: [
    { kind: "keep column", table: "policy", column: "region", was: null, disposition: "retained", description: "Retain this unused column; supported values survive saves until the row is deleted.", quotes: 3 },
    { kind: "keep column", table: "policy", column: "legacy", was: null, disposition: "retained", description: "Retain this unused column; supported values survive saves until the row is deleted.", quotes: null },
  ], manual: [], blocked: [], requires_maintenance: false,
}

const open = () => {
  useWorkbenchViewStore.setState({ mode: "build", schemaOpen: true })
  return render(<SchemaPane />)
}

const choose = (environment: string) => fireEvent.change(screen.getByRole("combobox", { name: "Database" }), { target: { value: environment } })
const option = (name: string) => screen.getByRole("option", { name })
const openField = (name: string) => {
  const button = within(screen.getByTestId(`schema-column-${name}`)).getByRole("button", { name: `Database status of ${name}` })
  if (button.getAttribute("aria-expanded") !== "true") fireEvent.click(button)
}

describe("Compare", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["dev", "prod"], storeColumns: {} })
    useStoreCompareStore.getState().reset()
    useWorkbenchViewStore.setState({ schemaDetails: null, schemaSearch: "" })
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, checked_at: 1700000000000, tables: TABLES }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(COMPARED)
  })

  afterEach(() => {
    cleanup()
    vi.resetAllMocks()
    useStoreCompareStore.getState().reset()
  })

  it("reuses persisted snapshot times after a session reset and explicitly refreshes the database", async () => {
    open()
    choose("dev")
    await waitFor(() => expect(option("dev")).toHaveAccessibleDescription("Changes needed"))
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute("title", `Schema snapshot fetched ${new Date(1700000000000).toLocaleString()}`)
    act(() => useStoreCompareStore.getState().reset())
    choose("dev")
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenCalledTimes(2))
    expect(fetchStoreCatalogue).toHaveBeenLastCalledWith("dev", false)
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute("title", `Schema snapshot fetched ${new Date(1700000000000).toLocaleString()}`)
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", checked_at: 1700000060000, tables: TABLES })
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenCalledTimes(3))
    expect(fetchStoreCatalogue).toHaveBeenLastCalledWith("dev", true)
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute("title", `Schema snapshot fetched ${new Date(1700000060000).toLocaleString()}`)
  })

  it("shows hidden managed and storage repairs in table details without field controls", async () => {
    const form = sheetForm()
    form.schema.tables.unshift(detailsTable())
    useWorkbenchFormStore.getState().install(form, "rev-1")
    useWorkbenchStore.setState({ storeColumns: { quote_details: [
      { name: "quote_version", kind: "int" }, { name: "saved_at", kind: "datetime" },
    ] } })
    const problem = "quote_details.saved_at, the store's own column, is missing"
    const audit = "quote_details.modified_date has the wrong SQL type"
    const visible = "quote_details.insured has the wrong SQL type"
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [
      { table: "quote_details", column: "saved_at", kind: "store", message: problem },
      { table: "quote_details", column: "modified_date", kind: "type", message: audit },
      { table: "quote_details", column: "insured", kind: "type", message: visible },
    ], changes: [], extras: [], manual: [problem] })
    open()
    const table = within(screen.getByRole("region", { name: "Table quote_details" }))
    expect(table.queryByText(/^Database details/)).toBeNull()
    choose("dev")
    await waitFor(() => expect(table.getByTestId("database-mark-quote_details")).toHaveTextContent("2 differences"))
    expect(fetchStoreCompare).toHaveBeenCalledWith(form, "dev", TABLES, [])
    expect(table.queryByTestId(/^schema-store-column-/)).toBeNull()
    expect(table.queryByTestId("schema-column-modified_date")).toBeNull()
    fireEvent.click(table.getByText("Database details"))
    const details = table.getByRole("region", { name: "Database details for quote_details" })
    expect(details).toHaveTextContent(problem)
    expect(details).toHaveTextContent(audit)
    expect(details).not.toHaveTextContent(visible)
    expect(within(details).queryByRole("combobox")).toBeNull()
    expect(within(details).queryByRole("textbox")).toBeNull()
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(table.getByRole("region", { name: "Database details for quote_details" })).toHaveTextContent(problem)
    let fail!: (error: Error) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(table.queryByText("Database details")).toBeNull()
    expect(table.queryByTestId("database-mark-quote_details")).toBeNull()
    await act(async () => fail(new Error("Database unavailable")))
    expect(screen.getByRole("alert")).toHaveTextContent("Database unavailable")
    expect(table.queryByText("Database details")).toBeNull()
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", checked_at: 1700000000000, tables: TABLES })
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await waitFor(() => expect(table.getByText("Database details")).toBeInTheDocument())
    choose("")
    expect(table.queryByText("Database details")).toBeNull()
    expect(table.queryByTestId("database-mark-quote_details")).toBeNull()
  })

  it("uses Local and name-only database colours, clearing a match throughout refresh", async () => {
    let finishRead!: (value: { environment: string; checked_at: number; tables: WorkbenchCatalogueTable[] }) => void
    let finishCompare!: (value: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((resolve) => { finishRead = resolve }))
    vi.mocked(fetchStoreCompare).mockImplementation(() => new Promise((resolve) => { finishCompare = resolve }))
    open()
    const database = screen.getByRole("combobox", { name: "Database" })
    const refresh = screen.getByRole("button", { name: "Refresh" })
    expect(within(database).getAllByRole("option").map((each) => each.textContent)).toEqual(["Local", "dev", "prod"])
    expect(database).toHaveValue("")
    expect(database).toHaveStyle({ color: "var(--text-primary)" })
    expect(refresh).toHaveTextContent("Refresh")
    expect(refresh).toHaveAttribute("aria-busy", "false")
    expect(refresh).toBeDisabled()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()

    choose("dev")
    expect(database).toHaveStyle({ color: "var(--danger-text)" })
    expect(option("dev")).toHaveAccessibleDescription("Checking")
    expect(refresh).toBeDisabled()
    expect(refresh).toHaveAttribute("aria-busy", "true")
    expect(refresh.querySelector("svg")).toHaveClass("animate-spin")
    await act(async () => finishRead({ environment: "dev", checked_at: 1700000000000, tables: TABLES }))
    expect(database).toHaveStyle({ color: "var(--caution)" })
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    expect(option("dev")).toHaveAccessibleDescription("Comparing")
    expect(refresh).toBeDisabled()
    expect(refresh).toHaveAttribute("aria-busy", "true")
    await act(async () => finishCompare({ ...COMPARED, differences: [], extras: [], changes: [], matches: true }))
    expect(refresh).toBeEnabled()
    expect(refresh).toHaveAttribute("aria-busy", "false")
    expect(refresh.querySelector("svg")).not.toHaveClass("animate-spin")
    expect(database).toHaveStyle({ color: "var(--success)" })
    expect(option("dev")).toHaveStyle({ color: "var(--success)" })
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })

    fireEvent.click(refresh)
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(2)
    expect(database).toHaveStyle({ color: "var(--danger-text)" })
    expect(option("dev")).toHaveStyle({ color: "var(--danger-text)" })
    expect(refresh).toBeDisabled()
    expect(refresh).toHaveAttribute("aria-busy", "true")
    await act(async () => finishRead({ environment: "dev", checked_at: 1700000000000, tables: TABLES }))
    expect(database).toHaveStyle({ color: "var(--caution)" })
    await act(async () => finishCompare(COMPARED))
    expect(refresh).toHaveAttribute("aria-busy", "false")
    expect(database).toHaveStyle({ color: "var(--caution)" })
    expect(option("dev")).toHaveAccessibleDescription("Changes needed")
    expect(option("dev")).toHaveTextContent(/^dev$/)
  })

  it("keeps quote_details keys fixed while showing current database and missing-table details", async () => {
    const form = sheetForm()
    form.schema.tables.unshift(detailsTable())
    useWorkbenchFormStore.getState().install(form, "rev-1")
    useWorkbenchStore.setState({ storeColumns: { quote_details: [{ name: "quote_version", kind: "int" }] } })
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [], changes: [], extras: [], matches: true })
    open()
    choose("dev")
    const row = within(screen.getByTestId("schema-column-quote_reference"))
    const status = row.getByRole("button", { name: "Database status of quote_reference" })
    await waitFor(() => expect(status).toHaveTextContent("Matches"))
    fireEvent.click(status)
    expect(row.queryByRole("button", { name: "Label and rules of quote_reference" })).toBeNull()
    expect(row.queryByRole("textbox", { name: "Label of quote_reference" })).toBeNull()
    let finish!: (answer: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCompare).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    act(() => useWorkbenchFormStore.getState().change((current) => ({ ...current, name: "Changed" })))
    expect(status).toHaveTextContent("Comparing")
    expect(row.queryByText("This field matches the database.")).not.toBeInTheDocument()
    await act(async () => finish({ ...COMPARED, differences: [
      { table: "quote_details", column: null, kind: "missing", message: "the database has no quote_details" },
    ], changes: [], extras: [] }))
    expect(status).toHaveTextContent("New column")
    expect(row.getByRole("region")).toHaveTextContent("the database has no quote_details")
    expect(row.queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("keeps database selection compact and resolves a rename in the field's own details", async () => {
    open()
    expect(screen.queryByRole("button", { name: "Compare dev" })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "Database" }), { target: { value: "dev" } })
    const row = screen.getByTestId("schema-column-state")
    const status = await within(row).findByRole("button", { name: "Database status of state" })
    expect(status).toHaveTextContent("New column")
    expect(screen.queryByTestId("store-compare")).not.toBeInTheDocument()
    expect(within(row).queryByRole("combobox", { name: "Database column for policy.state" })).not.toBeInTheDocument()

    fireEvent.click(status)
    expect(within(row).queryByRole("textbox", { name: "Label of state" })).not.toBeInTheDocument()
    const source = within(row).getByRole("combobox", { name: "Database column for policy.state" })
    expect(source).toHaveValue("")
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, _tables, renames) => ({ ...COMPARED, renames }))
    fireEvent.change(source, { target: { value: "region" } })
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, [{ table: "policy", column: "state", was: "region" }]))
    expect(within(row).getByRole("combobox", { name: "Database column for policy.state" })).toHaveValue("region")
    fireEvent.change(within(row).getByRole("combobox", { name: "Database column for policy.state" }), { target: { value: "" } })
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, []))
  })

  it("opens database details and field configuration separately, switching or closing the active view", async () => {
    open()
    choose("dev")
    const row = within(screen.getByTestId("schema-column-state"))
    const status = row.getByRole("button", { name: "Database status of state" })
    const config = row.getByRole("button", { name: "Label and rules of state" })
    const databaseDetails = () => row.queryByRole("region", { name: "Database details for policy.state" })
    const label = () => row.queryByRole("textbox", { name: "Label of state" })
    await waitFor(() => expect(status).toHaveTextContent("New column"))
    expect(status).toHaveAttribute("aria-expanded", "false")
    expect(config).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(config)
    expect(label()).toBeInTheDocument()
    expect(databaseDetails()).not.toBeInTheDocument()
    expect(config).toHaveAttribute("aria-expanded", "true")
    expect(status).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(status)
    expect(databaseDetails()).toBeInTheDocument()
    expect(label()).not.toBeInTheDocument()
    expect(config).toHaveAttribute("aria-expanded", "false")
    expect(status).toHaveAttribute("aria-expanded", "true")
    fireEvent.click(status)
    expect(databaseDetails()).not.toBeInTheDocument()
    expect(status).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(status)
    fireEvent.click(config)
    expect(label()).toBeInTheDocument()
    expect(databaseDetails()).not.toBeInTheDocument()
    expect(status).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(config)
    expect(label()).not.toBeInTheDocument()
    expect(config).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(config)
    openField("year")
    expect(label()).not.toBeInTheDocument()
    expect(config).toHaveAttribute("aria-expanded", "false")
    expect(screen.getByRole("region", { name: "Database details for policy.year" })).toBeInTheDocument()
    choose("")
    expect(screen.queryByRole("region", { name: "Database details for policy.year" })).not.toBeInTheDocument()
    expect(screen.queryByRole("textbox", { name: "Label of year" })).not.toBeInTheDocument()
  })

  it("reads the chosen environment's database and marks each table and column of the form where it differs", async () => {
    open()
    expect(option("dev")).toHaveStyle({ color: "var(--danger-text)" })
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })

    choose("dev")

    expect(await screen.findByTestId("database-mark-policy-state")).toHaveTextContent("New column")
    expect(fetchStoreCatalogue).toHaveBeenCalledWith("dev", false)
    expect(fetchStoreCompare).toHaveBeenCalledWith(useWorkbenchFormStore.getState().form, "dev", TABLES, [])
    const typed = screen.getByTestId("database-mark-policy-year")
    expect(typed).toHaveTextContent("Type change")
    expect(typed).toHaveAttribute("title", expect.stringContaining("3 quotes hold values in it"))
    expect(screen.getByTestId("database-mark-equipment")).toHaveTextContent("missing")
    expect(screen.queryByTestId("store-compare")).not.toBeInTheDocument()
    fireEvent.click(within(screen.getByRole("region", { name: "Table policy" })).getByText("Database details · 2 retained fields"))
    const extras = screen.getByRole("region", { name: "Database details for policy" })
    expect(extras).toHaveTextContent("region")
    expect(extras).toHaveTextContent("3 quotes")
    expect(extras).toHaveTextContent("until the row is deleted")
  })

  it("says when the database matches the form", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [], extras: [], matches: true })
    open()
    choose("dev")

    expect(await screen.findByRole("status")).toHaveTextContent("Compatible")
    expect(option("dev")).toHaveStyle({ color: "var(--success)" })
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Matches")
  })

  it("explains a missing table in its fields' database details", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, changes: [
      { kind: "create table", table: "equipment", column: null, was: null, disposition: "sql", description: "Create equipment.", quotes: null },
    ] })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-equipment-item")).toHaveTextContent("New column"))
    openField("item")
    const details = within(screen.getByRole("region", { name: "Database details for equipment.item" }))
    expect(details.getByText("Table missing")).toBeVisible()
    expect(details.getByText(/Use Export SQL, then run the query/)).toHaveTextContent("create equipment")
    expect(details.getByText("the database has no equipment")).not.toBeVisible()
  })

  it("explains a renamed row key with one addition and manual data/key work, keeping SQL details secondary", async () => {
    const form = sheetForm()
    const table = form.schema.tables.find((each) => each.name === "equipment")!
    const key = table.inputs.find((each) => each.name === "row_number")!
    key.type = "str"
    key.index = "typed"
    useWorkbenchFormStore.getState().install(form, "rev-1")
    const catalogue: WorkbenchCatalogueTable = {
      name: "equipment", primary_key: ["quote_reference", "quote_option", "quote_row"], refers: true, quotes: 3,
      columns: [{ name: "quote_row", kind: "integer", type: "BIGINT", nullable: false, collation: null, quotes: 3 }],
    }
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", checked_at: 1700000000000, tables: [...TABLES, catalogue] })
    const messages = [
      "the database names it quote_row",
      "the database holds it as BIGINT, not NVARCHAR(366) COLLATE Latin1_General_100_BIN2",
      "equipment.quote_row compares under another collation, not Latin1_General_100_BIN2, so two quotes could be taken for one.",
    ]
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED,
      differences: (["renamed", "type", "collation"] as const).map((kind, index) => ({ table: "equipment", column: "row_number", kind, message: messages[index] })),
      changes: [{ kind: "add column", table: "equipment", column: "row_number", was: null, disposition: "sql", description: "Add row_number as Text.", quotes: 3 }],
    })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-equipment-row_number")).toHaveTextContent("Needs attention"))
    fireEvent.click(within(screen.getByRole("region", { name: "Table equipment" })).getByRole("button", { name: "Database status of row_number" }))
    const details = within(screen.getByRole("region", { name: "Database details for equipment.row_number" }))
    expect(details.getByText("New column needed")).toBeVisible()
    expect(details.getByText("Haute: row_number (Text). Database: quote_row (Integer).")).toBeVisible()
    expect(details.getByText(/Export SQL adds row_number/)).toHaveTextContent("Copy any existing values from quote_row manually")
    expect(details.getByText(/Fill every row/)).toHaveTextContent("NOT NULL")
    expect(details.getByText(/Fill every row/)).toHaveTextContent("primary key")
    expect(details.queryByText("Different data type")).not.toBeInTheDocument()
    expect(details.queryByText("Different text comparison")).not.toBeInTheDocument()
    for (const message of messages) expect(details.getByText(message)).not.toBeVisible()
    fireEvent.click(details.getByText("SQL details"))
    for (const message of messages) expect(details.getByText(message)).toBeVisible()
    let finish!: (value: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCompare).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    act(() => useWorkbenchFormStore.getState().change((current) => ({ ...current, name: "Changed" })))
    expect(details.queryByText("New column needed")).not.toBeInTheDocument()
    expect(details.queryByText("SQL details")).not.toBeInTheDocument()
    await act(async () => finish({ ...COMPARED, differences: [], changes: [], matches: true }))
    expect(details.getByText("This field matches the database.")).toBeVisible()
  })

  it("explains existing type and blank-value repairs without promising them in Export SQL", async () => {
    const repair = "policy.year takes no null, so an empty year could not be saved"
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED,
      differences: [...COMPARED.differences, { table: "policy", column: "year", kind: "null", message: repair }],
      changes: [],
      manual: [repair],
    })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-policy-year")).toHaveTextContent("Needs attention"))
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    openField("year")
    const details = screen.getByRole("region", { name: "Database details for policy.year" })
    expect(within(details).getByText("Different data type")).toBeVisible()
    expect(within(details).getByText("Haute: year (Integer). Database: year (Text).")).toBeVisible()
    expect(within(details).getByText(/Change the type in Haute to match/)).toHaveTextContent("Export SQL does not convert existing columns")
    expect(within(details).getByText("Different blank-value rules")).toBeVisible()
    expect(within(details).getByText(/Allow NULL in this column/)).toBeVisible()
    expect(within(details).getByText(repair)).not.toBeVisible()
    fireEvent.click(within(details).getByText("SQL details"))
    expect(within(details).getByText(repair)).toBeVisible()
  })

  it("directs fixed field types to database repair without offering a Haute type change", async () => {
    const form = sheetForm()
    form.schema.tables.unshift(detailsTable())
    useWorkbenchFormStore.getState().install(form, "rev-1")
    const names = ["quote_reference", "insured"]
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", checked_at: 1700000000000, tables: [{
      name: "quote_details", primary_key: ["quote_reference", "quote_option"], refers: false, quotes: 3,
      columns: names.map((name) => ({ name, kind: "integer", type: "BIGINT", nullable: false, collation: null, quotes: 3 })),
    }] })
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, changes: [],
      differences: names.map((column) => ({ table: "quote_details", column, kind: "type", message: "Wrong SQL type" })),
    })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-quote_details-insured")).toHaveTextContent("Type change"))
    const table = within(screen.getByRole("region", { name: "Table quote_details" }))
    for (const name of names) {
      fireEvent.click(table.getByRole("button", { name: `Database status of ${name}` }))
      const details = within(screen.getByRole("region", { name: `Database details for quote_details.${name}` }))
      expect(details.getByText(/Convert this database column to Text/)).toHaveTextContent("fixed type")
      expect(details.queryByText(/Change the type in Haute/)).not.toBeInTheDocument()
    }
  })

  it("explains text comparison as a manual database change", async () => {
    const message = "policy.state compares under another collation, not Latin1_General_100_BIN2"
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED,
      differences: [{ table: "policy", column: "state", kind: "collation", message }], changes: [],
    })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Needs attention"))
    openField("state")
    const details = within(screen.getByRole("region", { name: "Database details for policy.state" }))
    expect(details.getByText("Different text comparison")).toBeVisible()
    expect(details.getByText(/exact text matching/)).toBeVisible()
    expect(details.getByText(/Apply the collation in SQL details/)).toHaveTextContent("Export SQL does not change existing columns")
    expect(details.getByText(message)).not.toBeVisible()
  })

  it("shows a blocked addition's reason without claiming SQL will add it", async () => {
    const reason = "Not added: policy.state already exists. Resolve the name change manually."
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED,
      changes: [{ kind: "add column", table: "policy", column: "state", was: null, disposition: "manual", description: reason, quotes: null }],
    })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Needs attention"))
    openField("state")
    const details = within(screen.getByRole("region", { name: "Database details for policy.state" }))
    expect(details.getByText("Cannot add this field yet")).toBeVisible()
    expect(details.getAllByText(reason).some((node) => !node.closest("details"))).toBe(true)
    expect(details.getByText(/Correct the field or table name/)).toBeVisible()
    expect(details.queryByText(/Export SQL adds/)).not.toBeInTheDocument()
  })

  it("keeps database-only fields visible during a manual table reshape", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED,
      differences: [{ table: "policy", column: null, kind: "rows", message: "the database holds many rows per quote" }],
      changes: [{ kind: "reshape table", table: "policy", column: null, was: null, disposition: "manual", description: "Rebuild the table and migrate its rows.", quotes: 4 }],
    })
    open()
    choose("dev")
    const table = screen.getByRole("region", { name: "Table policy" })
    fireEvent.click(await within(table).findByText(/Database details/))
    const details = screen.getByRole("region", { name: "Database details for policy" })
    expect(details).toHaveTextContent("region")
    expect(details).toHaveTextContent("legacy")
    expect(details).toHaveTextContent("3 quotes")
    expect(details).toHaveTextContent(/manual migration/)
    expect(details).not.toHaveTextContent("survive saves")
  })

  it("allows removal of an old rename when its target name now belongs to an index", async () => {
    useStoreCompareStore.getState().pair({ table: "equipment", column: "row_number", was: "old_field" })
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, _tables, renames) => ({ ...COMPARED,
      blocked: renames.length ? ["Cannot rename equipment.old_field to row_number."] : [],
    }))
    open()
    choose("dev")
    const table = screen.getByRole("region", { name: "Table equipment" })
    const status = within(table).getByRole("button", { name: "Database status of row_number" })
    await waitFor(() => expect(status).toHaveTextContent("Needs attention"))
    fireEvent.click(status)
    const details = screen.getByRole("region", { name: "Database details for equipment.row_number" })
    expect(within(details).queryByRole("combobox")).not.toBeInTheDocument()
    fireEvent.click(within(details).getByRole("button", { name: "Remove rename choice" }))
    await waitFor(() => expect(status).toHaveTextContent("New column"))
    expect(useStoreCompareStore.getState().renames).toEqual([])
    expect(within(details).queryByRole("alert")).not.toBeInTheDocument()
  })

  it("replaces field summaries with current database status and restores them when deselected", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [], extras: [], changes: [], matches: true })
    open()
    expect(screen.getByTestId("schema-column-state-summary")).toBeInTheDocument()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Matches"))
    expect(screen.queryByTestId("schema-column-state-summary")).not.toBeInTheDocument()
    openField("state")
    expect(screen.getByRole("region", { name: "Database details for policy.state" })).toHaveTextContent("This field matches the database.")

    let fail!: (error: unknown) => void
    vi.mocked(fetchStoreCompare).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    act(() => useWorkbenchFormStore.getState().change((form) => ({ ...form, name: "Changed" })))
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Comparing")
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    expect(screen.queryByText("This field matches the database.")).not.toBeInTheDocument()
    await act(async () => fail(new Error("Compare failed")))
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Check failed")
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute("aria-busy", "false")
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled()
    choose("")
    expect(screen.queryByTestId("database-mark-policy-state")).not.toBeInTheDocument()
    expect(screen.getByTestId("schema-column-state-summary")).toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Database details for policy.state" })).not.toBeInTheDocument()
  })

  it("keeps an invalid rename actionable locally and disables its choice during a fresh check", async () => {
    useStoreCompareStore.getState().pair({ table: "policy", column: "state", was: "region" })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Needs attention"))
    openField("state")
    const field = screen.getByRole("region", { name: "Database details for policy.state" })
    expect(within(field).getByRole("alert")).toHaveTextContent("This rename cannot be used in dev")
    let finish!: (value: { environment: string; checked_at: number; tables: WorkbenchCatalogueTable[] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(within(field).getByRole("combobox")).toBeDisabled()
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Checking")
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [], changes: [], extras: [], matches: true })
    await act(async () => finish({ environment: "dev", checked_at: 1700000000000, tables: [{ ...TABLES[0], columns: [{ ...TABLES[0].columns[0], name: "state" }] }] }))
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Matches")
    expect(within(field).queryByRole("alert")).not.toBeInTheDocument()
    expect(within(field).getByRole("combobox")).toBeEnabled()
    fireEvent.change(within(field).getByRole("combobox"), { target: { value: "" } })
    expect(useStoreCompareStore.getState().renames).toEqual([])
  })

  it("compares the form again as it changes without reading the database again", async () => {
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")

    useWorkbenchFormStore.getState().change((form) => updateSchemaTable(form, "equipment", { name: "kit" }))

    await waitFor(() => expect(fetchStoreCompare).toHaveBeenCalledTimes(2))
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetchStoreCompare).mock.calls[1][0]).toBe(useWorkbenchFormStore.getState().form)
  })

  it("pairs a column the database holds with one the form lacks as a rename, and unpairs it", async () => {
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")
    openField("state")
    const pairing = screen.getByRole("combobox", { name: "Database column for policy.state" })
    vi.mocked(fetchStoreCompare).mockResolvedValue({
      ...COMPARED,
      extras: [],
      differences: [{ table: "policy", column: "state", kind: "renamed", message: "the database names it region" }],
      changes: [{ kind: "add column", table: "policy", column: "state", was: null, disposition: "sql", description: "Add state as Text.", quotes: null }],
      renames: [{ table: "policy", column: "state", was: "region" }],
    })

    fireEvent.change(pairing, { target: { value: "region" } })

    await waitFor(() =>
      expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, [
        { table: "policy", column: "state", was: "region" },
      ]),
    )
    const renamed = await screen.findByRole("region", { name: "Database details for policy.state" })
    expect(renamed).toHaveTextContent("region → state")
    expect(within(renamed).getByText("Haute: state (Text). Database: region (Text).")).toBeVisible()
    expect(within(renamed).getByText(/Export SQL adds state/)).toHaveTextContent("Copy any existing values from region manually")
    expect(within(renamed).queryByText(/update the table's primary key/)).not.toBeInTheDocument()

    vi.mocked(fetchStoreCompare).mockResolvedValue(COMPARED)
    fireEvent.change(within(renamed).getByRole("combobox"), { target: { value: "" } })

    await waitFor(() => expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, []))
  })

  it("reads the database again on Refresh, keeping the pairings", async () => {
    // The comparison takes every pairing it is sent.
    vi.mocked(fetchStoreCompare).mockImplementation((_form, _environment, _tables, renames) => Promise.resolve({ ...COMPARED, renames }))
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")
    useStoreCompareStore.getState().pair({ table: "policy", column: "state", was: "region" })

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))

    await waitFor(() => expect(fetchStoreCatalogue).toHaveBeenCalledTimes(2))
    expect(useStoreCompareStore.getState().renames).toEqual([{ table: "policy", column: "state", was: "region" }])
  })

  it("says why the database could not be read, marking nothing", async () => {
    vi.mocked(fetchStoreCatalogue).mockRejectedValue(
      new ApiError("connections.toml has no [prod] connection: add one saying where the prod database is and how you sign in to it.", 409),
    )
    open()
    choose("prod")

    expect(await screen.findByRole("alert")).toHaveTextContent("connections.toml has no [prod] connection")
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute("aria-busy", "false")
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled()
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })
    expect(fetchStoreCompare).not.toHaveBeenCalled()
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Check failed")
  })

  it("says why the form could not be compared, the database read", async () => {
    vi.mocked(fetchStoreCompare).mockRejectedValue(new ApiError("A column name is refused.", 422))
    open()
    choose("dev")

    expect(await screen.findByRole("alert")).toHaveTextContent("A column name is refused.")
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Check failed")
  })

  it("compares with nothing once Compare is set back to none, and reads nothing again without an environment", async () => {
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")

    act(() => useStoreCompareStore.getState().choose(null))

    expect(screen.queryByTestId("store-compare")).not.toBeInTheDocument()
    expect(screen.queryByTestId("database-mark-policy-state")).not.toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Database" })).toHaveStyle({ color: "var(--text-primary)" })
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled()
    expect(useStoreCompareStore.getState()).toMatchObject({ environment: null, renames: [] })
    useStoreCompareStore.getState().readAgain()
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(1)
  })

  it("keeps snapshots and explicit rename choices when changing environments and closing the pane", async () => {
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, _tables, renames) => ({ ...COMPARED, renames }))
    const shown = open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")
    openField("state")
    fireEvent.change(screen.getByRole("combobox", { name: "Database column for policy.state" }), { target: { value: "region" } })
    choose("prod")
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenCalledWith(expect.anything(), "prod", TABLES, [{ table: "policy", column: "state", was: "region" }]))
    shown.unmount()
    open()
    choose("dev")
    expect(await screen.findByRole("region", { name: "Database details for policy.state" })).toHaveTextContent("region → state")
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(2)
    expect(option("prod")).toHaveStyle({ color: "var(--caution)" })
  })

  it("replaces conflicting pairings, preserves shared intent and ignores superseded reads and comparisons", async () => {
    const store = useStoreCompareStore.getState()
    store.pair({ table: "policy", column: "state", was: "region" })
    store.pair({ table: "equipment", column: "make", was: "brand" })
    // Replacing by form column and by database column keeps other tables' pairings.
    store.pair({ table: "policy", column: "state", was: "territory" })
    store.pair({ table: "policy", column: "year", was: "territory" })
    expect(useStoreCompareStore.getState().renames).toEqual([
      { table: "equipment", column: "make", was: "brand" },
      { table: "policy", column: "year", was: "territory" },
    ])
    store.unpair("policy", "year")
    expect(useStoreCompareStore.getState().renames).toEqual([
      { table: "equipment", column: "make", was: "brand" },
    ])

    // Superseded catalogue read (both success and failure) is ignored after reset.
    let resolveRead!: (value: { environment: string; checked_at: number; tables: WorkbenchCatalogueTable[] }) => void
    let rejectRead!: (error: unknown) => void
    vi.mocked(fetchStoreCatalogue)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRead = resolve }))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRead = reject }))

    store.choose("dev")
    store.choose("prod")
    resolveRead({ environment: "dev", checked_at: 1700000000000, tables: TABLES })
    store.reset()
    rejectRead(new ApiError("Stale failure", 503))
    await Promise.resolve()
    expect(useStoreCompareStore.getState().environments).toEqual({})

    // A rejected pairing stays explicit for other environments; an older failure cannot land.
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", checked_at: 1700000000000, tables: TABLES })
    let rejectCompare!: (error: unknown) => void
    vi.mocked(fetchStoreCompare)
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectCompare = reject }))
      .mockResolvedValueOnce({ ...COMPARED, renames: [] })

    store.choose("dev")
    await waitFor(() => expect(useStoreCompareStore.getState().environments.dev.read.status).toBe("read"))
    store.pair({ table: "policy", column: "state", was: "region" })
    const form = useWorkbenchFormStore.getState().form!
    store.compare(form)
    store.compare({ ...form, name: "New target" })
    rejectCompare(new ApiError("Stale compare error", 422))
    await waitFor(() => expect(useStoreCompareStore.getState().environments.dev.compared.status).toBe("compared"))
    expect(useStoreCompareStore.getState().renames).toEqual([{ table: "policy", column: "state", was: "region" }])
  })

  it("checks environments independently, shows freshness, and verifies only from a fresh catalogue", async () => {
    let finishProd!: (answer: { environment: string; checked_at: number; tables: WorkbenchCatalogueTable[] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation((environment) => environment === "prod"
      ? new Promise((resolve) => { finishProd = resolve })
      : Promise.resolve({ environment, checked_at: 1700000000000, tables: TABLES }))
    open()
    act(() => useStoreCompareStore.getState().checkAll(["dev", "prod"]))
    await waitFor(() => expect(option("dev")).toHaveAccessibleDescription("Changes needed"))
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })
    await act(async () => finishProd({ environment: "prod", checked_at: 1700000000000, tables: TABLES }))
    choose("prod")
    expect(await screen.findByTestId("database-mark-policy-state")).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveAttribute("title", expect.stringContaining("Schema snapshot fetched"))

    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "prod", checked_at: 1700000000000, tables: [] })
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, tables) => tables.length === 0
      ? { ...COMPARED, differences: [], matches: true } : COMPARED)
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Database" })).toHaveAccessibleDescription("Compatible"))
    expect(within(screen.getByRole("region", { name: "Table policy" })).getByText("Database details · 2 retained fields").closest("details")).not.toHaveAttribute("open")
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(3)
  })

  it("forgets session choices and late replies on document, form and environment changes", async () => {
    let finish!: (answer: { environment: string; checked_at: number; tables: WorkbenchCatalogueTable[] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const store = useStoreCompareStore.getState()
    store.choose("dev")
    store.pair({ table: "policy", column: "state", was: "region" })
    useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
    await finish({ environment: "dev", checked_at: 1700000000000, tables: TABLES })
    expect(useStoreCompareStore.getState()).toMatchObject({ environment: null, environments: {}, renames: [] })
    store.pair({ table: "policy", column: "state", was: "region" })
    useWorkbenchFormStore.setState((state) => ({ reads: state.reads + 1 }))
    expect(useStoreCompareStore.getState().renames).toEqual([])
    store.choose("dev", false)
    useWorkbenchStore.setState({ environments: ["test"] })
    expect(useStoreCompareStore.getState().environment).toBeNull()
  })

  it("does not allow rename decisions or review export in Preview", async () => {
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")
    openField("state")
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(screen.getByRole("combobox", { name: "Database column for policy.state" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Export SQL" }))
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
  })
})
