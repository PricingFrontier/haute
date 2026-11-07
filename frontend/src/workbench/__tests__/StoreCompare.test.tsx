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
    columns: [{ name: "region", kind: "string", type: "TEXT", nullable: true, collation: null, quotes: 3 }],
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
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, tables: TABLES }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(COMPARED)
  })

  afterEach(() => {
    cleanup()
    vi.resetAllMocks()
    useStoreCompareStore.getState().reset()
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
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", tables: TABLES })
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await waitFor(() => expect(table.getByText("Database details")).toBeInTheDocument())
    choose("")
    expect(table.queryByText("Database details")).toBeNull()
    expect(table.queryByTestId("database-mark-quote_details")).toBeNull()
  })

  it("uses Local and name-only database colours, clearing a match throughout refresh", async () => {
    let finishRead!: (value: { environment: string; tables: WorkbenchCatalogueTable[] }) => void
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
    expect(refresh).toBeDisabled()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()

    choose("dev")
    expect(database).toHaveStyle({ color: "var(--danger-text)" })
    expect(option("dev")).toHaveAccessibleDescription("Checking")
    expect(refresh).toBeDisabled()
    await act(async () => finishRead({ environment: "dev", tables: TABLES }))
    expect(database).toHaveStyle({ color: "var(--caution)" })
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    expect(option("dev")).toHaveAccessibleDescription("Comparing")
    expect(refresh).toBeEnabled()
    await act(async () => finishCompare({ ...COMPARED, differences: [], extras: [], changes: [], matches: true }))
    expect(database).toHaveStyle({ color: "var(--success)" })
    expect(option("dev")).toHaveStyle({ color: "var(--success)" })
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })

    fireEvent.click(refresh)
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(2)
    expect(database).toHaveStyle({ color: "var(--danger-text)" })
    expect(option("dev")).toHaveStyle({ color: "var(--danger-text)" })
    expect(refresh).toBeDisabled()
    await act(async () => finishRead({ environment: "dev", tables: TABLES }))
    expect(database).toHaveStyle({ color: "var(--caution)" })
    await act(async () => finishCompare(COMPARED))
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
    expect(within(row).getByRole("textbox", { name: "Label of state" })).toBeInTheDocument()
    const source = within(row).getByRole("combobox", { name: "Database column for policy.state" })
    expect(source).toHaveValue("")
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, _tables, renames) => ({ ...COMPARED, renames }))
    fireEvent.change(source, { target: { value: "region" } })
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, [{ table: "policy", column: "state", was: "region" }]))
    expect(within(row).getByRole("combobox", { name: "Database column for policy.state" })).toHaveValue("region")
    fireEvent.change(within(row).getByRole("combobox", { name: "Database column for policy.state" }), { target: { value: "" } })
    await waitFor(() => expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, []))
  })

  it("reads the chosen environment's database and marks each table and column of the form where it differs", async () => {
    open()
    expect(option("dev")).toHaveStyle({ color: "var(--danger-text)" })
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })

    choose("dev")

    expect(await screen.findByTestId("database-mark-policy-state")).toHaveTextContent("New column")
    expect(fetchStoreCatalogue).toHaveBeenCalledWith("dev")
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
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-equipment-item")).toHaveTextContent("New column"))
    openField("item")
    expect(screen.getByRole("region", { name: "Database details for equipment.item" })).toHaveTextContent("the database has no equipment")
  })

  it("explains a separate manual repair alongside a planned field type change", async () => {
    const repair = "policy.year takes no null, so an empty year could not be saved"
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED,
      differences: [...COMPARED.differences, { table: "policy", column: "year", kind: "null", message: repair }],
      changes: [{ kind: "retype column", table: "policy", column: "year", was: null, disposition: "sql", description: "Change TEXT to Integer. Review value conversion before running.", quotes: 3 }],
      manual: [repair],
    })
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-policy-year")).toHaveTextContent("Needs attention"))
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    openField("year")
    const details = screen.getByRole("region", { name: "Database details for policy.year" })
    expect(details).toHaveTextContent("Change TEXT to Integer")
    expect(details).toHaveTextContent(repair)
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
    let finish!: (value: { environment: string; tables: WorkbenchCatalogueTable[] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(within(field).getByRole("combobox")).toBeDisabled()
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Checking")
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [], changes: [], extras: [], matches: true })
    await act(async () => finish({ environment: "dev", tables: [{ ...TABLES[0], columns: [{ ...TABLES[0].columns[0], name: "state" }] }] }))
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
    let resolveRead!: (value: { environment: string; tables: WorkbenchCatalogueTable[] }) => void
    let rejectRead!: (error: unknown) => void
    vi.mocked(fetchStoreCatalogue)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRead = resolve }))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRead = reject }))

    store.choose("dev")
    store.choose("prod")
    resolveRead({ environment: "dev", tables: TABLES })
    store.reset()
    rejectRead(new ApiError("Stale failure", 503))
    await Promise.resolve()
    expect(useStoreCompareStore.getState().environments).toEqual({})

    // A rejected pairing stays explicit for other environments; an older failure cannot land.
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", tables: TABLES })
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
    let finishProd!: (answer: { environment: string; tables: WorkbenchCatalogueTable[] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation((environment) => environment === "prod"
      ? new Promise((resolve) => { finishProd = resolve })
      : Promise.resolve({ environment, tables: TABLES }))
    open()
    act(() => useStoreCompareStore.getState().checkAll(["dev", "prod"]))
    await waitFor(() => expect(option("dev")).toHaveAccessibleDescription("Changes needed"))
    expect(option("dev")).toHaveStyle({ color: "var(--caution)" })
    expect(option("prod")).toHaveStyle({ color: "var(--danger-text)" })
    await act(async () => finishProd({ environment: "prod", tables: TABLES }))
    choose("prod")
    expect(await screen.findByTestId("database-mark-policy-state")).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveAttribute("title", expect.stringContaining("Database checked"))

    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "prod", tables: [] })
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, tables) => tables.length === 0
      ? { ...COMPARED, differences: [], matches: true } : COMPARED)
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Database" })).toHaveAccessibleDescription("Compatible"))
    expect(within(screen.getByRole("region", { name: "Table policy" })).getByText("Database details · 2 retained fields").closest("details")).not.toHaveAttribute("open")
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(3)
  })

  it("forgets session choices and late replies on document, form and environment changes", async () => {
    let finish!: (answer: { environment: string; tables: WorkbenchCatalogueTable[] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const store = useStoreCompareStore.getState()
    store.choose("dev")
    store.pair({ table: "policy", column: "state", was: "region" })
    useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
    await finish({ environment: "dev", tables: TABLES })
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
    expect(screen.getByRole("button", { name: "Export SQL" })).toBeDisabled()
  })
})
