import { useEffect, useId, useRef, useState } from "react"

import { PIVOT_CHART_COLORS } from "../../theme/colors"
import type { ExploreChartConfig } from "./chartConfig"
import { chartExportFileName, type PivotChartData } from "./chartData"
import { buildComboChartOptions, type ChartThemeTokens } from "./chartOptions"

type ComboChartProps = { chart: ExploreChartConfig; data: PivotChartData }
type ComboChartRuntime = {
  setOption(option: Record<string, unknown>): void
  resize(): void
  dispose(): void
  getDataURL(): string
}

const FALLBACK_TOKENS: ChartThemeTokens = PIVOT_CHART_COLORS.fallback

function cssValue(style: CSSStyleDeclaration, name: string, fallback: string): string {
  return style.getPropertyValue(name).trim() || fallback
}

function themeTokens(element: HTMLElement): ChartThemeTokens {
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

export default function ComboChart({ chart, data }: ComboChartProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const runtimeRef = useRef<ComboChartRuntime | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [rendered, setRendered] = useState(false)
  const [showTable, setShowTable] = useState(false)
  const [environmentRevision, setEnvironmentRevision] = useState(0)
  const summaryId = useId()
  const tableId = useId()

  useEffect(() => {
    const refresh = () => setEnvironmentRevision((revision) => revision + 1)
    const media =
      typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-reduced-motion: reduce)")
        : null
    media?.addEventListener?.("change", refresh)

    const themeObserver =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(refresh)
    themeObserver?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme", "style"],
    })
    return () => {
      media?.removeEventListener?.("change", refresh)
      themeObserver?.disconnect()
    }
  }, [])

  useEffect(() => {
    const element = hostRef.current
    if (element === null) return
    let disposed = false
    let observer: ResizeObserver | undefined
    let runtime: ComboChartRuntime | undefined

    const start = async () => {
      try {
        const reducedMotion =
          typeof window.matchMedia === "function" &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches
        const options = buildComboChartOptions({
          chart,
          data,
          tokens: themeTokens(element),
          reducedMotion,
        })
        const { createComboChart } = await import("./chartRuntime")
        if (disposed) return
        runtime = createComboChart(element)
        runtime.setOption(options)
        runtimeRef.current = runtime
        setRendered(true)
        if (typeof ResizeObserver !== "undefined") {
          observer = new ResizeObserver(() => runtime?.resize())
          observer.observe(element)
        }
        setError(null)
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : "Unable to render chart.")
      }
    }
    void start()
    return () => {
      disposed = true
      observer?.disconnect()
      runtime?.dispose()
      runtimeRef.current = null
      setRendered(false)
    }
  }, [chart, data, environmentRevision])

  const downloadImage = () => {
    const runtime = runtimeRef.current
    const host = hostRef.current
    if (!runtime || !host) return
    let dataUrl: string
    try {
      dataUrl = runtime.getDataURL()
    } catch (cause) {
      setExportError(
        cause instanceof Error ? cause.message : "Could not export the chart image.",
      )
      return
    }
    const image = new Image()
    image.onload = () => {
      try {
        const width = image.naturalWidth || host.clientWidth
        const height = image.naturalHeight || host.clientHeight
        if (!width || !height) {
          throw new Error("The rendered chart has no measurable size.")
        }
        const canvas = document.createElement("canvas")
        canvas.width = width * 2
        canvas.height = height * 2
        const context = canvas.getContext("2d")
        if (!context) {
          throw new Error("A 2D canvas context is unavailable.")
        }
        // The SVG has a transparent backdrop: paint the resolved theme
        // background first so the PNG matches the on-screen rendering.
        context.fillStyle = themeTokens(host).background
        context.fillRect(0, 0, canvas.width, canvas.height)
        context.drawImage(image, 0, 0, canvas.width, canvas.height)
        const anchor = document.createElement("a")
        anchor.href = canvas.toDataURL("image/png")
        anchor.download = chartExportFileName(chart.name)
        anchor.click()
        setExportError(null)
      } catch (cause) {
        setExportError(
          cause instanceof Error
            ? cause.message
            : "Could not export the chart image.",
        )
      }
    }
    image.onerror = () => {
      setExportError("Could not export the chart image.")
    }
    image.src = dataUrl
  }

  const summary = `${chart.name}: ${data.categories.length} categories and ${data.series.length} series.`
  return (
    <section
      aria-labelledby={summaryId}
      className="flex flex-col gap-2 px-2 pb-2"
    >
      <p id={summaryId} className="sr-only">
        {summary}
      </p>
      {error !== null && (
        <div
          role="alert"
          className="rounded px-2 py-1.5 text-[11px]"
          style={{ color: "var(--danger)", background: "var(--danger-soft)" }}
        >
          {error}
        </div>
      )}
      <div
        ref={hostRef}
        style={{ minHeight: 280, width: "100%" }}
      />
      {data.warnings.length > 0 && (
        <ul
          role="status"
          className="rounded px-2 py-1 text-[10px]"
          style={{ color: "var(--warning)", background: "var(--warning-soft)" }}
        >
          {data.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {exportError !== null && (
        <div
          role="alert"
          className="rounded px-2 py-1.5 text-[11px]"
          style={{ color: "var(--danger)", background: "var(--danger-soft)" }}
        >
          {exportError}
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-expanded={showTable}
          aria-controls={tableId}
          onClick={() => setShowTable((visible) => !visible)}
          className="rounded px-2 py-1 text-[11px] font-semibold"
          style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          {showTable ? "Hide data table" : "Show data table"}
        </button>
        <button
          type="button"
          aria-label={`Download ${chart.name} image`}
          disabled={!rendered}
          onClick={downloadImage}
          className="rounded px-2 py-1 text-[11px] font-semibold disabled:opacity-50"
          style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          Download image
        </button>
      </div>
      {showTable && (
        <div className="max-h-72 overflow-auto rounded" style={{ border: "1px solid var(--border)" }}>
          <table
            id={tableId}
            aria-label={`${chart.name} data`}
            className="min-w-full border-collapse text-left text-[11px]"
          >
            <thead className="sticky top-0" style={{ background: "var(--bg-panel)" }}>
              <tr>
                <th scope="col" className="whitespace-nowrap px-2 py-1.5">
                  Category
                </th>
                {data.series.map((series) => (
                  <th scope="col" key={series.key} className="whitespace-nowrap px-2 py-1.5">
                    {series.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.categories.map((category, rowIndex) => (
                <tr key={category.key} style={{ borderTop: "1px solid var(--border)" }}>
                  <th scope="row" className="whitespace-nowrap px-2 py-1.5 font-medium">
                    {category.label}
                  </th>
                  {data.series.map((series) => (
                    <td key={series.key} className="whitespace-nowrap px-2 py-1.5 tabular-nums">
                      {series.formattedValues[rowIndex] ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
