import * as echarts from "echarts/core"
import { BarChart, LineChart } from "echarts/charts"
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
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  AriaComponent,
  SVGRenderer,
])

export type ComboChartInstance = {
  setOption(option: Record<string, unknown>): void
  resize(): void
  dispose(): void
  getDataURL(): string
}

export function createComboChart(element: HTMLElement): ComboChartInstance {
  const instance = echarts.init(element, undefined, { renderer: "svg" })
  return {
    setOption(option) {
      instance.setOption(option)
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
