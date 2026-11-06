import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
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

const OFFLINE = {
  branch: "main",
  environments: [
    { name: "test", provider: "postgresql", sql: "-- offline test SQL" },
    { name: "prod", provider: "sqlserver", sql: "-- offline prod SQL" },
  ],
}
const LIVE: WorkbenchStoreCompareResponse = {
  differences: [{ table: "policy", column: "territory", kind: "renamed", message: "the database names it region" }],
  extras: [], renames: [], matches: false, sql: "-- from the prod database", provider: "sqlserver",
  changes: [{ kind: "rename column", table: "policy", column: "territory", was: "region", disposition: "sql", description: "Rename region to territory, keeping existing values.", quotes: 3 }],
  manual: [], blocked: [], requires_maintenance: true,
}

function live(answer = LIVE) {
  const form = useWorkbenchFormStore.getState().form!
  const read: CatalogueRead = { status: "read", tables: [], checkedAt: Date.UTC(2026, 10, 6, 10, 42) }
  const renames = answer.renames
  useStoreCompareStore.setState({ environment: "prod", renames, environments: { prod: { read, compared: { status: "compared", form, renames, read, answer } } } })
}

describe("StoreSqlWindow", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["test", "prod"] })
    useWorkbenchViewStore.setState({ mode: "build" })
    useStoreCompareStore.getState().reset()
    vi.mocked(fetchStoreSql).mockResolvedValue(OFFLINE)
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, tables: [] }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(LIVE)
    vi.mocked(downloadTextFile).mockReturnValue(true)
    vi.mocked(writeClipboardText).mockResolvedValue(undefined)
  })
  afterEach(() => { cleanup(); vi.resetAllMocks(); useStoreCompareStore.getState().reset() })

  it("opens the selected live environment and its readable plan without requesting offline SQL", async () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("combobox", { name: "Environment" })).toHaveValue("prod")
    expect(screen.getByTestId("store-sql")).toHaveTextContent(LIVE.sql)
    expect(screen.getByText("Working schema · Saved revision rev-1")).toBeInTheDocument()
    expect(within(screen.getByRole("region", { name: "SQL changes" })).getByText(/keeping existing values/)).toBeInTheDocument()
    expect(screen.getByText("3 quotes with values")).toBeInTheDocument()
    expect(screen.getByText(/Coordinate these changes with the app release/)).toBeInTheDocument()
    expect(fetchStoreSql).not.toHaveBeenCalled()

    fireEvent.change(screen.getByRole("combobox", { name: "Environment" }), { target: { value: "test" } })
    await waitFor(() => expect(fetchStoreCatalogue).toHaveBeenCalledWith("test"))
    expect(useStoreCompareStore.getState().environment).toBe("test")
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
    await act(async () => finish({ ...LIVE, sql: "-- newest SQL" }))
    expect(screen.getByTestId("store-sql")).toHaveTextContent("-- newest SQL")
  })

  it("keeps a failed live check explicit and verifies with a fresh read after the owner runs SQL", async () => {
    useStoreCompareStore.setState({ environment: "prod", environments: { prod: { read: { status: "failed", message: "Check connections.toml [prod]." }, compared: { status: "none" } } } })
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...LIVE, changes: [], differences: [], matches: true, requires_maintenance: false })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent("Check connections.toml")
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(fetchStoreSql).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Check prod again" }))
    expect(await screen.findByText("Compatible with this schema.")).toBeInTheDocument()
    expect(fetchStoreCatalogue).toHaveBeenCalledWith("prod")
  })

  it("offers offline sources explicitly and keeps their environment selection in the pane's store", async () => {
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(await screen.findByTestId("store-sql")).toHaveTextContent(OFFLINE.environments[0].sql)
    expect(screen.getByText(/Offline SQL · Not checked/)).toHaveTextContent("Starts from main.")
    fireEvent.change(screen.getByRole("combobox", { name: "Environment" }), { target: { value: "prod" } })
    expect(screen.getByTestId("store-sql")).toHaveTextContent(OFFLINE.environments[1].sql)
    expect(useStoreCompareStore.getState().environment).toBe("prod")
    expect(fetchStoreCatalogue).not.toHaveBeenCalled()

    vi.mocked(fetchStoreSql).mockResolvedValue({ ...OFFLINE, branch: null })
    fireEvent.change(screen.getByRole("combobox", { name: "Compare from" }), { target: { value: "none" } })
    await waitFor(() => expect(fetchStoreSql).toHaveBeenLastCalledWith(useWorkbenchFormStore.getState().form, "none"))
    expect(await screen.findByText(/Starts from a new database/)).toBeInTheDocument()
  })

  it("downloads and copies the reviewed SQL without marking the environment compatible", async () => {
    live()
    render(<StoreSqlWindow onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    expect(downloadTextFile).toHaveBeenCalledWith(LIVE.sql, "prod-database-schema.sql", "application/sql")
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    expect(writeClipboardText).toHaveBeenCalledWith(LIVE.sql)
    expect(await screen.findByText(/SQL copied/)).toHaveTextContent("then check again")
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

  it("blocks export for unresolved renames and exposes manual and retained work", () => {
    live({ ...LIVE, blocked: ["Remove or update the rename choice before exporting SQL."], manual: ["Fix the primary key."], changes: [
      ...LIVE.changes,
      { kind: "retype column", table: "policy", column: "year", was: null, disposition: "manual", description: "SQLite requires a rebuild.", quotes: 2 },
      { kind: "keep column", table: "pricing", column: "old", was: null, disposition: "retained", description: "Later saves clear its values.", quotes: 1 },
    ] })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(screen.getByRole("alert")).toHaveTextContent("Remove or update")
    expect(screen.getByRole("region", { name: "Manual work" })).toHaveTextContent("SQLite requires a rebuild")
    expect(screen.getByRole("region", { name: "Manual work" })).toHaveTextContent("Fix the primary key")
    expect(screen.getByRole("region", { name: "Retained in database" })).toHaveTextContent("Later saves clear")
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
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

  it("reports an offline failure and no declared environments", async () => {
    vi.mocked(fetchStoreSql).mockRejectedValue(new Error("The default branch form could not be read"))
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read")
    cleanup()
    useStoreCompareStore.getState().reset()
    useWorkbenchStore.setState({ environments: [] })
    vi.mocked(fetchStoreSql).mockResolvedValue({ branch: null, environments: [] })
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(await screen.findByText("No hosted environments are declared.")).toBeInTheDocument()
  })

  it("discards an offline answer from a previous document even if the form is unchanged", async () => {
    let oldAnswer!: (answer: typeof OFFLINE) => void
    vi.mocked(fetchStoreSql).mockImplementationOnce(() => new Promise((resolve) => { oldAnswer = resolve }))
    render(<StoreSqlWindow onClose={vi.fn()} />)
    act(() => useDocumentStatusStore.setState((state) => ({ executionGeneration: state.executionGeneration + 1 })))
    await screen.findByTestId("store-sql")
    await act(async () => oldAnswer({ ...OFFLINE, environments: [{ ...OFFLINE.environments[0], sql: "-- stale branch SQL" }] }))
    expect(screen.getByTestId("store-sql")).toHaveTextContent(OFFLINE.environments[0].sql)
    expect(screen.getByTestId("store-sql")).not.toHaveTextContent("stale branch")
  })

  it("invalidates offline SQL when the configured environments change", async () => {
    render(<StoreSqlWindow onClose={vi.fn()} />)
    expect(await screen.findByTestId("store-sql")).toHaveTextContent(OFFLINE.environments[0].sql)
    let finish!: (answer: typeof OFFLINE) => void
    vi.mocked(fetchStoreSql).mockImplementation(() => new Promise((resolve) => { finish = resolve }))

    act(() => useWorkbenchStore.setState({ environments: ["staging"] }))
    expect(screen.queryByTestId("store-sql")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Download SQL" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "Environment" })).toHaveValue("staging")

    await act(async () => finish({ branch: "main", environments: [{ name: "staging", provider: "postgresql", sql: "-- current staging SQL" }] }))
    fireEvent.click(screen.getByRole("button", { name: "Download SQL" }))
    expect(downloadTextFile).toHaveBeenCalledWith("-- current staging SQL", "staging-branch-schema.sql", "application/sql")
    expect(screen.queryByRole("option", { name: "test" })).not.toBeInTheDocument()
  })
})
