/**
 * The optimiser result workspace on the shared ResultsWorkspace shell.
 *
 * Mirrors ValidationWorkspace.test.tsx (the modelling workspace): the same
 * Focus view, remembered height and tab/pane wiring, plus what only the
 * optimiser has — a provenance strip on every tab, an intro on every tab
 * and the Frontier tab's narrow-width stacking.
 */
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import OptimiserPreview from "../OptimiserPreview"
import type { FrontierData, OptimiserPreviewData } from "../OptimiserPreview"
import type { OptimiserSolveResult } from "../../api/types"
import useUIStore from "../../stores/useUIStore"
import { makeHistoryEntry, makeInputSummary, makeSolveResult } from "../../test-utils/factories"
import { makeAdjustmentReport, makeOnlineFrontier } from "../optimiser/__tests__/fixtures"

vi.mock("../../api/client", () => ({
  selectFrontierPoint: vi.fn(() => new Promise(() => {})),
  solveOptimiser: vi.fn(),
  cancelOptimiserSolve: vi.fn(),
}))

// The shell is under test, not the panes: each pane is a marker.
vi.mock("../optimiser/SummaryTab", () => ({
  default: () => <div data-testid="summary-pane" />,
}))
vi.mock("../optimiser/DetailCard", () => ({
  default: () => <div data-testid="frontier-detail-card" />,
}))
vi.mock("../optimiser/ConvergenceChart", () => ({
  default: () => <div data-testid="convergence-pane" />,
}))
vi.mock("../optimiser/QuotesTab", () => ({
  default: () => <div data-testid="quotes-pane" />,
}))
vi.mock("../optimiser/RatebookRatesTab", () => ({
  default: () => <div data-testid="rates-pane" />,
}))

const HERE = path.dirname(fileURLToPath(import.meta.url))
const VALIDATION_CSS = readFileSync(
  path.resolve(HERE, "..", "modelling", "validation.css"),
  "utf8",
)
const INDEX_CSS = readFileSync(path.resolve(HERE, "..", "..", "index.css"), "utf8")

const EXPECTED_VALUES =
  "Expected values from the scoring models on the solve quotes; not observed outcomes."
// The source segment of the default input summary (makeInputSummary).
const SOURCE = "Data: batch scenario of main.py"

function makeFrontier(n = 5): FrontierData {
  return makeOnlineFrontier(n)
}

function onlineResult(overrides: Partial<OptimiserSolveResult> = {}): OptimiserSolveResult {
  return makeSolveResult({
    mode: "online",
    total_objective: 1_234_567,
    constraints: { loss_ratio: 0.65 },
    effective_bounds: { loss_ratio: { kind: "max", bound: 0.7 } },
    lambdas: { loss_ratio: 0.005 },
    converged: true,
    iterations: 15,
    n_quotes: 50_000,
    n_steps: 21,
    history: [
      makeHistoryEntry({ iteration: 1, total_objective: 1_100_000 }),
      makeHistoryEntry({ iteration: 2, total_objective: 1_200_000 }),
    ],
    adjustments: makeAdjustmentReport(),
    ...overrides,
  })
}

function makeData(overrides: Partial<OptimiserPreviewData> = {}): OptimiserPreviewData {
  const solvedResult = overrides.solvedResult ?? overrides.result ?? onlineResult()
  return {
    result: solvedResult,
    solvedResult,
    jobId: "job_1",
    constraints: { loss_ratio: { max: 0.7 } },
    nodeLabel: "Price optimiser",
    frontier: makeFrontier(),
    selectedPointIndex: null,
    ...overrides,
  }
}

function renderPreview(data: OptimiserPreviewData = makeData()) {
  return render(
    <OptimiserPreview data={data} nodeId="opt_1" allNodes={[]} edges={[]} />,
  )
}

function activePane(): HTMLElement {
  return screen.getByRole("tabpanel")
}

beforeEach(() => useUIStore.setState({ optimiserPreviewHeight: 420, modellingPreviewHeight: 420 }))
afterEach(cleanup)

describe("Optimiser workspace", () => {
  it("wires every tab to its pane and the active pane back to its tab", () => {
    renderPreview()
    const tablist = screen.getByRole("tablist", { name: "Optimiser result panes" })
    const tabs = within(tablist).getAllByRole("tab")
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Frontier",
      "Summary",
      "Adjustments",
      "Quotes",
      "Convergence",
    ])
    for (const tab of tabs) {
      const key = tab.textContent!.toLowerCase()
      expect(tab).toHaveAttribute("id", `optimiser-preview-${key}-tab`)
      expect(tab).toHaveAttribute("aria-controls", `optimiser-preview-${key}-pane`)
      fireEvent.click(tab)
      const pane = activePane()
      expect(pane).toHaveAttribute("id", `optimiser-preview-${key}-pane`)
      expect(pane).toHaveAttribute("aria-labelledby", `optimiser-preview-${key}-tab`)
      expect(pane).toHaveClass("validation-workspace")
    }
  })

  it("keeps the active tab through Focus view and returns on Escape", () => {
    renderPreview()
    fireEvent.click(screen.getByRole("tab", { name: "Convergence" }))
    const focus = screen.getByRole("button", { name: "Focus view" })
    focus.focus()
    fireEvent.click(focus)
    const dialog = screen.getByRole("dialog", { name: "Optimiser validation" })
    expect(within(dialog).getByRole("tab", { name: "Convergence" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    expect(within(dialog).getByTestId("convergence-pane")).toBeInTheDocument()
    // The frontier point stepper stays a header action in Focus view.
    expect(within(dialog).getByRole("button", { name: "Exit focus view" })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: "Escape" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Convergence" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
    expect(screen.getByRole("button", { name: "Focus view" })).toHaveFocus()
  })

  it("keeps the frontier point stepper as a header action", () => {
    renderPreview(makeData({ selectedPointIndex: 1 }))
    const header = screen.getByTestId("optimiser-preview-frame-header")
    expect(within(header).getByText("Point 2 of 5")).toBeInTheDocument()
    expect(within(header).getByRole("button", { name: "Focus view" })).toBeInTheDocument()
  })

  it("remembers its own docked height across remounts, apart from modelling's", () => {
    useUIStore.setState({ optimiserPreviewHeight: 380 })
    const { unmount } = renderPreview()
    const frame = screen.getByTestId("optimiser-preview-frame")
    expect(frame.style.height).toBe("380px")
    fireEvent.mouseDown(frame.firstElementChild!, { clientY: 600 })
    fireEvent.mouseMove(document, { clientY: 540 })
    fireEvent.mouseUp(document, { clientY: 540 })
    expect(frame.style.height).toBe("440px")
    unmount()
    renderPreview()
    expect(screen.getByTestId("optimiser-preview-frame").style.height).toBe("440px")
    expect(useUIStore.getState().modellingPreviewHeight).toBe(420)
  })

  it("shows the provenance strip on every tab", () => {
    renderPreview()
    for (const name of ["Frontier", "Summary", "Quotes", "Convergence"]) {
      fireEvent.click(screen.getByRole("tab", { name }))
      expect(screen.getByTestId("optimiser-provenance")).toHaveTextContent(
        `Online · 50,000 quotes × 21 scenario steps · ${SOURCE} · As solved · ${EXPECTED_VALUES}`,
      )
    }
  })

  it("names a selected frontier point as i of the points returned", () => {
    renderPreview(makeData({ selectedPointIndex: 2 }))
    expect(screen.getByTestId("optimiser-provenance")).toHaveTextContent(
      `Online · 50,000 quotes × 21 scenario steps · ${SOURCE} · Frontier point 3 of 5 · ${EXPECTED_VALUES}`,
    )
  })

  it("names a ratebook solve and its grid", () => {
    renderPreview(
      makeData({
        frontier: null,
        result: makeSolveResult({
          mode: "ratebook",
          n_quotes: 1_200,
          n_steps: 9,
          cd_iterations: 4,
          factor_tables: {
            region: [{ __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 3 }],
          },
        }),
      }),
    )
    for (const name of ["Summary", "Rates"]) {
      fireEvent.click(screen.getByRole("tab", { name }))
      expect(screen.getByTestId("optimiser-provenance")).toHaveTextContent(
        `Ratebook · 1,200 quotes × 9 scenario steps · ${SOURCE} · As solved · ${EXPECTED_VALUES}`,
      )
    }
  })

  it("says so when a result does not report its grid, rather than omitting it", () => {
    renderPreview(makeData({ result: onlineResult({ n_quotes: null, n_steps: null }) }))
    expect(screen.getByTestId("optimiser-provenance")).toHaveTextContent(
      `Online · Grid size not reported · ${SOURCE} · As solved · ${EXPECTED_VALUES}`,
    )
  })

  it("names the data the solve ran on, from the result's input summary", () => {
    renderPreview(makeData({
      result: onlineResult({
        input_summary: makeInputSummary({ data_source: "renewals_2026", source_file: "pricing/main.py" }),
      }),
    }))
    expect(screen.getByTestId("optimiser-provenance")).toHaveTextContent(
      "Data: renewals_2026 scenario of pricing/main.py",
    )
  })

  it("names only the scenario when the pipeline has no source file", () => {
    renderPreview(makeData({
      result: onlineResult({ input_summary: makeInputSummary({ source_file: null }) }),
    }))
    const strip = screen.getByTestId("optimiser-provenance")
    expect(strip).toHaveTextContent(`Data: batch scenario · As solved · ${EXPECTED_VALUES}`)
    expect(strip).not.toHaveTextContent(" of ")
  })

  it("fails loudly on a mode it does not know", () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    expect(() =>
      renderPreview(makeData({ result: onlineResult({ mode: "hybrid" as OptimiserSolveResult["mode"] }) })),
    ).toThrow(/Unknown optimiser mode "hybrid"/)
  })

  it("introduces every tab, stating clamp rate as a search-space diagnostic", () => {
    const expected: Record<string, string> = {
      Frontier: "Efficient frontier",
      Summary: "Solve summary",
      Adjustments: "Adjustments",
      Quotes: "Per-quote choices",
      Convergence: "Convergence",
    }
    renderPreview()
    for (const [tab, title] of Object.entries(expected)) {
      fireEvent.click(screen.getByRole("tab", { name: tab }))
      const pane = activePane()
      expect(within(pane).getByRole("heading", { level: 3, name: title })).toBeInTheDocument()
      expect(within(pane).getByRole("heading", { level: 3 }).nextElementSibling?.textContent)
        .not.toBe("")
    }
    cleanup()

    renderPreview(
      makeData({
        frontier: null,
        result: makeSolveResult({
          mode: "ratebook",
          n_quotes: 10,
          n_steps: 3,
          factor_tables: {
            region: [{ __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 3 }],
          },
        }),
      }),
    )
    fireEvent.click(screen.getByRole("tab", { name: "Rates" }))
    const rates = activePane()
    expect(within(rates).getByRole("heading", { level: 3, name: "Ratebook rates" }))
      .toBeInTheDocument()
    expect(rates).toHaveTextContent(/strictly outside the scenario range/)
    expect(rates).toHaveTextContent(/quotes at a grid edge are not counted/i)
  })

  it("uses its own accent, never the warning colour", () => {
    renderPreview()
    expect(screen.getByRole("tab", { name: "Frontier" })).toHaveStyle({
      color: "var(--optimiser-accent)",
    })
    const pane = activePane()
    expect(pane.style.getPropertyValue("--results-accent")).toBe("var(--optimiser-accent)")
    expect(pane.style.getPropertyValue("--results-accent-soft")).toBe(
      "var(--optimiser-accent-soft)",
    )
    const token = (name: string) =>
      new RegExp(`${name}:\\s*([^;]+);`).exec(INDEX_CSS)?.[1].trim()
    const accent = token("--optimiser-accent")
    expect(accent).toBeDefined()
    expect(token("--optimiser-accent-soft")).toBeDefined()
    for (const warning of ["--warning", "--warning-strong", "--danger"]) {
      expect(accent).not.toBe(token(warning))
      expect(accent).not.toContain(`var(${warning})`)
    }
  })

  it("stacks the frontier chart above the detail card at a narrow container width", () => {
    renderPreview(makeData({ selectedPointIndex: 0 }))
    const pane = activePane()
    const layout = pane.querySelector(".optimiser-frontier-layout")
    expect(layout).not.toBeNull()
    const chart = layout!.querySelector(".optimiser-frontier-chart")
    const detail = layout!.querySelector(".optimiser-frontier-detail")
    expect(chart).not.toBeNull()
    expect(within(detail as HTMLElement).getByTestId("frontier-detail-card")).toBeInTheDocument()
    // Chart first in reading order, so stacking puts it above the card.
    expect(chart!.compareDocumentPosition(detail!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    const narrow = /@container\s*\(max-width:\s*640px\)\s*\{([\s\S]*)\}\s*$/.exec(VALIDATION_CSS)
    expect(narrow).not.toBeNull()
    expect(narrow![1]).toMatch(/\.optimiser-frontier-layout\s*\{[^}]*flex-direction:\s*column/)
    expect(narrow![1]).toMatch(/\.optimiser-frontier-detail\s*\{[^}]*max-width:\s*none/)
  })

  it("labels the frontier axis picker in the workspace type scale", () => {
    const base = makeFrontier()
    // The picker offers the frontier's swept constraints: every point names each one.
    const frontier: FrontierData = {
      ...base,
      constraint_names: ["loss_ratio", "volume"],
      swept_axes: ["loss_ratio", "volume"],
      points: base.points.map((point) => ({
        ...point,
        thresholds: { ...point.thresholds, volume: 0.9 },
        bounds: { ...point.bounds, volume: 0.9 },
        totals: { ...point.totals, volume: 0.95 },
        lambdas: { ...point.lambdas, volume: 0 },
      })),
    }
    renderPreview(
      makeData({
        frontier,
        result: onlineResult({
          constraints: { loss_ratio: 0.65, volume: 0.95 },
          effective_bounds: {
            loss_ratio: { kind: "max", bound: 0.7 },
            volume: { kind: "min", bound: 0.9 },
          },
          lambdas: { loss_ratio: 0.005, volume: 0 },
        }),
        constraints: { loss_ratio: { max: 0.7 }, volume: { min: 0.9 } },
      }),
    )
    const picker = screen.getByLabelText(/X axis/)
    expect(picker.tagName).toBe("SELECT")
    expect(picker).toHaveClass("validation-control")
    expect(screen.getByText(/5 frontier points\. Click a point for details\./))
      .toHaveClass("validation-chart-description")
  })
})
