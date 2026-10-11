import { PIVOT_CHART_COLORS } from "../../theme/colors"
import type { ChartThemeTokens } from "./chartOptions"

const FALLBACK_TOKENS: ChartThemeTokens = PIVOT_CHART_COLORS.fallback

function cssValue(style: CSSStyleDeclaration, name: string, fallback: string): string {
  return style.getPropertyValue(name).trim() || fallback
}

/**
 * A chart's theme, read from the page's tokens where `element` sits: ECharts takes no CSS
 * variables, so each is resolved here, a fallback standing in for a token the page lacks.
 * Explore's charts and the workbench's Chart both draw with it.
 */
export function chartThemeTokens(element: HTMLElement): ChartThemeTokens {
  const style = getComputedStyle(element)
  return {
    background: cssValue(
      style,
      "--bg-panel",
      cssValue(style, "--bg-base", FALLBACK_TOKENS.background),
    ),
    text: cssValue(style, "--text-primary", FALLBACK_TOKENS.text),
    muted: cssValue(style, "--text-muted", FALLBACK_TOKENS.muted),
    grid: cssValue(style, "--border", FALLBACK_TOKENS.grid),
    series: FALLBACK_TOKENS.series.map((fallback, index) =>
      cssValue(style, `--chart-series-${index + 1}`, fallback),
    ),
  }
}
