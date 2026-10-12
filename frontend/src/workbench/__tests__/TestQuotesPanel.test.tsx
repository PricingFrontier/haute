/**
 * The test quotes panel (specs/workbench): the quote on the sheets saved under a name, the
 * test quotes listed, run, opened, accepted and deleted, each run's result and what changed,
 * and a warning once what the run priced on has changed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import type { FormSpec, WorkbenchTestQuoteSummary } from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchTestQuotesStore, { currentBasis, type QuoteRun } from "../../stores/useWorkbenchTestQuotesStore"
import { sheet } from "./fixtures"
import TestQuotesPanel from "../TestQuotesPanel"

const form: FormSpec = { version: 1, quote_list: { title: null, fields: [], headline: null }, name: "t", schema: { tables: [] }, pages: [sheet("p", "Sheet 1")], sample: {} }
const summary = (id: string, name: string): WorkbenchTestQuoteSummary => ({
  id, name, number: 1, saved_by: "ana@example.com", saved_at: "2026-10-12T09:00:00", priced: true,
})
const result = (patch: Partial<QuoteRun>): QuoteRun => ({
  status: "unchanged", number: 1, digest: "read-1", values: {}, dropped: [], tables: {}, changes: [], error: null, ...patch,
})

const actions = {
  load: vi.fn(async () => {}),
  save: vi.fn(async () => true),
  openQuote: vi.fn(async () => {}),
  run: vi.fn(async () => {}),
  accept: vi.fn(async () => {}),
  remove: vi.fn(async () => {}),
  setOpen: vi.fn(),
}

describe("TestQuotesPanel", () => {
  beforeEach(() => {
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchTestQuotesStore.setState({
      ...actions,
      open: true,
      quotes: [summary("a", "Northern fleet"), summary("b", "Southern fleet")],
      loading: false,
      error: null,
      notice: null,
      saving: false,
      selected: [],
      runs: {},
      runForm: null,
      runBasis: null,
      running: false,
      accepting: false,
    })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.restoreAllMocks()
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
  })

  it("lists the test quotes when it opens, or says how to make the first", () => {
    render(<TestQuotesPanel />)
    expect(actions.load).toHaveBeenCalledTimes(1)
    const list = screen.getByRole("list", { name: "Test quotes" })
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("Northern fleet"),
      expect.stringContaining("Southern fleet"),
    ])

    act(() => useWorkbenchTestQuotesStore.setState({ quotes: [] }))
    expect(screen.getByText(/No test quotes yet/)).toBeInTheDocument()
  })

  it("saves the quote on the sheets under the name typed, and empties the name once saved", async () => {
    render(<TestQuotesPanel />)
    const save = screen.getByRole("button", { name: "Save quote" })
    expect(save).toBeDisabled()

    fireEvent.change(screen.getByRole("textbox", { name: "Test quote name" }), { target: { value: "  Coastal  " } })
    fireEvent.click(save)

    await waitFor(() => expect(actions.save).toHaveBeenCalledWith("Coastal"))
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Test quote name" })).toHaveValue(""))
  })

  it("runs every test quote, or those ticked, and counts what the run found", () => {
    render(<TestQuotesPanel />)
    fireEvent.click(screen.getByRole("button", { name: "Run all" }))
    expect(actions.run).toHaveBeenCalledTimes(1)

    act(() =>
      useWorkbenchTestQuotesStore.setState({
        selected: ["b"],
        runBasis: currentBasis(form),
        runs: { a: result({ status: "changed" }), b: result({ status: "failed", error: "boom" }) },
      }),
    )
    expect(screen.getByRole("button", { name: "Run 1 ticked" })).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent("1 changed, 1 failed, 0 unchanged")
    expect(screen.getByTestId("test-quote-status-a")).toHaveTextContent("Changed")
    expect(screen.getByTestId("test-quote-status-b")).toHaveTextContent("Failed")
  })

  it("shows what changed in a quote, and accepts it while the run is current", () => {
    useWorkbenchTestQuotesStore.setState({
      runBasis: currentBasis(form),
      runs: {
        a: result({
          status: "changed",
          changes: [
            { path: "pricing.premium", kind: "changed", accepted: 512.4, priced: 530 },
            { path: "pricing.tax", kind: "added", accepted: null, priced: 3 },
          ],
        }),
      },
    })
    render(<TestQuotesPanel />)

    fireEvent.click(screen.getByRole("button", { name: "Northern fleet" }))
    const changes = screen.getByRole("list", { name: "What changed in Northern fleet" })
    expect(within(changes).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "pricing.premium512.4 → 530",
      "pricing.taxadded 3",
    ])
    fireEvent.click(screen.getByRole("button", { name: "Accept Northern fleet" }))
    expect(actions.accept).toHaveBeenCalledWith("a")
  })

  it("warns once what the run priced on has changed, and accepts nothing until it runs again", () => {
    useWorkbenchTestQuotesStore.setState({ runBasis: currentBasis(form), runs: { a: result({ status: "changed" }) } })
    render(<TestQuotesPanel />)
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()

    act(() => useGraphStore.setState({ structuralFingerprint: `${useGraphStore.getState().structuralFingerprint}+edit` }))

    expect(screen.getByRole("alert")).toHaveTextContent("has changed since this run")
    expect(screen.getByRole("button", { name: "Accept Northern fleet" })).toBeDisabled()
  })

  it("accepts nothing while a run or another accept is under way, the run current or not", () => {
    useWorkbenchTestQuotesStore.setState({
      runBasis: currentBasis(form),
      running: true,
      runs: { a: result({ status: "changed" }), b: result({ status: "running" }) },
    })
    render(<TestQuotesPanel />)
    expect(screen.getByRole("button", { name: "Accept Northern fleet" })).toBeDisabled()

    // Edited mid-run: nothing is said until the run is over, but nothing is accepted either.
    act(() => useGraphStore.setState({ structuralFingerprint: `${useGraphStore.getState().structuralFingerprint}+edit` }))
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Accept Northern fleet" })).toBeDisabled()

    act(() => useWorkbenchTestQuotesStore.setState({ running: false, accepting: true, runBasis: currentBasis(form) }))
    expect(screen.getByRole("button", { name: "Accept Northern fleet" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Run all" })).toBeDisabled()
  })

  it("opens a test quote onto the sheets, and deletes one once confirmed", () => {
    render(<TestQuotesPanel />)
    fireEvent.click(screen.getByRole("button", { name: "Open Southern fleet" }))
    expect(actions.openQuote).toHaveBeenCalledWith("b")

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)
    fireEvent.click(screen.getByRole("button", { name: "Delete Southern fleet" }))
    expect(confirm).toHaveBeenCalledWith("Delete the test quote 'Southern fleet'?")
    expect(actions.remove).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole("button", { name: "Delete Southern fleet" }))
    expect(actions.remove).toHaveBeenCalledWith("b")
  })

  it("closes through its header", () => {
    render(<TestQuotesPanel />)
    fireEvent.click(screen.getByTitle("Close"))
    expect(actions.setOpen).toHaveBeenCalledWith(false)
  })
})
