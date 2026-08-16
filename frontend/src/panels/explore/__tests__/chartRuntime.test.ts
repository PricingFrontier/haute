import { beforeEach, describe, expect, it, vi } from "vitest"

const { init, use } = vi.hoisted(() => ({ init: vi.fn(), use: vi.fn() }))

vi.mock("echarts/core", () => ({
  use: (...args: unknown[]) => use(...args),
  init: (...args: unknown[]) => init(...args),
}))
vi.mock("echarts/charts", () => ({ BarChart: {}, LineChart: {} }))
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

describe("createComboChart", () => {
  beforeEach(() => {
    init.mockReset()
  })

  it("initialises with the svg renderer and requests svg data URLs", () => {
    const inner = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      getDataURL: vi.fn(() => "data:image/svg+xml,ok"),
    }
    init.mockReturnValueOnce(inner)
    const element = document.createElement("div")

    const instance = createComboChart(element)

    expect(init).toHaveBeenCalledWith(element, undefined, { renderer: "svg" })
    expect(instance.getDataURL()).toBe("data:image/svg+xml,ok")
    expect(inner.getDataURL).toHaveBeenCalledWith({ type: "svg" })
  })
})
