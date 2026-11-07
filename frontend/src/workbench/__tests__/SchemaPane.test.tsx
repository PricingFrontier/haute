/**
 * The schema pane owns database selection, review and refresh; export is a query view.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchStoreCatalogue: vi.fn(), fetchStoreCompare: vi.fn(), fetchStoreSql: vi.fn(),
}))
vi.mock("../../panels/editors/shared/tableClipboard", () => ({ downloadTextFile: vi.fn(), writeClipboardText: vi.fn() }))

import type { WorkbenchStoreCompareResponse } from "../../api/types"
import { fetchStoreCatalogue, fetchStoreCompare, fetchStoreSql } from "../../api/workbench"
import { writeClipboardText } from "../../panels/editors/shared/tableClipboard"
import useStoreCompareStore from "../../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import SchemaPane from "../SchemaPane"
import { sheetForm } from "./fixtures"

const ANSWER: WorkbenchStoreCompareResponse = {
  differences: [], extras: [], renames: [], matches: true, sql: "-- dev query", provider: "sqlite",
  changes: [], manual: [], blocked: [], requires_maintenance: false,
}

const selectDev = () => fireEvent.change(screen.getByRole("combobox", { name: "Database" }), { target: { value: "dev" } })

describe("SchemaPane", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["dev", "prod"] })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: true, schemaSearch: "" })
    useStoreCompareStore.getState().reset()
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, checked_at: 1700000000000, tables: [] }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(ANSWER)
    vi.mocked(writeClipboardText).mockResolvedValue(undefined)
  })

  afterEach(() => {
    cleanup()
    vi.resetAllMocks()
    useStoreCompareStore.getState().reset()
  })

  it("opens SQL beside Refresh, explains unavailable states, and copies the current query", async () => {
    let finish!: (value: { environment: string; checked_at: number; tables: [] }) => void
    vi.mocked(fetchStoreCatalogue).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    render(<SchemaPane />)
    const exportButton = screen.getByRole("button", { name: "Export SQL" })
    expect(exportButton.previousElementSibling).toBe(screen.getByRole("button", { name: "Refresh" }))
    fireEvent.click(exportButton)
    expect(screen.getByRole("alert")).toHaveTextContent("Select a database")
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }))
    selectDev()
    exportButton.focus()
    fireEvent.click(exportButton)
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("Wait for the database comparison")
    await act(async () => finish({ environment: "dev", checked_at: 1700000000000, tables: [] }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    const window = screen.getByTestId("store-sql-window")
    expect(within(window).getByTestId("store-sql")).toBeVisible()
    expect(within(window).queryByRole("combobox")).not.toBeInTheDocument()
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(1)
    expect(fetchStoreSql).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    await waitFor(() => expect(writeClipboardText).toHaveBeenCalledWith(ANSWER.sql))
    fireEvent.click(within(window).getByRole("button", { name: "Close" }))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(screen.getByTestId("schema-pane")).toBeVisible()
    expect(exportButton).toHaveFocus()
  })

  it.each(["preview", "stale"])("keeps SQL disabled for %s even with a current comparison", async (state) => {
    render(<SchemaPane />)
    selectDev()
    await waitFor(() => expect(screen.getByRole("option", { name: "dev" })).toHaveAccessibleDescription("Compatible"))
    act(() => {
      if (state === "preview") useWorkbenchViewStore.setState({ mode: "preview" })
      else useWorkbenchFormStore.setState({ stale: true })
    })
    fireEvent.click(screen.getByRole("button", { name: "Export SQL" }))
    expect(screen.getByRole("dialog")).toBeVisible()
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent(state === "preview" ? "Switch to Build" : "Reload")
  })

  it("keeps a removed field's unresolved rename removable without blocking additive export", async () => {
    useStoreCompareStore.getState().pair({ table: "policy", column: "removed_field", was: "region" })
    vi.mocked(fetchStoreCompare).mockImplementation(async (_form, _env, _tables, renames) => ({ ...ANSWER, blocked: renames.length ? ["Cannot rename policy.region to removed_field."] : [] }))
    render(<SchemaPane />)
    selectDev()
    const summary = await screen.findByText("Database changes · Needs attention")
    expect(summary.closest("details")).not.toHaveAttribute("open")
    fireEvent.click(screen.getByRole("button", { name: "Export SQL" }))
    const window = screen.getByRole("dialog")
    expect(within(window).getByRole("button", { name: "Copy SQL" })).toBeEnabled()
    fireEvent.click(within(window).getByRole("button", { name: "Close" }))
    fireEvent.click(summary)
    expect(screen.getByRole("alert")).toHaveTextContent("Not included in SQL")
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot rename")
    act(() => useWorkbenchViewStore.setState({ mode: "preview" }))
    expect(screen.getByRole("button", { name: "Remove rename choice" })).toBeDisabled()
    act(() => useWorkbenchViewStore.setState({ mode: "build" }))
    fireEvent.click(screen.getByRole("button", { name: "Remove rename choice" }))
    fireEvent.click(screen.getByRole("button", { name: "Export SQL" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    expect(useStoreCompareStore.getState().renames).toEqual([])
  })

  it("keeps manual work, retention, metadata and checking environments on the pane", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...ANSWER, matches: false, manual: ["Fix the primary key."], requires_maintenance: true, changes: [
      { kind: "retype column", table: "policy", column: "year", was: null, disposition: "manual", description: "SQLite requires a rebuild.", quotes: 2 },
      { kind: "keep column", table: "pricing", column: "old", was: null, disposition: "retained", description: "Later saves clear its values.", quotes: 1 },
    ] })
    render(<SchemaPane />)
    selectDev()
    fireEvent.click(await screen.findByText("Database changes · Needs attention"))
    expect(screen.getByRole("region", { name: "Manual work" })).toHaveTextContent("Fix the primary key")
    fireEvent.click(screen.getByText("Change type of policy.year"))
    expect(screen.getByText("SQLite requires a rebuild.")).toBeVisible()
    fireEvent.click(screen.getByText("1 unused database item kept"))
    fireEvent.click(screen.getByText("Keep pricing.old"))
    expect(screen.getByText("Later saves clear its values.")).toBeVisible()
    expect(screen.getByText(/Saved revision rev-1/)).toBeVisible()
    expect(screen.queryByText(/Coordinate these changes/)).not.toBeInTheDocument()
    expect(screen.queryByText("The SQL does not complete these changes.")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Check environments" }))
    await waitFor(() => expect(fetchStoreCatalogue).toHaveBeenCalledTimes(3))
    expect(fetchStoreCatalogue).toHaveBeenCalledWith("prod", true)
  })

  it("explains why SQL is unavailable with no configured databases", () => {
    useWorkbenchStore.setState({ environments: [] })
    render(<SchemaPane />)
    fireEvent.click(screen.getByRole("button", { name: "Export SQL" }))
    expect(screen.getByRole("dialog", { name: "Export database changes" })).toBeVisible()
    expect(screen.getByRole("alert")).toHaveTextContent("Configure a database")
    expect(screen.getByRole("button", { name: "Copy SQL" })).toBeDisabled()
  })
})
