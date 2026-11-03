import { useEffect, useState } from "react"

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
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)"

/** Whether the page asks for reduced motion, which a chart's animation follows. */
export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION_QUERY).matches
}

/**
 * A number that changes whenever the page's theme or its reduced-motion preference does:
 * a chart that depends on it is drawn again in the new theme.
 */
export function useChartEnvironmentRevision(): number {
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const refresh = () => setRevision((current) => current + 1)
    const media = typeof window.matchMedia === "function" ? window.matchMedia(REDUCED_MOTION_QUERY) : null
    media?.addEventListener?.("change", refresh)
    const themeObserver = typeof MutationObserver === "undefined" ? null : new MutationObserver(refresh)
    themeObserver?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme", "style"],
    })
    return () => {
      media?.removeEventListener?.("change", refresh)
      themeObserver?.disconnect()
    }
  }, [])
  return revision
}

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
