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
import { sheetForm } from "./fixtures"

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
const option = (name: string) => screen.getByRole("option", { name: new RegExp(`^${name}( ·|$)`) })
const openField = (name: string) => {
  const button = within(screen.getByTestId(`schema-column-${name}`)).getByRole("button", { name: `Database status of ${name}` })
  if (button.getAttribute("aria-expanded") !== "true") fireEvent.click(button)
}

describe("Compare", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["dev", "prod"] })
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
    expect(option("dev")).toHaveTextContent("Not checked")
    expect(option("prod")).toHaveTextContent("Not checked")

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
    expect(option("dev")).toHaveTextContent("Compatible")
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Matches")
  })

  it("explains a missing table in its fields' database details", async () => {
    open()
    choose("dev")
    await waitFor(() => expect(screen.getByTestId("database-mark-equipment-item")).toHaveTextContent("New column"))
    openField("item")
    expect(screen.getByRole("region", { name: "Database details for equipment.item" })).toHaveTextContent("the database has no equipment")
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
    fireEvent.click(screen.getByRole("button", { name: "Check dev again" }))
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

  it("reads the database again on Read again, keeping the pairings", async () => {
    // The comparison takes every pairing it is sent.
    vi.mocked(fetchStoreCompare).mockImplementation((_form, _environment, _tables, renames) => Promise.resolve({ ...COMPARED, renames }))
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-state")
    useStoreCompareStore.getState().pair({ table: "policy", column: "state", was: "region" })

    fireEvent.click(screen.getByRole("button", { name: "Check dev again" }))

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
    expect(fetchStoreCompare).not.toHaveBeenCalled()
    expect(screen.getByTestId("database-mark-policy-state")).toHaveTextContent("Check failed")
  })

  it("says why the form could not be compared, the database read", async () => {
    vi.mocked(fetchStoreCompare).mockRejectedValue(new ApiError("A column name is refused.", 422))
    open()
    choose("dev")

    expect(await screen.findByRole("alert")).toHaveTextContent("A column name is refused.")
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
    expect(screen.queryByRole("button", { name: /Check .* again/ })).not.toBeInTheDocument()
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
    expect(option("prod")).toHaveTextContent("Changes needed")
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
    await waitFor(() => expect(option("dev")).toHaveTextContent("Changes needed"))
    expect(option("prod")).toHaveTextContent("Checking")
    await act(async () => finishProd({ environment: "prod", tables: TABLES }))
    choose("prod")
    expect(await screen.findByTestId("database-mark-policy-state")).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveAttribute("title", expect.stringContaining("Database checked"))

    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "prod", tables: [] })
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _environment, tables) => tables.length === 0
      ? { ...COMPARED, differences: [], matches: true } : COMPARED)
    fireEvent.click(screen.getByRole("button", { name: "Check prod again" }))
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Compatible"))
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
