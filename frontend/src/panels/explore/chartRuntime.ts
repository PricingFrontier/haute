import * as echarts from "echarts/core"
import { BarChart, LineChart, PieChart } from "echarts/charts"
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components"
import { SVGRenderer } from "echarts/renderers"

echarts.use([
  BarChart,
  LineChart,
  // The workbench's Chart draws pies; Explore's charts draw none.
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  AriaComponent,
  SVGRenderer,
])

/** How a new option joins a chart's: merged, the component types listed replaced whole. */
export type ChartOptionSettings = { replaceMerge?: string[] }

export type ComboChartInstance = {
  setOption(option: Record<string, unknown>, settings?: ChartOptionSettings): void
  resize(): void
  dispose(): void
  getDataURL(): string
}

export function createComboChart(element: HTMLElement): ComboChartInstance {
  const instance = echarts.init(element, undefined, { renderer: "svg" })
  return {
    setOption(option, settings) {
      if (settings === undefined) instance.setOption(option)
      else instance.setOption(option, settings)
    },
    resize() {
      instance.resize()
    },
    dispose() {
      instance.dispose()
    },
    getDataURL() {
      return instance.getDataURL({ type: "svg" })
    },
  }
}
