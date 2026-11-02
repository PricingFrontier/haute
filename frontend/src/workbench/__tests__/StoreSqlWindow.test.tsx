/**
 * The tables' SQL (specs/workbench): the SQL the server writes for each hosted environment the
 * project declares, a tab each, from the form the default branch holds or from none, read only
 * with Copy; a failure said in its place and no environment declared said so.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchStoreCompare: vi.fn(),
  fetchStoreSql: vi.fn(),
}))

import { ApiError } from "../../api/client"
import type { WorkbenchStoreCompareResponse } from "../../api/types"
import { fetchStoreCompare, fetchStoreSql } from "../../api/workbench"
import useStoreCompareStore from "../../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import StoreSqlWindow from "../StoreSqlWindow"
import { sheetForm } from "./fixtures"

const ANSWER = {
  branch: "main",
  environments: [
    { name: "test", provider: "postgresql", sql: "BEGIN;\nALTER TABLE policy ADD COLUMN seats BIGINT;\nCOMMIT;\n" },
    { name: "prod", provider: "sqlserver", sql: "SET XACT_ABORT ON;\nALTER TABLE policy ADD seats BIGINT NULL;\n" },
  ],
}

describe("StoreSqlWindow", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    useStoreCompareStore.getState().reset()
  })

  /** A comparison of the prod database, of the form the view holds and of `renames`. */
  const comparedProd = (renames: { table: string; column: string; was: string }[]) => {
    const form = useWorkbenchFormStore.getState().form
    if (form === null) throw new Error("no form")
    const answer = { differences: [], extras: [], renames, matches: false, sql: "-- from the prod database", branch: null }
    useStoreCompareStore.setState({ environment: "prod", renames, compared: { status: "compared", form, renames, answer } })
  }

  it("starts from the tables Compare read, on its environment's tab, the other starts still offered", async () => {
    vi.mocked(fetchStoreSql).mockResolvedValue(ANSWER)
    comparedProd([])
    render(<StoreSqlWindow onClose={vi.fn()} />)

    expect(await screen.findByTestId("store-sql")).toHaveTextContent("ALTER TABLE policy ADD COLUMN seats BIGINT;")
    fireEvent.click(screen.getByRole("tab", { name: /prod/ }))
    expect(screen.getByTestId("store-sql")).toHaveTextContent("-- from the prod database")
    const start = screen.getByRole("combobox")
    expect(start).toHaveValue("database")
    expect(screen.getByRole("option", { name: "The prod database" })).toBeInTheDocument()

    fireEvent.change(start, { target: { value: "branch" } })
    expect(screen.getByTestId("store-sql")).toHaveTextContent("ALTER TABLE policy ADD seats BIGINT NULL;")
  })

  it("offers no SQL from the database while a pairing made since is being compared", async () => {
    vi.mocked(fetchStoreSql).mockResolvedValue(ANSWER)
    let finishCompare: ((answer: WorkbenchStoreCompareResponse) => void) | undefined
    vi.mocked(fetchStoreCompare).mockImplementation(
      () =>
        new Promise((resolve) => {
          finishCompare = resolve
        }),
    )
    comparedProd([])
    useStoreCompareStore.setState({ read: { status: "read", tables: [] } })
    const pairing = { table: "policy", column: "policy.state", was: "region" }
    // The analyst pairs a column and the replacement comparison is still in flight.
    useStoreCompareStore.getState().pair(pairing)
    useStoreCompareStore.getState().compare(useWorkbenchFormStore.getState().form!)
    render(<StoreSqlWindow onClose={vi.fn()} />)

    await screen.findByTestId("store-sql")
    fireEvent.click(screen.getByRole("tab", { name: /prod/ }))
    expect(screen.getByTestId("store-sql")).not.toHaveTextContent("-- from the prod database")
    expect(screen.queryByRole("option", { name: "The prod database" })).not.toBeInTheDocument()

    finishCompare!({
      differences: [],
      extras: [],
      renames: [pairing],
      matches: false,
      sql: "-- renamed in prod",
      branch: null,
    })
    expect(await screen.findByRole("option", { name: "The prod database" })).toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "database" } })
    expect(screen.getByTestId("store-sql")).toHaveTextContent("-- renamed in prod")
  })

  it("shows each environment's SQL for the form the view holds, from the default branch's form", async () => {
    vi.mocked(fetchStoreSql).mockResolvedValue(ANSWER)

    render(<StoreSqlWindow onClose={vi.fn()} />)

    expect(await screen.findByTestId("store-sql")).toHaveTextContent("ALTER TABLE policy ADD COLUMN seats BIGINT;")
    expect(fetchStoreSql).toHaveBeenCalledWith(useWorkbenchFormStore.getState().form, "branch")
    expect(screen.getByRole("tab", { name: /test/ })).toHaveAttribute("aria-selected", "true")
    fireEvent.click(screen.getByRole("tab", { name: /prod/ }))
    expect(screen.getByTestId("store-sql")).toHaveTextContent("ALTER TABLE policy ADD seats BIGINT NULL;")
  })

  it("starts from none when asked, the branch still offered", async () => {
    vi.mocked(fetchStoreSql).mockResolvedValue(ANSWER)
    render(<StoreSqlWindow onClose={vi.fn()} />)
    const start = await screen.findByRole("combobox")
    expect(start).toHaveValue("branch")

    vi.mocked(fetchStoreSql).mockResolvedValue({ ...ANSWER, branch: null })
    fireEvent.change(start, { target: { value: "none" } })

    await waitFor(() => expect(fetchStoreSql).toHaveBeenLastCalledWith(useWorkbenchFormStore.getState().form, "none"))
    expect(await screen.findByRole("combobox")).toHaveValue("none")
    expect(screen.getByRole("option", { name: "main" })).toBeInTheDocument()
  })

  it("offers none alone while the default branch holds no form", async () => {
    vi.mocked(fetchStoreSql).mockResolvedValue({ ...ANSWER, branch: null })

    render(<StoreSqlWindow onClose={vi.fn()} />)

    expect(await screen.findByRole("combobox")).toHaveValue("none")
    expect(screen.queryByRole("option", { name: "main" })).toBeNull()
  })

  it("copies the SQL showing", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } })
    vi.mocked(fetchStoreSql).mockResolvedValue(ANSWER)
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await screen.findByTestId("store-sql")

    fireEvent.click(screen.getByRole("button", { name: "Copy" }))

    expect(writeText).toHaveBeenCalledWith(ANSWER.environments[0].sql)
  })

  it("says why the SQL could not be written, and when no environment is declared", async () => {
    vi.mocked(fetchStoreSql).mockRejectedValue(new ApiError("HTTP 409", 409, "main:rating/workbench/index.json could not be read"))
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read")
    cleanup()

    vi.mocked(fetchStoreSql).mockResolvedValue({ branch: null, environments: [] })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(await screen.findByRole("status")).toHaveTextContent("No hosted environments are declared.")
  })
})
