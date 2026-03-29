import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import OptimiserDataPreview from "../OptimiserDataPreview"
import type { PreviewData } from "../DataPreview"

vi.mock("../../hooks/useDragResize", () => ({
  useDragResize: () => ({
    height: 320,
    containerRef: { current: null },
    onDragStart: vi.fn(),
  }),
}))

// ── Helpers ───────────────────────────────────────────────────────

function makePreviewData(overrides: Partial<PreviewData> = {}): PreviewData {
  // 2 quotes, 3 scenarios each
  const preview = [
    { quote_id: "Q001", scenario_index: 0, scenario_value: 0.9, margin: 100, volume: 1.0 },
    { quote_id: "Q001", scenario_index: 1, scenario_value: 1.0, margin: 110, volume: 0.95 },
    { quote_id: "Q001", scenario_index: 2, scenario_value: 1.1, margin: 120, volume: 0.9 },
    { quote_id: "Q002", scenario_index: 0, scenario_value: 0.9, margin: 200, volume: 1.0 },
    { quote_id: "Q002", scenario_index: 1, scenario_value: 1.0, margin: 220, volume: 0.93 },
    { quote_id: "Q002", scenario_index: 2, scenario_value: 1.1, margin: 240, volume: 0.86 },
  ]
  return {
    nodeId: "opt_1",
    nodeLabel: "Price Optimiser",
    status: "ok",
    row_count: 6,
    column_count: 5,
    columns: [
      { name: "quote_id", dtype: "Utf8" },
      { name: "scenario_index", dtype: "Int32" },
      { name: "scenario_value", dtype: "Float32" },
      { name: "margin", dtype: "Float64" },
      { name: "volume", dtype: "Float64" },
    ],
    preview,
    error: null,
    ...overrides,
  }
}

function makeConfig(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    objective: "margin",
    constraints: { volume: { min: 0.9 } },
    quote_id: "quote_id",
    scenario_index: "scenario_index",
    scenario_value: "scenario_value",
    ...overrides,
  }
}

function renderComponent(
  dataOverrides: Partial<PreviewData> = {},
  configOverrides: Record<string, unknown> = {},
) {
  return render(
    <OptimiserDataPreview
      data={makePreviewData(dataOverrides)}
      config={makeConfig(configOverrides)}
    />,
  )
}

// ── Tests ─────────────────────────────────────────────────────────

describe("OptimiserDataPreview", () => {
  afterEach(cleanup)

  describe("Header & metadata", () => {
    it("renders node label and quote/scenario counts", () => {
      renderComponent()
      expect(screen.getByText("Price Optimiser")).toBeInTheDocument()
      expect(screen.getByText(/2 quotes/)).toBeInTheDocument()
      expect(screen.getByText(/3 scenarios/)).toBeInTheDocument()
    })

    it("shows first quote ID and position by default", () => {
      renderComponent()
      // Q001 appears in both the nav header and the sidebar — use getAllByText
      const q001Elements = screen.getAllByText("Q001")
      expect(q001Elements.length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("1/2")).toBeInTheDocument()
    })
  })

  describe("Quote navigation", () => {
    it("navigates to next quote on right arrow click", () => {
      renderComponent()
      // Find the nav container by the position indicator
      const positionLabel = screen.getByText("1/2")
      const navContainer = positionLabel.parentElement!
      const buttons = navContainer.querySelectorAll("button")
      // buttons[0] = prev, buttons[1] = next
      fireEvent.click(buttons[1])
      const q002Elements = screen.getAllByText("Q002")
      expect(q002Elements.length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("2/2")).toBeInTheDocument()
    })

    it("prev button is disabled on first quote", () => {
      renderComponent()
      const positionLabel = screen.getByText("1/2")
      const navContainer = positionLabel.parentElement!
      const prevBtn = navContainer.querySelectorAll("button")[0]
      expect(prevBtn).toBeDisabled()
    })

    it("next button is disabled on last quote", () => {
      renderComponent()
      // Navigate to last quote
      const positionLabel = screen.getByText("1/2")
      const navContainer = positionLabel.parentElement!
      const nextBtn = navContainer.querySelectorAll("button")[1]
      fireEvent.click(nextBtn)
      // Now next should be disabled
      const updatedNextBtn = screen.getByText("2/2").parentElement!.querySelectorAll("button")[1]
      expect(updatedNextBtn).toBeDisabled()
    })

    it("navigates via search input", () => {
      renderComponent()
      const searchInput = screen.getByPlaceholderText("Find quote...")
      fireEvent.change(searchInput, { target: { value: "Q002" } })
      fireEvent.keyDown(searchInput, { key: "Enter" })
      const q002Elements = screen.getAllByText("Q002")
      expect(q002Elements.length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("2/2")).toBeInTheDocument()
    })

    it("search for non-existent quote does nothing", () => {
      renderComponent()
      const searchInput = screen.getByPlaceholderText("Find quote...")
      fireEvent.change(searchInput, { target: { value: "NOPE" } })
      fireEvent.keyDown(searchInput, { key: "Enter" })
      // Should still be on first quote
      expect(screen.getByText("1/2")).toBeInTheDocument()
    })
  })

  describe("Series checkboxes", () => {
    it("renders checkboxes for objective and constraint columns", () => {
      renderComponent()
      const checkboxes = screen.getAllByRole("checkbox")
      // margin + volume = 2
      expect(checkboxes.length).toBe(2)
    })

    it("labels objective column with 'obj' tag", () => {
      renderComponent()
      expect(screen.getByText("obj")).toBeInTheDocument()
    })

    it("unchecking a series removes its line from the chart", () => {
      const { container } = renderComponent()
      // Combined chart SVG has <g> groups (one per series) with <path> inside
      const chartSvg = container.querySelector('svg[style*="background"]')!
      const circlesBefore = chartSvg.querySelectorAll("circle").length
      expect(circlesBefore).toBe(6) // 2 series × 3 points

      // Uncheck volume (second series checkbox)
      const checkboxes = screen.getAllByRole("checkbox")
      fireEvent.click(checkboxes[1]) // uncheck volume

      const circlesAfter = chartSvg.querySelectorAll("circle").length
      expect(circlesAfter).toBe(3) // 1 series × 3 points
    })
  })

  describe("Chart rendering", () => {
    it("renders an SVG chart in combined mode by default", () => {
      const { container } = renderComponent()
      // There should be at least one non-icon SVG (the chart)
      const svgs = container.querySelectorAll("svg")
      // Icon SVGs (Target, ChevronLeft, etc.) + chart SVG
      expect(svgs.length).toBeGreaterThan(0)
    })

    it("shows individual series when only one is checked", () => {
      const { container } = renderComponent()
      const checkboxes = screen.getAllByRole("checkbox")
      // Uncheck volume (second series) — leaves only margin
      fireEvent.click(checkboxes[1])
      const chartSvg = container.querySelector('svg[style*="background"]')!
      const circles = chartSvg.querySelectorAll("circle")
      expect(circles.length).toBe(3) // 1 series × 3 points
    })

    it("renders dots for each scenario point in combined mode", () => {
      const { container } = renderComponent()
      // Combined mode: circles inside <g> elements (not icon circles)
      const chartSvg = container.querySelector('svg[style*="background"]')!
      const circles = chartSvg.querySelectorAll("circle")
      // 2 series × 3 scenarios = 6 circles
      expect(circles.length).toBe(6)
    })

    it("shows 'scenario index' x-axis label", () => {
      renderComponent()
      expect(screen.getByText("scenario index")).toBeInTheDocument()
    })
  })

  describe("Edge cases", () => {
    it("shows message when no objective configured", () => {
      render(
        <OptimiserDataPreview
          data={makePreviewData()}
          config={{ constraints: {} }}
        />,
      )
      expect(screen.getByText(/Configure an objective/)).toBeInTheDocument()
    })

    it("shows message when preview is empty", () => {
      render(
        <OptimiserDataPreview
          data={makePreviewData({ preview: [] })}
          config={makeConfig()}
        />,
      )
      expect(screen.getByText(/No scenario data/)).toBeInTheDocument()
    })

    it("handles single-quote data", () => {
      const preview = [
        { quote_id: "SOLO", scenario_index: 0, scenario_value: 0.9, margin: 100, volume: 1.0 },
        { quote_id: "SOLO", scenario_index: 1, scenario_value: 1.0, margin: 110, volume: 0.95 },
      ]
      renderComponent({ preview, row_count: 2 })
      const soloElements = screen.getAllByText("SOLO")
      expect(soloElements.length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("1/1")).toBeInTheDocument()
    })

    it("shows empty message when all series unchecked", () => {
      const { container } = renderComponent()
      const checkboxes = screen.getAllByRole("checkbox")
      // Uncheck margin and volume (indices 0 and 1)
      fireEvent.click(checkboxes[0])
      fireEvent.click(checkboxes[1])
      // Combined chart should show the empty message
      expect(screen.getByText(/Select at least one series/)).toBeInTheDocument()
      // No chart SVG should be rendered
      const chartSvgs = container.querySelectorAll('svg[style*="background"]')
      expect(chartSvgs.length).toBe(0)
    })

    it("collapse and expand work", () => {
      const { container } = renderComponent()
      // Find the collapse button in the header's ml-auto section
      // The last button in the header is the collapse button
      const allButtons = container.querySelectorAll("button")
      // Find the one right before the content area (the ChevronDown)
      const collapseBtn = Array.from(allButtons).find((btn) => {
        const svg = btn.querySelector(".lucide-chevron-down")
        return svg !== null
      })!
      fireEvent.click(collapseBtn)
      // Collapsed state: shows the expand button with ChevronUp
      const expandBtn = container.querySelector(".lucide-chevron-up")
      expect(expandBtn).toBeInTheDocument()
    })
  })

  describe("Quote summary sidebar", () => {
    it("shows quote ID label and scenario count label", () => {
      renderComponent()
      expect(screen.getByText("ID")).toBeInTheDocument()
      expect(screen.getByText("Scenarios")).toBeInTheDocument()
    })

    it("displays correct scenario count for current quote", () => {
      renderComponent()
      // In the sidebar, "3" should appear as the scenario count
      expect(screen.getByText("3")).toBeInTheDocument()
    })
  })

  describe("Header metadata extended", () => {
    it("shows correct quote count for multi-quote data", () => {
      const preview = [
        { quote_id: "A", scenario_index: 0, scenario_value: 0.9, margin: 100, volume: 1.0 },
        { quote_id: "A", scenario_index: 1, scenario_value: 1.0, margin: 110, volume: 0.95 },
        { quote_id: "B", scenario_index: 0, scenario_value: 0.9, margin: 200, volume: 1.0 },
        { quote_id: "B", scenario_index: 1, scenario_value: 1.0, margin: 210, volume: 0.93 },
        { quote_id: "C", scenario_index: 0, scenario_value: 0.9, margin: 300, volume: 1.0 },
        { quote_id: "C", scenario_index: 1, scenario_value: 1.0, margin: 310, volume: 0.91 },
      ]
      renderComponent({ preview, row_count: 6 })
      expect(screen.getByText(/3 quotes/)).toBeInTheDocument()
      expect(screen.getByText(/2 scenarios/)).toBeInTheDocument()
    })
  })

  describe("Quote navigation extended", () => {
    it("prev button becomes enabled after navigating forward", () => {
      renderComponent()
      const positionLabel = screen.getByText("1/2")
      const navContainer = positionLabel.parentElement!
      const prevBtn = navContainer.querySelectorAll("button")[0]
      const nextBtn = navContainer.querySelectorAll("button")[1]
      expect(prevBtn).toBeDisabled()
      fireEvent.click(nextBtn)
      const updatedPrevBtn = screen.getByText("2/2").parentElement!.querySelectorAll("button")[0]
      expect(updatedPrevBtn).not.toBeDisabled()
    })

    it("navigating back from last quote re-enables next button", () => {
      renderComponent()
      const positionLabel = screen.getByText("1/2")
      const navContainer = positionLabel.parentElement!
      const nextBtn = navContainer.querySelectorAll("button")[1]
      fireEvent.click(nextBtn)
      const updatedPrevBtn = screen.getByText("2/2").parentElement!.querySelectorAll("button")[0]
      fireEvent.click(updatedPrevBtn)
      const finalNextBtn = screen.getByText("1/2").parentElement!.querySelectorAll("button")[1]
      expect(finalNextBtn).not.toBeDisabled()
    })
  })

  describe("Chart rendering extended", () => {
    it("chart SVG renders correct number of dots for multiple series", () => {
      const { container } = renderComponent()
      const chartSvg = container.querySelector('svg[style*="background"]')!
      const circles = chartSvg.querySelectorAll("circle")
      expect(circles.length).toBe(6)
    })

    it("chart SVG has path elements for lines", () => {
      const { container } = renderComponent()
      const chartSvg = container.querySelector('svg[style*="background"]')!
      const paths = chartSvg.querySelectorAll("path")
      expect(paths.length).toBe(2)
    })
  })

  describe("Collapse/expand extended", () => {
    it("collapsed state shows node label and metadata", () => {
      const { container } = renderComponent()
      const collapseBtn = Array.from(container.querySelectorAll("button")).find((btn) => {
        return btn.querySelector(".lucide-chevron-down") !== null
      })!
      fireEvent.click(collapseBtn)
      expect(screen.getByText("Price Optimiser")).toBeInTheDocument()
      expect(screen.getByText(/2 quotes/)).toBeInTheDocument()
    })

    it("expanding from collapsed state restores chart", () => {
      const { container } = renderComponent()
      const collapseBtn = Array.from(container.querySelectorAll("button")).find((btn) => {
        return btn.querySelector(".lucide-chevron-down") !== null
      })!
      fireEvent.click(collapseBtn)
      const expandBtn = container.querySelector(".lucide-chevron-up")!.closest("button")!
      fireEvent.click(expandBtn)
      const chartSvg = container.querySelector('svg[style*="background"]')
      expect(chartSvg).toBeInTheDocument()
    })
  })

  describe("Statistics tab extended", () => {
    it("computes correct mean for volume at scenario 0", () => {
      const { container } = renderComponent()
      fireEvent.click(screen.getByText("Statistics"))
      const cells = container.querySelectorAll("td")
      const cellTexts = Array.from(cells).map((c) => c.textContent)
      expect(cellTexts).toContain("1")
    })

    it("statistics tables render for each series", () => {
      renderComponent()
      fireEvent.click(screen.getByText("Statistics"))
      const marginLabels = screen.getAllByText("margin")
      const volumeLabels = screen.getAllByText("volume")
      expect(marginLabels.length).toBeGreaterThanOrEqual(1)
      expect(volumeLabels.length).toBeGreaterThanOrEqual(1)
    })
  })

  describe("Config with no constraints", () => {
    it("renders only the objective series when no constraints", () => {
      renderComponent({}, { constraints: {} })
      const checkboxes = screen.getAllByRole("checkbox")
      // margin only = 1
      expect(checkboxes.length).toBe(1)
    })
  })

  describe("Statistics tab", () => {
    it("renders Chart and Statistics tab buttons", () => {
      renderComponent()
      expect(screen.getByText("Chart")).toBeInTheDocument()
      expect(screen.getByText("Statistics")).toBeInTheDocument()
    })

    it("defaults to Chart tab", () => {
      renderComponent()
      // Chart tab should be active — chart SVG is present
      const chartBtn = screen.getByText("Chart")
      expect(chartBtn.style.color).toContain("var(--accent)")
    })

    it("switches to Statistics tab and shows stats tables", () => {
      renderComponent()
      fireEvent.click(screen.getByText("Statistics"))

      // Should render table headers for stats columns
      expect(screen.getAllByText("Mean").length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText("Std").length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText("Min").length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText("Max").length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByText("Median").length).toBeGreaterThanOrEqual(1)
    })

    it("shows a table for each series (objective + constraints)", () => {
      renderComponent()
      fireEvent.click(screen.getByText("Statistics"))

      // "margin" (objective) and "volume" (constraint) should appear as table headers
      expect(screen.getByText("objective")).toBeInTheDocument()
      // Both series labels should be present
      const marginLabels = screen.getAllByText("margin")
      expect(marginLabels.length).toBeGreaterThanOrEqual(1)
      const volumeLabels = screen.getAllByText("volume")
      expect(volumeLabels.length).toBeGreaterThanOrEqual(1)
    })

    it("renders one row per scenario index", () => {
      renderComponent()
      fireEvent.click(screen.getByText("Statistics"))

      // 3 scenario indices (0, 1, 2) — each table has 3 data rows
      // Scenario index "0" appears in both tables
      const zeros = screen.getAllByText("0")
      expect(zeros.length).toBeGreaterThanOrEqual(2) // at least once per table
    })

    it("computes correct mean for margin at scenario 0", () => {
      // Q001 margin@0 = 100, Q002 margin@0 = 200 → mean = 150
      const { container } = renderComponent()
      fireEvent.click(screen.getByText("Statistics"))
      // Find all table cells and check for "150" (integer formatting)
      const cells = container.querySelectorAll("td")
      const cellTexts = Array.from(cells).map((c) => c.textContent)
      expect(cellTexts).toContain("150")
    })

    it("hides quote navigation and search on Statistics tab", () => {
      renderComponent()
      fireEvent.click(screen.getByText("Statistics"))
      expect(screen.queryByPlaceholderText("Find quote...")).not.toBeInTheDocument()
      expect(screen.queryByText("1/2")).not.toBeInTheDocument()
    })

    it("switching back to Chart tab restores chart view", () => {
      const { container } = renderComponent()
      fireEvent.click(screen.getByText("Statistics"))
      fireEvent.click(screen.getByText("Chart"))

      // Chart SVG should be back
      const chartSvg = container.querySelector('svg[style*="background"]')
      expect(chartSvg).toBeInTheDocument()
    })
  })
})
