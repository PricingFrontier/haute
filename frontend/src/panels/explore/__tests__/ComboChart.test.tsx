import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ComboChart from "../ComboChart"
import type { PivotChartData } from "../chartData"
import { createExploreChart } from "../chartConfig"

const setOption = vi.fn()
const resize = vi.fn()
const dispose = vi.fn()
const createComboChart = vi.fn<(element: HTMLElement) => {
  setOption: typeof setOption
  resize: typeof resize
  dispose: typeof dispose
}>(() => ({ setOption, resize, dispose }))

vi.mock("../chartRuntime", () => ({
  createComboChart: (element: HTMLElement) => createComboChart(element),
}))

let resizeCallback: ResizeObserverCallback | null = null

class MockResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    resizeCallback = callback
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

const chart = {
  ...createExploreChart([]),
  name: "Claims chart",
  pivot_id: "pivot_1",
}

const data: PivotChartData = {
  categories: [
    {
      key: "north",
      label: "North",
      rowIndex: 0,
      path: { members: [], is_grand_total: false },
    },
    {
      key: "south",
      label: "South",
      rowIndex: 1,
      path: { members: [], is_grand_total: false },
    },
  ],
  series: [
    {
      key: "paid",
      id: "encoding_paid",
      valueId: "paid",
      name: "Paid",
      columnIndex: 0,
      style: {
        mark: "column",
        axis: "primary",
        stack_group: null,
        color: null,
        data_labels: false,
        markers: false,
      },
      values: [42, null],
      formattedValues: ["£42.00", null],
    },
  ],
  dormantOverrideIds: [],
  dormantEncodingIds: [],
  warnings: [],
}

describe("ComboChart", () => {
  beforeEach(() => {
    setOption.mockReset()
    resize.mockReset()
    dispose.mockReset()
    createComboChart.mockClear()
    resizeCallback = null
    vi.stubGlobal("ResizeObserver", MockResizeObserver)
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it("initialises lazily, resizes, updates options, and disposes deterministically", async () => {
    const view = render(<ComboChart chart={chart} data={data} />)

    await waitFor(() => expect(createComboChart).toHaveBeenCalledTimes(1))
    expect(setOption).toHaveBeenCalledTimes(1)

    resizeCallback?.([], {} as ResizeObserver)
    expect(resize).toHaveBeenCalledTimes(1)

    view.rerender(
      <ComboChart
        chart={{ ...chart, legend: { visible: false, position: "bottom" } }}
        data={data}
      />,
    )
    await waitFor(() => expect(createComboChart).toHaveBeenCalledTimes(2))
    expect(dispose).toHaveBeenCalledTimes(1)

    view.unmount()
    expect(dispose).toHaveBeenCalledTimes(2)
  })

  it("provides a textual summary and an equivalent semantic data table", async () => {
    render(<ComboChart chart={chart} data={data} />)
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())

    const summary = "Claims chart: 2 categories and 1 series."
    expect(screen.getByRole("region", { name: summary })).toBeVisible()
    expect(screen.getByText(summary)).toHaveClass("sr-only")
    expect(screen.queryByRole("table")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Show data table" }))
    const table = screen.getByRole("table", { name: "Claims chart data" })
    expect(table).toHaveTextContent("Category")
    expect(table).toHaveTextContent("Paid")
    expect(table).toHaveTextContent("North")
    expect(table).toHaveTextContent("£42.00")
    expect(table).toHaveTextContent("—")
    fireEvent.click(screen.getByRole("button", { name: "Hide data table" }))
    expect(screen.queryByRole("table")).toBeNull()
  })

  it("rebuilds when the application theme changes", async () => {
    const view = render(<ComboChart chart={chart} data={data} />)
    await waitFor(() => expect(createComboChart).toHaveBeenCalledTimes(1))

    document.documentElement.dataset.theme = "dark"
    await waitFor(() => expect(createComboChart).toHaveBeenCalledTimes(2))
    expect(dispose).toHaveBeenCalledTimes(1)

    view.unmount()
    expect(dispose).toHaveBeenCalledTimes(2)
    delete document.documentElement.dataset.theme
  })

  it("surfaces an isolated runtime error instead of throwing from the pane", async () => {
    createComboChart.mockImplementationOnce(() => {
      throw new Error("SVG runtime unavailable")
    })

    render(<ComboChart chart={chart} data={data} />)

    expect(await screen.findByRole("alert")).toHaveTextContent("SVG runtime unavailable")
  })
})
