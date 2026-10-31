/**
 * The schema pane's Compare (specs/workbench): an environment chosen reads its database once,
 * the form is compared with what was read as it changes, each difference marked on the form's
 * table or column, the extras listed with their quotes and paired as renames, a failure said in
 * its place, and the comparison closed with the pane.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"

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
    { table: "policy", column: "policy.state", kind: "missing", message: "the database has no state" },
    { table: "policy", column: "policy.year", kind: "type", message: "the database holds it as TEXT, not BIGINT (3 quotes hold values in it)" },
    { table: "equipment", column: null, kind: "missing", message: "the database has no equipment" },
    { table: null, column: null, kind: "missing", message: "the database has no haute_activity" },
  ],
  extras: [{ table: "policy", column: "region", table_id: "policy", quotes: 3 }],
  renames: [],
  matches: false,
  sql: "BEGIN;\nALTER TABLE policy ADD COLUMN state TEXT;\nCOMMIT;\n",
  branch: null,
}

const open = () => {
  useWorkbenchViewStore.setState({ mode: "build", schemaOpen: true })
  return render(<SchemaPane />)
}

const choose = (environment: string) => fireEvent.change(screen.getByRole("combobox", { name: "Compare with" }), { target: { value: environment } })

describe("Compare", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["dev", "prod"] })
    useStoreCompareStore.getState().reset()
    vi.mocked(fetchStoreCatalogue).mockResolvedValue({ environment: "dev", tables: TABLES })
    vi.mocked(fetchStoreCompare).mockResolvedValue(COMPARED)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("reads the chosen environment's database and marks each table and column of the form where it differs", async () => {
    open()
    const environments = within(screen.getByRole("combobox", { name: "Compare with" })).getAllByRole("option")
    expect(environments.map((each) => each.textContent)).toEqual(["Compare", "dev", "prod"])

    choose("dev")

    expect(await screen.findByTestId("database-mark-policy-policy.state")).toHaveTextContent("missing")
    expect(fetchStoreCatalogue).toHaveBeenCalledWith("dev")
    expect(fetchStoreCompare).toHaveBeenCalledWith(useWorkbenchFormStore.getState().form, "dev", TABLES, [])
    const typed = screen.getByTestId("database-mark-policy-policy.year")
    expect(typed).toHaveTextContent("type")
    expect(typed).toHaveAttribute("title", expect.stringContaining("3 quotes hold values in it"))
    expect(screen.getByTestId("database-mark-equipment")).toHaveTextContent("missing")
    expect(screen.getByTestId("store-compare")).toHaveTextContent("the database has no haute_activity")
    const extras = screen.getByRole("region", { name: "Not in the form" })
    expect(extras).toHaveTextContent("policy.region")
    expect(extras).toHaveTextContent("3 quotes")
  })

  it("says when the database matches the form", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...COMPARED, differences: [], extras: [], matches: true })
    open()
    choose("dev")

    expect(await screen.findByText("The dev database matches the form.")).toBeInTheDocument()
    expect(screen.queryByTestId("database-mark-policy-policy.state")).not.toBeInTheDocument()
  })

  it("compares the form again as it changes without reading the database again", async () => {
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-policy.state")

    useWorkbenchFormStore.getState().change((form) => updateSchemaTable(form, "equipment", { name: "kit" }))

    await waitFor(() => expect(fetchStoreCompare).toHaveBeenCalledTimes(2))
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(1)
    expect(vi.mocked(fetchStoreCompare).mock.calls[1][0]).toBe(useWorkbenchFormStore.getState().form)
  })

  it("pairs a column the database holds with one the form lacks as a rename, and unpairs it", async () => {
    open()
    choose("dev")
    const pairing = await screen.findByRole("combobox", { name: "Rename of policy.region" })
    vi.mocked(fetchStoreCompare).mockResolvedValue({
      ...COMPARED,
      extras: [],
      renames: [{ table: "policy", column: "policy.state", was: "region" }],
    })

    fireEvent.change(pairing, { target: { value: "policy.state" } })

    await waitFor(() =>
      expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, [
        { table: "policy", column: "policy.state", was: "region" },
      ]),
    )
    const renamed = await screen.findByRole("region", { name: "Renamed" })
    expect(renamed).toHaveTextContent("region → state")

    vi.mocked(fetchStoreCompare).mockResolvedValue(COMPARED)
    fireEvent.click(within(renamed).getByRole("button", { name: "Unpair region" }))

    await waitFor(() => expect(fetchStoreCompare).toHaveBeenLastCalledWith(expect.anything(), "dev", TABLES, []))
  })

  it("reads the database again on Read again, keeping the pairings", async () => {
    // The comparison takes every pairing it is sent.
    vi.mocked(fetchStoreCompare).mockImplementation((_form, _environment, _tables, renames) => Promise.resolve({ ...COMPARED, renames }))
    open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-policy.state")
    useStoreCompareStore.getState().pair({ table: "policy", column: "policy.state", was: "region" })

    fireEvent.click(screen.getByRole("button", { name: "Read the dev database again" }))

    await waitFor(() => expect(fetchStoreCatalogue).toHaveBeenCalledTimes(2))
    expect(useStoreCompareStore.getState().renames).toEqual([{ table: "policy", column: "policy.state", was: "region" }])
  })

  it("says why the database could not be read, marking nothing", async () => {
    vi.mocked(fetchStoreCatalogue).mockRejectedValue(
      new ApiError("connections.toml has no [prod] connection: add one saying where the prod database is and how you sign in to it.", 409),
    )
    open()
    choose("prod")

    expect(await screen.findByRole("alert")).toHaveTextContent("connections.toml has no [prod] connection")
    expect(fetchStoreCompare).not.toHaveBeenCalled()
    expect(screen.queryByTestId("database-mark-policy-policy.state")).not.toBeInTheDocument()
  })

  it("closes the comparison with the pane", async () => {
    const shown = open()
    choose("dev")
    await screen.findByTestId("database-mark-policy-policy.state")

    shown.unmount()

    expect(useStoreCompareStore.getState()).toMatchObject({ environment: null, read: { status: "idle" }, compared: { status: "none" } })
  })
})
