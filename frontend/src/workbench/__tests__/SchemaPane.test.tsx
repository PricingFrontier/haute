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
  differences: [], extras: [], renames: [], matches: true, sql: "-- dev query",
  changes: [], manual: [], blocked: [],
}

const selectDev = () => fireEvent.change(screen.getByRole("combobox", { name: "Database" }), { target: { value: "dev" } })

describe("SchemaPane", () => {
  beforeEach(() => {
    useWorkbenchFormStore.getState().install(sheetForm(), "rev-1")
    useWorkbenchStore.setState({ environments: ["dev", "prod"] })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: true, schemaSearch: "" })
    useStoreCompareStore.getState().reset()
    vi.mocked(fetchStoreCatalogue).mockImplementation(async (environment) => ({ environment, target: "a".repeat(64), checked_at: 1700000000000, tables: [] }))
    vi.mocked(fetchStoreCompare).mockResolvedValue(ANSWER)
    vi.mocked(writeClipboardText).mockResolvedValue(undefined)
  })

  afterEach(() => {
    cleanup()
    vi.resetAllMocks()
    useStoreCompareStore.getState().reset()
  })

  it("opens SQL beside Refresh, explains unavailable states, and copies the current query", async () => {
    let finish!: (value: { environment: string; target: string; checked_at: number; tables: [] }) => void
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
    await act(async () => finish({ environment: "dev", target: "a".repeat(64), checked_at: 1700000000000, tables: [] }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
    const window = screen.getByTestId("store-sql-window")
    expect(within(window).getByTestId("store-sql")).toBeVisible()
    expect(within(window).queryByRole("combobox")).not.toBeInTheDocument()
    expect(fetchStoreCatalogue).toHaveBeenCalledTimes(1)
    expect(fetchStoreSql).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Copy SQL" }))
    await waitFor(() => expect(writeClipboardText).toHaveBeenCalledWith(expect.any(Promise)))
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

  it("shows no additions without claiming compatibility when existing fields differ", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...ANSWER, matches: false,
      manual: ["Fix the primary key."], blocked: ["Cannot rename an existing index."],

    })
    render(<SchemaPane />)
    selectDev()
    const summary = await screen.findByText("Database changes · No additions")
    expect(summary.closest("details")).not.toHaveAttribute("open")
    fireEvent.click(summary)
    const review = screen.getByTestId("store-plan-review")
    expect(review).toHaveTextContent("No missing tables or columns.")
    expect(review).not.toHaveTextContent(/Compatible|primary key|rename|Needs attention/)
    expect(screen.getByRole("option", { name: "dev" })).toHaveAccessibleDescription("Needs attention")
    fireEvent.click(screen.getByRole("button", { name: "Export SQL" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy SQL" })).toBeEnabled())
  })

  it("groups missing columns and new tables without migration-review clutter", async () => {
    vi.mocked(fetchStoreCompare).mockResolvedValue({ ...ANSWER, matches: false,
      manual: ["Fix the primary key."], blocked: ["Cannot rename an existing index."], changes: [
        { kind: "add column", table: "policy", column: "flood_zone", disposition: "sql", description: "Add Text column; existing rows are empty." },
        { kind: "add column", table: "policy", column: "territory", disposition: "sql", description: "Add Text column; existing rows are empty." },
        { kind: "add column", table: "policy", column: "invalid_name", disposition: "manual", description: "Name is too long for this database." },
        { kind: "create table", table: "pricing", column: null, disposition: "sql", description: "Create table with keys." },
      ] })
    render(<SchemaPane />)
    selectDev()
    fireEvent.click(await screen.findByText("Database changes · 3 columns · 1 table"))
    const review = screen.getByTestId("store-plan-review")
    const policy = within(review).getByRole("region", { name: "Additions for policy" })
    expect(within(policy).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "flood_zone", "territory", "invalid_nameName is too long for this database.",
    ])
    expect(within(policy).getByRole("alert")).toHaveTextContent("Name is too long")
    expect(within(review).getByRole("region", { name: "Additions for pricing" })).toHaveTextContent("New table")
    expect(review).not.toHaveTextContent(/primary key|rename|rebuild|saves clear|quotes|Add Text/)
    expect(review.querySelector("details")).toBeNull()
    expect(screen.queryByText(/Saved revision/)).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Check environments" })).not.toBeInTheDocument()
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
