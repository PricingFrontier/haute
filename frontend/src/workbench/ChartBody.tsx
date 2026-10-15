/**
 * A Chart on a sheet (specs/workbench, Charts): its fields' values as the sheet holds them,
 * none while building, the sample or the underwriter app's quote in Preview, plotted
 * through Explore's lazy chart runtime, imported once, in the page's chart theme. One chart is drawn per mount and per
 * change of theme or reduced motion, and each new option is merged into it, so a series
 * moves to its new values rather than being drawn again. It is dimmed while it plots a price for other
 * values, says "Nothing to chart yet" with nothing to plot, or why a pie cannot be drawn
 * (`chartMessage`), and gives a screen reader its values as a table.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react"
import type { ChartWidget } from "../api/types"
import type { ComboChartInstance, createComboChart } from "../panels/explore/chartRuntime"
import { chartThemeTokens } from "../panels/explore/chartTheme"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import { chartMessage, sheetChartData, sheetChartOptions } from "../utils/sheetChart"
import { useSheetValues } from "./useSheetValues"

/** What a new option replaces whole rather than merges: what a change of type, orientation or legend adds or drops. */
const REPLACED = ["series", "xAxis", "yAxis", "grid", "legend"]

/**
 * The lazy runtime's chart maker, once imported: every later draw sets its option as the
 * values change rather than after another import.
 */
let makeChart: typeof createComboChart | null = null
/**
 * The one import of the runtime in flight, which every draw waiting for it shares, as two
 * Charts mounting together or a draw overlapping the last would otherwise each import it.
 * One that fails is forgotten, so the next draw tries again.
 */
let loadingChart: Promise<typeof createComboChart> | null = null

const loadChartMaker = (): Promise<typeof createComboChart> =>
  (loadingChart ??= import("../panels/explore/chartRuntime").then(
    (runtime) => {
      makeChart = runtime.createComboChart
      return makeChart
    },
    (cause: unknown) => {
      loadingChart = null
      throw cause
    },
  ))

const reducedMotion = (): boolean =>
  typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches

export default function ChartBody({ widget }: { widget: ChartWidget }) {
  // The schema alone, so a change elsewhere in the form, as a drag makes, draws nothing again.
  const schema = useWorkbenchFormStore((s) => s.form?.schema ?? null)
  const values = useSheetValues()
  const data = useMemo(() => (schema === null ? null : sheetChartData(schema, widget, values)), [schema, widget, values])
  const message = data === null ? null : chartMessage(widget, data)
  const drawn = data !== null && message === null
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ComboChartInstance | null>(null)
  const observerRef = useRef<ResizeObserver | null>(null)
  const [environment, setEnvironment] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const summaryId = useId()

  // The theme and reduced motion, watched as Explore's charts watch them: a change draws a new chart.
  useEffect(() => {
    const refresh = () => setEnvironment((revision) => revision + 1)
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null
    media?.addEventListener?.("change", refresh)
    const themeObserver = typeof MutationObserver === "undefined" ? null : new MutationObserver(refresh)
    themeObserver?.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] })
    return () => {
      media?.removeEventListener?.("change", refresh)
      themeObserver?.disconnect()
    }
  }, [])

  // The chart lasts while there is something to draw, until the theme or reduced motion changes.
  useEffect(
    () => () => {
      observerRef.current?.disconnect()
      observerRef.current = null
      chartRef.current?.dispose()
      chartRef.current = null
    },
    [drawn, environment],
  )

  // Each new option merged into the chart, the chart made first while there is none.
  useEffect(() => {
    const host = hostRef.current
    if (!drawn || data === null || host === null) return
    let cancelled = false
    const draw = async () => {
      try {
        const make = makeChart ?? (await loadChartMaker())
        if (cancelled) return
        let chart = chartRef.current
        if (chart === null) {
          const made = make(host)
          chart = made
          chartRef.current = made
          if (typeof ResizeObserver !== "undefined") {
            observerRef.current = new ResizeObserver(() => made.resize())
            observerRef.current.observe(host)
          }
        }
        chart.setOption(sheetChartOptions(widget, data, chartThemeTokens(host), reducedMotion()), { replaceMerge: REPLACED })
        setError(null)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "The chart could not be drawn.")
      }
    }
    void draw()
    return () => {
      cancelled = true
    }
  }, [drawn, data, widget, environment])

  if (data === null) return null
  const name = widget.title || "Chart"
  return (
    <figure
      aria-labelledby={summaryId}
      data-stale={data.stale ? "true" : undefined}
      className="relative m-0 flex min-h-0 flex-1 flex-col transition-opacity"
      style={{ opacity: data.stale ? 0.5 : 1 }}
    >
      <figcaption id={summaryId} className="sr-only">
        {`${name}: ${data.categories.length} categories and ${data.series.length} series.`}
      </figcaption>
      {error !== null && (
        <div role="alert" className="mx-2 mt-2 rounded px-2 py-1.5 text-[11px]" style={{ color: "var(--danger)", background: "var(--danger-soft)" }}>
          {error}
        </div>
      )}
      {drawn ? (
        <div ref={hostRef} data-testid={`chart-host-${widget.id}`} className="min-h-0 flex-1" />
      ) : (
        <div
          className="m-3 grid flex-1 place-items-center rounded-md text-center text-[11px]"
          style={{ border: "1px dashed var(--border)", color: "var(--text-secondary)" }}
        >
          {message}
        </div>
      )}
      <table className="sr-only" aria-label={`${name} values`}>
        <thead>
          <tr>
            <th scope="col">Category</th>
            {data.series.map((entry) => (
              <th key={entry.key} scope="col">
                {entry.name || "Value"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.categories.map((category, at) => (
            <tr key={at}>
              <th scope="row">{category}</th>
              {data.series.map((entry) => (
                <td key={entry.key}>{entry.labels[at] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}
