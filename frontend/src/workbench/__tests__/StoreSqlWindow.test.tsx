import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchStoreCatalogue: vi.fn(), fetchStoreCompare: vi.fn(), fetchStoreSql: vi.fn(),
}))
vi.mock("../../panels/editors/shared/tableClipboard", () => ({ downloadTextFile: vi.fn(), writeClipboardText: vi.fn() }))

import type { WorkbenchStoreCompareResponse } from "../../api/types"
import { fetchStoreCatalogue, fetchStoreCompare, fetchStoreSql } from "../../api/workbench"
import { downloadTextFile, writeClipboardText } from "../../panels/editors/shared/tableClipboard"
import useStoreCompareStore, { type CatalogueRead } from "../../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import StoreSqlWindow from "../StoreSqlWindow"
import { sheetForm } from "./fixtures"

const copied = vi.fn<(text: string) => void>()

const LIVE: WorkbenchStoreCompareResponse = {
  differences: [{ table: "policy", column: "territory", kind: "renamed", message: "the database names it region" }],
  extras: [], renames: [], matches: false, sql: "-- from the prod database",
  changes: [{ kind: "add column", table: "policy", column: "territory", disposition: "sql", description: "Add territory as Text." }],
  manual: [], blocked: [],
}

function live(answer = LIVE, renames = answer.renames, environment = "prod") {
  vi.mocked(fetchStoreCompare).mockResolvedValue(answer)
  const form = useWorkbenchFormStore.getState().form!
  const read: CatalogueRead = { status: "read", target: "a".repeat(64), tables: [], checkedAt: Date.UTC(2026, 10, 6, 10, 42) }
  useStoreCompareStore.setState({ environment, renames, environments: { [environment]: { read, compared: { status: "compared", form, renames, read, answer } } } })
}

describe("StoreSqlWindow", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["test", "prod"] })
    useWorkbenchViewStore.setState({ mode: "build" })
    useStoreCompareStore.getState().reset()
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, target: "a".repeat(64), checked_at: 1700000000000, tables: [] }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(LIVE)
    vi.mocked(downloadTextFile).mockReturnValue(true)
    vi.mocked(writeClipboardText).mockImplementation(async (text) => { copied(await text) })
  })
  afterEach(() => { cleanup(); vi.resetAllMocks(); useStoreCompareStore.getState().reset() })

  it("validates the connection before showing a reused query and copying it", async () => {
    live()
    let finish!: (answer: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCompare).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.queryByTestId("store-sql")).toBeNull()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    await act(async () => finish(LIVE))
    expect(screen.getByTestId("store-sql")).toHaveTextContent(LIVE.sql)
    vi.mocked(fetchStoreCompare).mockRejectedValueOnce(new Error("Connection changed. Use Refresh."))
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection changed. Use Refresh.")
    expect(screen.queryByTestId("store-sql")).toBeNull()
    expect(copied).not.toHaveBeenCalled()
    expect(downloadTextFile).not.toHaveBeenCalled()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
  })

  it.each(["form", "environment", "preview", "stale"])("does not finish an export after %s changes during validation", async (change) => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await screen.findByTestId("store-sql")
    let finish!: (answer: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCompare).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Download SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    act(() => {
      if (change === "form") useWorkbenchFormStore.getState().change((form) => ({ ...form, name: "Changed" }))
      if (change === "environment") useStoreCompareStore.getState().choose(null)
      if (change === "preview") useWorkbenchViewStore.setState({ mode: "preview" })
      if (change === "stale") useWorkbenchFormStore.setState({ stale: true })
    })
    await act(async () => finish(LIVE))
    expect(downloadTextFile).not.toHaveBeenCalled()
  })

  it("shows just the selected query after validating its connection with no selection, refresh or options", async () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("heading", { name: "Export SQL" })).toBeInTheDocument()
    expect(await screen.findByTestId("store-sql")).toBeVisible()
    expect(screen.getByTestId("store-sql")).toHaveTextContent(LIVE.sql)
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(screen.queryByText("SQL options")).not.toBeInTheDocument()
    expect(screen.queryByText("View SQL")).not.toBeInTheDocument()
    expect(screen.queryByTestId("store-plan-review")).not.toBeInTheDocument()
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual(["Close", "Copy SQL", "Download SQL"])
    expect(screen.getByText("Working schema · Saved")).toBeInTheDocument()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
    expect(fetchStoreSql).not.toHaveBeenCalled()
  })

  it("removes obsolete SQL while edits are compared and never substitutes offline SQL", async () => {
    live()
    let finish!: (answer: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCompare).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    render(<StoreSqlWindow onClose={vi.fn()} />)
    act(() => useWorkbenchFormStore.getState().change((form) => ({ ...form, name: "Changed" })))
    expect(screen.queryByTestId("store-sql")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    expect(screen.getByText("Working schema · Unsaved changes")).toBeInTheDocument()
    expect(fetchStoreSql).not.toHaveBeenCalled()
    act(() => useStoreCompareStore.getState().compare(useWorkbenchFormStore.getState().form!))
    await act(async () => finish({ ...LIVE, sql: "-- newest SQL" }))
    expect(screen.getByTestId("store-sql")).toHaveTextContent("-- newest SQL")
  })

  it("downloads and copies the reviewed SQL without marking the environment compatible", async () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole("button", { name: "Download SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    await waitFor(() => expect(downloadTextFile).toHaveBeenCalledWith(LIVE.sql, "prod-database-schema.sql", "application/sql"))
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    await waitFor(() => expect(copied).toHaveBeenCalledWith(LIVE.sql))
    expect(await screen.findByText("SQL copied.")).toBeInTheDocument()
    expect(screen.queryByText(/Compatible with this schema/)).not.toBeInTheDocument()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
  })

  it.each(["Copy", "Download"])("reports %s success for the newly verified SQL", async (action) => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole("button", { name: `${action} SQL` })).toBeEnabled())
    const sql = "-- newly verified SQL with equivalent indexes in another order"
    vi.mocked(fetchStoreCompare).mockResolvedValueOnce({ ...LIVE, sql })
    fireEvent.click(screen.getByRole("button", { name: `${action} SQL` }))
    expect(await screen.findByRole("status")).toHaveTextContent(action === "Copy" ? "SQL copied." : "SQL downloaded.")
    expect(screen.getByTestId("store-sql")).toHaveTextContent(sql)
    if (action === "Copy") expect(copied).toHaveBeenCalledWith(sql)
    else expect(downloadTextFile).toHaveBeenCalledWith(sql, "prod-database-schema.sql", "application/sql")
  })

  it.each(["Copy", "Download"])("reports %s failure when verification changes the SQL", async (action) => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole("button", { name: `${action} SQL` })).toBeEnabled())
    let finish!: (answer: WorkbenchStoreCompareResponse) => void
    vi.mocked(fetchStoreCompare).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    vi.mocked(writeClipboardText).mockRejectedValue(new Error("Clipboard permission denied"))
    vi.mocked(downloadTextFile).mockReturnValue(false)
    fireEvent.click(screen.getByRole("button", { name: `${action} SQL` }))
    await act(async () => finish({ ...LIVE, sql: "-- newly verified SQL" }))
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(action === "Copy" ? "Clipboard permission denied" : "SQL download is unavailable"))
  })

  it("reports copy and download failures", async () => {
    live()
    vi.mocked(writeClipboardText).mockRejectedValue(new Error("Clipboard permission denied"))
    vi.mocked(downloadTextFile).mockReturnValue(false)
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Clipboard permission denied"))
    await waitFor(() => expect(screen.getByRole("button", { name: "Download SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("SQL download is unavailable"))
  })

  it("prevents export in Preview and when the form changed on disk", () => {
    live()
    useWorkbenchViewStore.setState({ mode: "preview" })
    const view = render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    act(() => {
      useWorkbenchViewStore.setState({ mode: "build" })
      useWorkbenchFormStore.setState({ stale: true })
    })
    expect(screen.getByRole("alert")).toHaveTextContent("Reload")
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    view.unmount()
  })

  it("follows the database chosen on the pane without choosing or fetching one itself", async () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByText("· prod")).toBeInTheDocument()
    act(() => live({ ...LIVE, sql: "-- test query" }, [], "test"))
    expect(await screen.findByTestId("store-sql")).toHaveTextContent("-- test query")
    await waitFor(() => expect(screen.getByRole("button", { name: "Download SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    await waitFor(() => expect(downloadTextFile).toHaveBeenCalledWith("-- test query", "test-database-schema.sql", "application/sql"))
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
    expect(fetchStoreSql).not.toHaveBeenCalled()
  })

  it.each([true, false])("keeps Local unselected and refuses export (databases configured: %s)", (configured) => {
    if (!configured) useWorkbenchStore.setState({ environments: [] })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent(configured ? "Select a database" : "Configure a database")
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(useStoreCompareStore.getState().environment).toBeNull()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
    expect(fetchStoreSql).not.toHaveBeenCalled()
  })

  it.each(["read", "compare"])("shows a failed %s without fetching or offering alternative SQL", (failure) => {
    live()
    const snapshot = useStoreCompareStore.getState().environments.prod
    useStoreCompareStore.setState({ environments: { prod: failure === "read"
      ? { read: { status: "failed", message: "Database unavailable" }, compared: { status: "none" } }
      : { ...snapshot, compared: { status: "failed", form: useWorkbenchFormStore.getState().form!, renames: useStoreCompareStore.getState().renames, read: snapshot.read, message: "Invalid schema" } },
    } })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent(failure === "read" ? "Database unavailable" : "Invalid schema")
    expect(screen.queryByTestId("store-sql")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
    expect(fetchStoreSql).not.toHaveBeenCalled()
  })

  it.each(["Unresolved rename", "Existing key differs"])("exports additions independently of %s", async (blocker) => {
    const sql = "ALTER TABLE policy ADD flood_zone TEXT;"
    live({ ...LIVE, sql, blocked: [blocker] })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeEnabled()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByText(/This SQL adds tables and columns only/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Run through your database owner/)).not.toBeInTheDocument()
    expect(screen.getByTestId("store-sql")).toHaveTextContent(sql)
    expect(screen.queryByTestId("store-plan-review")).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    await waitFor(() => expect(copied).toHaveBeenCalledWith(sql))
    expect(await screen.findByText("SQL copied.")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole("button", { name: "Download SQL" })).toBeEnabled())
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    await waitFor(() => expect(downloadTextFile).toHaveBeenCalledWith(sql, "prod-database-schema.sql", "application/sql"))
    expect(useStoreCompareStore.getState().environments.prod.compared).toMatchObject({ answer: { matches: false, blocked: [blocker] } })
  })

  it.each(["document", "environments"])("discards the query after %s changes without selecting another database", (change) => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    act(() => {
      if (change === "document") useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 }))
      else useWorkbenchStore.setState({ environments: ["staging"] })
    })
    expect(screen.queryByTestId("store-sql")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    expect(useStoreCompareStore.getState().environment).toBeNull()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
    expect(fetchStoreSql).not.toHaveBeenCalled()
  })

})
