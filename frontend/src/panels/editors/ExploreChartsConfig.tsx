import { useState } from "react"
import { ArrowLeft } from "lucide-react"

import useNodeResultsStore, { explorePivotResultKey } from "../../stores/useNodeResultsStore"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import {
  applyChartPreset,
  createExploreChart,
  parseExploreCharts,
  seedValueEncodings,
  type ChartAxis,
  type ChartMark,
  type ChartNumberFormat,
  type ChartPreset,
  type ChartStyle,
  type ExploreChartConfig,
} from "../explore/chartConfig"
import { adaptPivotChartData } from "../explore/chartData"
import {
  isPivotResultFresh,
  parseExplorePivots,
  pivotCalculationIdentity,
  type ExplorePivotConfig,
  type PivotResultFreshnessEntry,
} from "../explore/pivotConfig"
import { INPUT_STYLE } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import {
  ExploreConfigCard,
  ExploreConfigCardEmptyState,
  ExploreConfigCardListHeader,
} from "./ExploreConfigCardList"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeId: string
  /**
   * Hash of the node's current Explore cache identity (graph + source), or
   * null when unknown. A retained Explore result only counts as current when
   * its configHash matches — the same gate the Explore preview applies.
   */
  currentConfigHash: string | null
  onShowPivots?: () => void
}
const presets: ChartPreset[] = [
  "clustered_columns",
  "stacked_columns",
  "lines",
  "column_line",
  "column_line_secondary",
  "stacked_column_line",
]
const formats: ChartNumberFormat[] = [
  "inherit",
  "number",
  "integer",
  "percent",
  "currency_gbp",
  "currency_usd",
  "currency_eur",
]

type PivotSourceStatus =
  | "unconfigured"
  | "loading"
  | "error"
  | "not_calculated"
  | "ready"
  | "stale"

const SOURCE_STATUS_LABELS: Readonly<Record<PivotSourceStatus, string>> = {
  unconfigured: "Unconfigured",
  loading: "Loading",
  error: "Error",
  not_calculated: "Not calculated",
  ready: "Ready",
  stale: "Stale",
}

function pivotSourceStatus(
  pivot: ExplorePivotConfig,
  entry: (PivotResultFreshnessEntry & { error?: string }) | undefined,
  hasActiveJob: boolean,
  currentDataframeKey: string | null,
): PivotSourceStatus {
  if (pivot.values.length === 0) return "unconfigured"
  if (hasActiveJob) return "loading"
  if (entry?.error && !entry.result) return "error"
  if (!entry?.result) return "not_calculated"
  return isPivotResultFresh(entry, currentDataframeKey, pivotCalculationIdentity(pivot))
    ? "ready"
    : "stale"
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>
      <span>{label}</span>
      {children}
    </label>
  )
}

function ConfigError({ error }: { error: string }) {
  return (
    <div data-testid="explore-charts-config" className="px-4 py-3">
      <div
        role="alert"
        className="rounded-lg px-3 py-2 text-xs"
        style={{
          color: "var(--danger)",
          background: "var(--danger-soft)",
          border: "1px solid var(--danger-border)",
        }}
      >
        {error}
      </div>
    </div>
  )
}

function StyleControls({
  style,
  suffix,
  onChange,
}: {
  style: ChartStyle
  suffix: string
  onChange: (change: Partial<ChartStyle>) => void
}) {
  const control = "rounded px-1.5 py-1 text-xs"
  return (
    <div
      className="grid grid-cols-2 gap-2 rounded p-2"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <Field label={`Mark for ${suffix}`}>
        <select
          aria-label={`Mark for ${suffix}`}
          className={control}
          style={INPUT_STYLE}
          value={style.mark}
          onChange={(e) => {
            const mark = e.target.value as ChartMark
            onChange({ mark, ...(mark === "column" ? {} : { stack_group: null }) })
          }}
        >
          <option value="column">Column</option>
          <option value="line">Line</option>
          <option value="area">Area</option>
        </select>
      </Field>
      <Field label={`Axis for ${suffix}`}>
        <select
          aria-label={`Axis for ${suffix}`}
          className={control}
          style={INPUT_STYLE}
          value={style.axis}
          onChange={(e) => onChange({ axis: e.target.value as ChartAxis })}
        >
          <option value="primary">Primary</option>
          <option value="secondary">Secondary</option>
        </select>
      </Field>
      <Field label={`Stack group for ${suffix}`}>
        <input
          aria-label={`Stack group for ${suffix}`}
          disabled={style.mark !== "column"}
          className={control}
          style={INPUT_STYLE}
          value={style.stack_group ?? ""}
          onChange={(e) => onChange({ stack_group: e.target.value.trim() || null })}
        />
      </Field>
      <ColourControl
        key={style.color ?? "automatic"}
        suffix={suffix}
        value={style.color}
        onCommit={(color) => onChange({ color })}
      />
      <label className="text-[11px]">
        <input
          type="checkbox"
          aria-label={`Markers for ${suffix}`}
          checked={style.markers}
          onChange={(e) => onChange({ markers: e.target.checked })}
        />{" "}
        Markers for {suffix}
      </label>
      <label className="text-[11px]">
        <input
          type="checkbox"
          aria-label={`Data labels for ${suffix}`}
          checked={style.data_labels}
          onChange={(e) => onChange({ data_labels: e.target.checked })}
        />{" "}
        Data labels for {suffix}
      </label>
    </div>
  )
}

function ColourControl({
  suffix,
  value,
  onCommit,
}: {
  suffix: string
  value: string | null
  onCommit: (value: string | null) => void
}) {
  const [draft, setDraft] = useState(value ?? "")
  const [error, setError] = useState<string | null>(null)

  const commit = () => {
    const next = draft.trim()
    if (next && !/^#[0-9A-Fa-f]{6}$/.test(next)) {
      setError("Colour must be a complete #RRGGBB value.")
      return
    }
    setError(null)
    const normalized = next ? next.toUpperCase() : null
    if (normalized !== value) onCommit(normalized)
  }

  return (
    <div>
      <Field label={`Colour for ${suffix}`}>
        <input
          aria-label={`Colour for ${suffix}`}
          className="rounded px-1.5 py-1 text-xs"
          style={INPUT_STYLE}
          value={draft}
          placeholder="#RRGGBB or automatic"
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault()
              commit()
            }
          }}
        />
      </Field>
      {error && (
        <div role="alert" className="mt-1 text-[10px]" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      )}
    </div>
  )
}

function AxisBoundInput({
  axis,
  bound,
  value,
  other,
  onCommit,
}: {
  axis: "primary" | "secondary"
  bound: "minimum" | "maximum"
  value: number | null
  other: number | null
  onCommit: (value: number | null) => void
}) {
  const label = `${axis === "primary" ? "Primary" : "Secondary"} ${bound}`
  const [draft, setDraft] = useState(value === null ? "" : String(value))
  const [error, setError] = useState<string | null>(null)

  const commit = () => {
    const next = draft.trim() === "" ? null : Number(draft)
    if (next !== null && !Number.isFinite(next)) {
      setError("Axis bounds must be finite numbers.")
      return
    }
    if (
      next !== null &&
      other !== null &&
      (bound === "minimum" ? next >= other : other >= next)
    ) {
      setError("Axis minimum must be less than maximum.")
      return
    }
    setError(null)
    if (next !== value) onCommit(next)
  }

  return (
    <div>
      <Field label={label}>
        <input
          type="number"
          step="any"
          aria-label={label}
          className="rounded px-2 py-1 text-xs"
          style={INPUT_STYLE}
          value={draft}
          placeholder="Automatic"
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault()
              commit()
            }
          }}
        />
      </Field>
      {error && (
        <div role="alert" className="mt-1 text-[10px]" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      )}
    </div>
  )
}

export default function ExploreChartsConfig({
  config,
  onUpdate,
  nodeId,
  currentConfigHash,
  onShowPivots,
}: Props) {
  const [configuredId, setConfiguredId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const pivotResults = useNodeResultsStore((s) => s.pivotResults)
  const pivotJobs = useNodeResultsStore((s) => s.pivotJobs)
  const retainedExplore = useNodeResultsStore((s) => s.exploreResults[nodeId] ?? null)
  const currentDataframeKey =
    retainedExplore !== null &&
    currentConfigHash !== null &&
    retainedExplore.configHash === currentConfigHash
      ? (retainedExplore.result?.dataframe_cache_key ?? null)
      : null
  const parsedCharts = parseExploreCharts(config)
  const parsedPivots = parseExplorePivots(config)
  if (!parsedCharts.ok) return <ConfigError error={parsedCharts.error} />
  if (!parsedPivots.ok) return <ConfigError error={parsedPivots.error} />
  const charts = parsedCharts.charts,
    pivots = parsedPivots.pivots
  const chart = configuredId ? charts.find((c) => c.id === configuredId) : undefined
  const commit = (next: ExploreChartConfig) =>
    onUpdate(
      "charts",
      charts.map((c) => (c.id === next.id ? next : c)),
    )

  if (!chart)
    return (
      <div data-testid="explore-charts-config" className="px-4 py-3 flex flex-col gap-3">
        <ExploreConfigCardListHeader
          title="Charts"
          description="Toggle charts shown in the visualisation area."
          addLabel="Add Chart"
          onAdd={() => onUpdate("charts", [...charts, createExploreChart(charts)])}
        />
        {charts.length === 0 ? (
          <ExploreConfigCardEmptyState>
            No charts yet. Add one to start building the visualisation area.
          </ExploreConfigCardEmptyState>
        ) : (
          <div className="flex flex-col gap-2">
            {charts.map((c) => (
              <ExploreConfigCard
              key={c.id}
                name={c.name}
                enabled={c.enabled}
                onEnabledChange={(enabled) =>
                  onUpdate(
                    "charts",
                    charts.map((x) => (x.id === c.id ? { ...x, enabled } : x)),
                  )
                }
                onDelete={() => {
                  if (!window.confirm(`Delete ${c.name}?`)) return
                  onUpdate("charts", charts.filter((candidate) => candidate.id !== c.id))
                }}
                onConfigure={() => {
                  setMessage(null)
                  setConfiguredId(c.id)
                }}
              />
            ))}
          </div>
        )}
      </div>
    )

  const pivot =
    chart.pivot_id === null ? null : (pivots.find((p) => p.id === chart.pivot_id) ?? null)
  const sourceMissing = chart.pivot_id !== null && !pivot
  const updateStyle = (
    collection: "value_encodings" | "series_overrides",
    id: string,
    change: Partial<ChartStyle>,
  ) =>
    commit({
      ...chart,
      [collection]: chart[collection].map((item) =>
        item.id === id ? { ...item, ...change } : item,
      ),
    })
  const selectPivot = (id: string) => {
    const next = pivots.find((p) => p.id === id)
    if (!next) return
    if (
      chart.pivot_id !== null &&
      (chart.value_encodings.length > 0 || chart.series_overrides.length > 0) &&
      !window.confirm("Changing the source Pivot resets chart mappings and series overrides.")
    )
      return
    commit({
      ...chart,
      pivot_id: next.id,
      value_encodings: seedValueEncodings(next),
      series_overrides: [],
    })
  }
  let data: ReturnType<typeof adaptPivotChartData> | null = null
  let dataError: string | null = null
  let sourceStatus: PivotSourceStatus = "unconfigured"
  if (pivot) {
    const key = explorePivotResultKey(nodeId, pivot.id)
    const entry = pivotResults[key]
    sourceStatus = pivotSourceStatus(
      pivot,
      entry,
      Boolean(pivotJobs[key]),
      currentDataframeKey,
    )
    if (sourceStatus === "ready" && entry?.result) {
      try {
        data = adaptPivotChartData(chart, pivot, entry.result)
      } catch (e) {
        dataError = e instanceof Error ? e.message : "Could not adapt Pivot data."
      }
    }
  }
  const nameCommit = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return setMessage("Chart name cannot be blank.")
    if (
      charts.some((c) => c.id !== chart.id && c.name.trim().toLowerCase() === trimmed.toLowerCase())
    )
      return setMessage("Chart name must be unique.")
    setMessage(null)
    if (trimmed !== chart.name) commit({ ...chart, name: trimmed })
  }
  const updateAxis = (axis: "primary" | "secondary", change: Record<string, unknown>) =>
    commit({ ...chart, axes: { ...chart.axes, [axis]: { ...chart.axes[axis], ...change } } })
  const firstUnused = () => {
    const ids = new Set([
      ...chart.value_encodings.map((encoding) => encoding.id),
      ...chart.series_overrides.map((override) => override.id),
    ])
    let n = 1
    while (ids.has(`override_${n}`)) n++
    return `override_${n}`
  }
  return (
    <div data-testid="explore-charts-config" className="px-4 py-3 flex flex-col gap-4">
      <button
        type="button"
        aria-label="Back to charts"
        onClick={() => setConfiguredId(null)}
        className="self-start text-xs"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft size={13} className="inline" /> Back to charts
      </button>
      <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
        Configure {chart.name}
      </h3>
      {message && (
        <div role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
          {message}
        </div>
      )}
      <Field label="Chart name">
        <input
          key={`${chart.id}:${chart.name}`}
          aria-label="Chart name"
          defaultValue={chart.name}
          className="rounded px-2 py-1 text-xs"
          style={INPUT_STYLE}
          onBlur={(e) => nameCommit(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              nameCommit(e.currentTarget.value)
              e.currentTarget.blur()
            }
          }}
        />
      </Field>
      {pivots.length === 0 ? (
        <div
          className="rounded p-3 text-xs"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        >
          This chart requires a Pivot.{" "}
          <button
            type="button"
            onClick={onShowPivots}
            className="font-semibold"
            style={{ color: NODE_GROUP_COLORS.explore }}
          >
            Go to Pivots
          </button>
        </div>
      ) : (
        <Field label="Source pivot">
          <select
            aria-label="Source pivot"
            value={chart.pivot_id ?? ""}
            className="rounded px-2 py-1 text-xs"
            style={INPUT_STYLE}
            onChange={(e) => selectPivot(e.target.value)}
          >
            <option value="" disabled>
              Select a Pivot
            </option>
            {pivots.map((p) => {
              const key = explorePivotResultKey(nodeId, p.id)
              const status = pivotSourceStatus(
                p,
                pivotResults[key],
                Boolean(pivotJobs[key]),
                currentDataframeKey,
              )
              return (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.enabled ? "" : " (Hidden)"}
                  {` — ${SOURCE_STATUS_LABELS[status]}`}
                </option>
              )
            })}
          </select>
        </Field>
      )}
      {sourceMissing && (
        <div role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
          The selected source Pivot no longer exists. Choose another Pivot.
        </div>
      )}
      {pivot && (
        <>
          <div
            className="rounded p-2 text-[11px]"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-secondary)",
            }}
          >
            Filters: {pivot.filters.map((x) => x.field).join(", ") || "None"}
            <br />
            Rows: {pivot.rows.map((x) => x.field).join(", ") || "None"}
            <br />
            Columns: {pivot.columns.map((x) => x.field).join(", ") || "None"}
            <br />
            Values: {pivot.values.map((x) => x.display_name).join(", ") || "None"}
          </div>
          {pivot.values.length === 0 ? (
            <div role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
              Add at least one Value to the source Pivot before configuring this chart.
            </div>
          ) : (
            <>
              <Field label="Chart preset">
                <select
                  aria-label="Chart preset"
                  className="rounded px-2 py-1 text-xs"
                  style={INPUT_STYLE}
                  value=""
                  onChange={(e) =>
                    commit(applyChartPreset(chart, e.target.value as ChartPreset, pivot))
                  }
                >
                  <option value="" disabled>
                    Apply a preset…
                  </option>
                  {presets.map((p) => (
                    <option key={p} value={p}>
                      {p.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </Field>
              {pivot.values.map((value) => {
                const encoding = chart.value_encodings.find((x) => x.value_id === value.id)
                return encoding ? (
                  <div key={value.id}>
                    <div
                      className="mb-1 text-xs font-semibold"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {value.display_name}
                    </div>
                    <StyleControls
                      style={encoding}
                      suffix={value.display_name}
                      onChange={(change) => updateStyle("value_encodings", encoding.id, change)}
                    />
                  </div>
                ) : (
                  <div role="alert" key={value.id}>
                    Missing encoding for {value.display_name}.
                  </div>
                )
              })}
            </>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Field label="Primary axis title">
              <input
                className="rounded px-2 py-1 text-xs"
                style={INPUT_STYLE}
                value={chart.axes.primary.title}
                onChange={(e) => updateAxis("primary", { title: e.target.value })}
              />
            </Field>
            <Field label="Secondary axis title">
              <input
                className="rounded px-2 py-1 text-xs"
                style={INPUT_STYLE}
                value={chart.axes.secondary.title}
                onChange={(e) => updateAxis("secondary", { title: e.target.value })}
              />
            </Field>
            {(["primary", "secondary"] as const).map((axis) => (
              <div key={axis} className="contents">
                <AxisBoundInput
                  key={`${axis}-minimum-${chart.axes[axis].minimum ?? "automatic"}`}
                  axis={axis}
                  bound="minimum"
                  value={chart.axes[axis].minimum}
                  other={chart.axes[axis].maximum}
                  onCommit={(value) => updateAxis(axis, { minimum: value })}
                />
                <AxisBoundInput
                  key={`${axis}-maximum-${chart.axes[axis].maximum ?? "automatic"}`}
                  axis={axis}
                  bound="maximum"
                  value={chart.axes[axis].maximum}
                  other={chart.axes[axis].minimum}
                  onCommit={(value) => updateAxis(axis, { maximum: value })}
                />
                <Field label={`${axis === "primary" ? "Primary" : "Secondary"} number format`}>
                  <select
                    aria-label={`${axis === "primary" ? "Primary" : "Secondary"} number format`}
                    className="rounded px-2 py-1 text-xs"
                    style={INPUT_STYLE}
                    value={chart.axes[axis].number_format}
                    onChange={(e) => updateAxis(axis, { number_format: e.target.value })}
                  >
                    {formats.map((f) => (
                      <option key={f}>{f}</option>
                    ))}
                  </select>
                </Field>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">
              <input
                type="checkbox"
                checked={chart.legend.visible}
                onChange={(e) =>
                  commit({ ...chart, legend: { ...chart.legend, visible: e.target.checked } })
                }
              />{" "}
              Legend visible
            </label>
            <Field label="Legend position">
              <select
                aria-label="Legend position"
                className="rounded px-2 py-1 text-xs"
                style={INPUT_STYLE}
                value={chart.legend.position}
                onChange={(e) =>
                  commit({
                    ...chart,
                    legend: {
                      ...chart.legend,
                      position: e.target.value as ExploreChartConfig["legend"]["position"],
                    },
                  })
                }
              >
                {["top", "right", "bottom", "left"].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </Field>
            <Field label="Category label rotation">
              <select
                aria-label="Category label rotation"
                className="rounded px-2 py-1 text-xs"
                style={INPUT_STYLE}
                value={chart.category.label_rotation}
                onChange={(e) =>
                  commit({
                    ...chart,
                    category: { ...chart.category, label_rotation: Number(e.target.value) },
                  })
                }
              >
                {[-90, -45, 0, 45, 90].map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
            </Field>
            <label className="text-xs">
              <input
                type="checkbox"
                checked={chart.category.include_grand_total}
                onChange={(e) =>
                  commit({
                    ...chart,
                    category: { ...chart.category, include_grand_total: e.target.checked },
                  })
                }
              />{" "}
              Include grand total
            </label>
          </div>
          {!data && !dataError && sourceStatus === "loading" && (
            <div role="status" className="text-xs" style={{ color: "var(--text-muted)" }}>
              The source Pivot is updating. Concrete series will refresh when it completes.
            </div>
          )}
          {!data && !dataError && sourceStatus === "stale" && (
            <div className="text-xs" style={{ color: "var(--warning)" }}>
              The source Pivot result is out of date. Update it to refresh concrete series.
            </div>
          )}
          {!data && !dataError && sourceStatus === "error" && (
            <div className="text-xs" style={{ color: "var(--danger)" }}>
              The source Pivot failed. Update it before configuring concrete series.
            </div>
          )}
          {!data &&
            !dataError &&
            (sourceStatus === "not_calculated" || sourceStatus === "unconfigured") && (
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
              Update is required to generate concrete series.
            </div>
            )}
          {dataError && (
            <div role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
              {dataError}
            </div>
          )}
          {data && (
            <div className="flex flex-col gap-2">
              <div className="text-xs font-semibold">Concrete series</div>
              {data.dormantOverrideIds.length > 0 && (
                <div role="alert" className="text-xs">
                  Dormant overrides: {data.dormantOverrideIds.join(", ")}
                </div>
              )}
              {data.dormantEncodingIds.length > 0 && (
                <div role="alert" className="text-xs">
                  Dormant encodings: {data.dormantEncodingIds.join(", ")}
                </div>
              )}
              {data.series.map((series) => {
                const override = chart.series_overrides.find((x) => x.series_key === series.key)
                return override ? (
                  <div key={series.key} role="group" aria-label={`Override ${series.name}`}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-xs font-semibold">{series.name}</div>
                      <button
                        type="button"
                        aria-label={`Reset ${series.name} to Value default`}
                        onClick={() =>
                          commit({
                            ...chart,
                            series_overrides: chart.series_overrides.filter(
                              (candidate) => candidate.id !== override.id,
                            ),
                          })
                        }
                        className="text-[10px] font-semibold"
                        style={{ color: "var(--text-secondary)" }}
                      >
                        Use Value default
                      </button>
                    </div>
                    <StyleControls
                      style={override}
                      suffix={`${series.name} exact series`}
                      onChange={(change) => updateStyle("series_overrides", override.id, change)}
                    />
                  </div>
                ) : (
                  <div key={series.key} className="flex items-center justify-between text-xs">
                    <span>{series.name}</span>
                    <button
                      type="button"
                      aria-label={`Override ${series.name}`}
                      onClick={() =>
                        commit({
                          ...chart,
                          series_overrides: [
                            ...chart.series_overrides,
                            {
                              id: firstUnused(),
                              series_key: series.key,
                              mark: series.style.mark,
                              axis: series.style.axis,
                              stack_group: series.style.stack_group,
                              color: series.style.color,
                              data_labels: series.style.data_labels,
                              markers: series.style.markers,
                            },
                          ],
                        })
                      }
                    >
                      Override {series.name}
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
