import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ComboChart from "../ComboChart"
import { chartExportFileName, type PivotChartData } from "../chartData"
import { createExploreChart } from "../chartConfig"

const setOption = vi.fn()
const resize = vi.fn()
const dispose = vi.fn()
const getDataURL = vi.fn(() => "data:image/svg+xml;charset=UTF-8,<svg/>")
const createComboChart = vi.fn<(element: HTMLElement) => {
  setOption: typeof setOption
  resize: typeof resize
  dispose: typeof dispose
  getDataURL: typeof getDataURL
}>(() => ({ setOption, resize, dispose, getDataURL }))

vi.mock("../chartRuntime", () => ({
  createComboChart: (element: HTMLElement) => createComboChart(element),
}))

class StubImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  naturalWidth = 400
  naturalHeight = 280
  srcValue = ""

  set src(value: string) {
    this.srcValue = value
    stubImages.push(this)
  }
}

const stubImages: StubImage[] = []

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
        stack_normalize: false,
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
    getDataURL.mockClear()
    stubImages.length = 0
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

  it("derives export filenames with sanitisation and an all-punctuation fallback", () => {
    expect(chartExportFileName("Claims & Cost (GBP)")).toBe(
      "claims-cost-gbp.png",
    )
    expect(chartExportFileName("Chart 1")).toBe("chart-1.png")
    expect(chartExportFileName("???")).toBe("chart.png")
  })

  it("exports a 2x PNG painted with the theme background before the chart", async () => {
    render(<ComboChart chart={chart} data={data} />)
    const download = screen.getByRole("button", {
      name: "Download Claims chart image",
    })
    // The action is disabled until the async runtime has rendered.
    expect(download).toBeDisabled()
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())
    await waitFor(() => expect(download).toBeEnabled())

    const operations: string[] = []
    const context = {
      fillStyle: "",
      fillRect: () => operations.push("fillRect"),
      drawImage: () => operations.push("drawImage"),
    }
    const canvas = {
      width: 0,
      height: 0,
      getContext: () => context,
      toDataURL: () => "data:image/png;base64,exported",
    }
    const anchor = { href: "", download: "", click: vi.fn() }
    const realCreateElement = document.createElement.bind(document)
    const createElementSpy = vi
      .spyOn(document, "createElement")
      .mockImplementation((tag: string) => {
        if (tag === "canvas") return canvas as unknown as HTMLElement
        if (tag === "a") return anchor as unknown as HTMLElement
        return realCreateElement(tag)
      })
    vi.stubGlobal("Image", StubImage)

    fireEvent.click(download)
    expect(getDataURL).toHaveBeenCalledTimes(1)
    const image = stubImages[stubImages.length - 1]
    expect(image.srcValue).toContain("image/svg+xml")
    act(() => image.onload?.())

    expect(canvas.width).toBe(800)
    expect(canvas.height).toBe(560)
    expect(operations).toEqual(["fillRect", "drawImage"])
    expect(context.fillStyle).toBe("#111827")
    expect(anchor.download).toBe("claims-chart.png")
    expect(anchor.href).toBe("data:image/png;base64,exported")
    expect(anchor.click).toHaveBeenCalledTimes(1)
    createElementSpy.mockRestore()
  })

  it("surfaces a canvas rasterisation failure on the card and saves nothing", async () => {
    render(<ComboChart chart={chart} data={data} />)
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())
    const download = screen.getByRole("button", {
      name: "Download Claims chart image",
    })
    await waitFor(() => expect(download).toBeEnabled())

    const anchorClick = vi.fn()
    const realCreateElement = document.createElement.bind(document)
    const createElementSpy = vi
      .spyOn(document, "createElement")
      .mockImplementation((tag: string) => {
        if (tag === "canvas") {
          return {
            width: 0,
            height: 0,
            getContext: () => null,
          } as unknown as HTMLElement
        }
        if (tag === "a") {
          return { href: "", download: "", click: anchorClick } as unknown as HTMLElement
        }
        return realCreateElement(tag)
      })
    vi.stubGlobal("Image", StubImage)

    fireEvent.click(download)
    const image = stubImages[stubImages.length - 1]
    act(() => image.onload?.())

    expect(screen.getByRole("alert")).toHaveTextContent(
      "A 2D canvas context is unavailable.",
    )
    expect(anchorClick).not.toHaveBeenCalled()
    createElementSpy.mockRestore()
  })

  it("surfaces a decode failure on the card and saves nothing", async () => {
    render(<ComboChart chart={chart} data={data} />)
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())
    const download = screen.getByRole("button", {
      name: "Download Claims chart image",
    })
    await waitFor(() => expect(download).toBeEnabled())

    const anchorClick = vi.fn()
    const realCreateElement = document.createElement.bind(document)
    const createElementSpy = vi
      .spyOn(document, "createElement")
      .mockImplementation((tag: string) => {
        if (tag === "a") {
          return { href: "", download: "", click: anchorClick } as unknown as HTMLElement
        }
        return realCreateElement(tag)
      })
    vi.stubGlobal("Image", StubImage)

    fireEvent.click(download)
    const image = stubImages[stubImages.length - 1]
    act(() => image.onerror?.())

    expect(
      screen.getByRole("alert"),
    ).toHaveTextContent("Could not export the chart image.")
    expect(anchorClick).not.toHaveBeenCalled()
    createElementSpy.mockRestore()
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
