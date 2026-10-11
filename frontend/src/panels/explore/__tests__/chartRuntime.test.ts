import { beforeEach, describe, expect, it, vi } from "vitest"

const { init, use, PieChart } = vi.hoisted(() => ({ init: vi.fn(), use: vi.fn(), PieChart: { name: "pie" } }))

vi.mock("echarts/core", () => ({
  use: (...args: unknown[]) => use(...args),
  init: (...args: unknown[]) => init(...args),
}))
vi.mock("echarts/charts", () => ({ BarChart: {}, LineChart: {}, PieChart }))
vi.mock("echarts/components", () => ({
  AriaComponent: {},
  DataZoomComponent: {},
  GridComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  TooltipComponent: {},
}))
vi.mock("echarts/renderers", () => ({ SVGRenderer: {} }))

import { createComboChart } from "../chartRuntime"

const runtime = () => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  getDataURL: vi.fn(() => "data:image/svg+xml,ok"),
})

describe("createComboChart", () => {
  beforeEach(() => {
    init.mockReset()
  })

  it("registers the pie the workbench's Chart draws", () => {
    expect(use).toHaveBeenCalledWith(expect.arrayContaining([PieChart]))
  })

  it("passes a new option's merge settings through when given, and the option alone when not", () => {
    const inner = runtime()
    init.mockReturnValueOnce(inner)
    const instance = createComboChart(document.createElement("div"))
    const option = { series: [] }

    instance.setOption(option, { replaceMerge: ["series", "xAxis"] })
    expect(inner.setOption).toHaveBeenLastCalledWith(option, { replaceMerge: ["series", "xAxis"] })

    instance.setOption(option)
    expect(inner.setOption).toHaveBeenLastCalledWith(option)
    expect(inner.setOption.mock.calls[1]).toHaveLength(1)
  })

  it("initialises with the svg renderer and requests svg data URLs", () => {
    const inner = runtime()
    init.mockReturnValueOnce(inner)
    const element = document.createElement("div")

    const instance = createComboChart(element)

    expect(init).toHaveBeenCalledWith(element, undefined, { renderer: "svg" })
    expect(instance.getDataURL()).toBe("data:image/svg+xml,ok")
    expect(inner.getDataURL).toHaveBeenCalledWith({ type: "svg" })
  })
})
