import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
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

const LIVE: WorkbenchStoreCompareResponse = {
  differences: [{ table: "policy", column: "territory", kind: "renamed", message: "the database names it region" }],
  extras: [], renames: [], matches: false, sql: "-- from the prod database", provider: "sqlserver",
  changes: [{ kind: "rename column", table: "policy", column: "territory", was: "region", disposition: "sql", description: "Rename region to territory, keeping existing values.", quotes: 3 }],
  manual: [], blocked: [], requires_maintenance: true,
}

function live(answer = LIVE, renames = answer.renames, environment = "prod") {
  const form = useWorkbenchFormStore.getState().form!
  const read: CatalogueRead = { status: "read", tables: [], checkedAt: Date.UTC(2026, 10, 6, 10, 42) }
  useStoreCompareStore.setState({ environment, renames, environments: { [environment]: { read, compared: { status: "compared", form, renames, read, answer } } } })
}

describe("StoreSqlWindow", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["test", "prod"] })
    useWorkbenchViewStore.setState({ mode: "build" })
    useStoreCompareStore.getState().reset()
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, tables: [] }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(LIVE)
    vi.mocked(downloadTextFile).mockReturnValue(true)
    vi.mocked(writeClipboardText).mockResolvedValue(undefined)
  })
  afterEach(() => { cleanup(); vi.resetAllMocks(); useStoreCompareStore.getState().reset() })

  it("shows just the selected query immediately with no selection, refresh or options", () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("heading", { name: "Export SQL" })).toBeInTheDocument()
    expect(screen.getByTestId("store-sql")).toBeVisible()
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
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    expect(downloadTextFile).toHaveBeenCalledWith(LIVE.sql, "prod-database-schema.sql", "application/sql")
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    expect(writeClipboardText).toHaveBeenCalledWith(LIVE.sql)
    expect(await screen.findByText("SQL copied.")).toBeInTheDocument()
    expect(screen.queryByText(/Compatible with this schema/)).not.toBeInTheDocument()
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()
  })

  it("reports copy and download failures", async () => {
    live()
    vi.mocked(writeClipboardText).mockRejectedValue(new Error("Clipboard permission denied"))
    vi.mocked(downloadTextFile).mockReturnValue(false)
    render(<StoreSqlWindow onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Clipboard permission denied")
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("SQL download is unavailable")
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

  it("follows the database chosen on the pane without choosing or fetching one itself", () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByText("· prod")).toBeInTheDocument()
    act(() => live({ ...LIVE, sql: "-- test query" }, [], "test"))
    expect(screen.getByTestId("store-sql")).toHaveTextContent("-- test query")
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    expect(downloadTextFile).toHaveBeenCalledWith("-- test query", "test-database-schema.sql", "application/sql")
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

  it("blocks unresolved renames and points manual work back to the schema pane", () => {
    live({ ...LIVE, blocked: ["Unresolved rename"], manual: ["Fix the primary key."] })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent("Resolve the rename choices in Database changes")
    expect(screen.getByText(/This SQL completes only part/)).toHaveTextContent("schema pane")
    expect(screen.queryByTestId("store-plan-review")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
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
