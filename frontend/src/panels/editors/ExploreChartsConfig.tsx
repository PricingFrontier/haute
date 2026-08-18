import { useEffect, useMemo, useState } from "react"
import {
  ArrowLeft,
  ChartColumn,
  ChartColumnStacked,
  ChartLine,
} from "lucide-react"

import useNodeResultsStore, { explorePivotResultKey } from "../../stores/useNodeResultsStore"
import useUIStore from "../../stores/useUIStore"
import { NODE_GROUP_COLORS, PIVOT_CHART_COLORS } from "../../theme/colors"
import {
  applyChartPreset,
  chartStackingMode,
  createExploreChart,
  detectChartPreset,
  exploreChartSeriesLabel,
  parseExploreCharts,
  reconcileValueEncodings,
  renameChartStackGroup,
  seedValueEncodings,
  setChartStacking,
  setChartStyleAxis,
  setSecondaryAxisEnabled,
  type ChartAxis,
  type ChartAxisConfig,
  type ChartMark,
  type ChartNumberFormat,
  type ChartPreset,
  type ChartSeriesOverride,
  type ChartStackingMode,
  type ChartStyle,
  type ExploreChartConfig,
} from "../explore/chartConfig"
import { adaptPivotChartData, type ChartSeriesData } from "../explore/chartData"
import {
  isPivotResultFresh,
  parseExplorePivots,
  pivotCalculationIdentity,
  pivotOutputs,
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
import { useGraph } from "../useGraph"
import type { SimpleNode } from "../editors"
import useAutoUpdateExplorePivots from "../explore/useAutoUpdateExplorePivots"
import useExplorePivotActions from "../explore/useExplorePivotActions"

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

/**
 * Mounts the shared per-pivot auto-update scheduler for the configured
 * chart's resolved source, so opening Configure refreshes an already-stale
 * source even when neither result pane is mounted. Claim-serialised with
 * any mounted pane.
 */
function ConfigureSourceScheduler({
  node,
  pivot,
  currentConfigHash,
}: {
  node: SimpleNode
  pivot: ExplorePivotConfig
  currentConfigHash: string | null
}) {
  const graph = useGraph()
  const retained = useNodeResultsStore(
    (s) => s.exploreResults[node.id] ?? null,
  )
  const report =
    retained !== null &&
    currentConfigHash !== null &&
    retained.configHash === currentConfigHash
      ? (retained.result ?? null)
      : null
  const { submitting, updatePivot } = useExplorePivotActions({
    node,
    allNodes: graph.allNodes,
    edges: graph.edges,
    submodels: graph.submodels,
    preamble: graph.preamble,
  })
  const pivots = useMemo(() => [pivot], [pivot])
  useAutoUpdateExplorePivots({
    nodeId: node.id,
    pivots,
    report,
    submitting,
    updatePivot,
  })
  return null
}

function SourceSchedulerMount({
  nodeId,
  pivot,
  currentConfigHash,
}: {
  nodeId: string
  pivot: ExplorePivotConfig
  currentConfigHash: string | null
}) {
  const graph = useGraph()
  const node = graph.allNodes.find((candidate) => candidate.id === nodeId)
  if (!node) return null
  return (
    <ConfigureSourceScheduler
      node={node}
      pivot={pivot}
      currentConfigHash={currentConfigHash}
    />
  )
}
const presets: ChartPreset[] = [
  "combo",
  "clustered_columns",
  "stacked_columns",
  "hundred_percent_stacked_columns",
]
const PRESET_LABELS: Readonly<Record<ChartPreset, string>> = {
  clustered_columns: "Clustered columns",
  stacked_columns: "Stacked columns",
  hundred_percent_stacked_columns: "100% stacked columns",
  combo: "Combo",
}
const PRESET_ICONS: Readonly<Record<ChartPreset, typeof ChartColumn>> = {
  clustered_columns: ChartColumn,
  stacked_columns: ChartColumnStacked,
  hundred_percent_stacked_columns: ChartColumnStacked,
  combo: ChartLine,
}
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
  multiGroup,
  secondaryEnabled,
  onChange,
  onAxisChange,
  onStackingChange,
  onGroupRename,
}: {
  style: ChartStyle
  suffix: string
  multiGroup: boolean
  secondaryEnabled: boolean
  onChange: (change: Partial<ChartStyle>) => void
  onAxisChange: (axis: ChartAxis) => void
  onStackingChange: (mode: ChartStackingMode) => void
  onGroupRename: (name: string) => string | null
}) {
  const control = "rounded px-1.5 py-1 text-xs"
  return (
    <div
      className="grid grid-cols-2 gap-2 rounded p-2"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <Field label={`Chart type for ${suffix}`}>
        <select
          aria-label={`Chart type for ${suffix}`}
          className={control}
          style={INPUT_STYLE}
          value={style.mark}
          onChange={(e) => onChange({ mark: e.target.value as ChartMark })}
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
          onChange={(e) => onAxisChange(e.target.value as ChartAxis)}
        >
          <option value="primary">Primary</option>
          {secondaryEnabled && <option value="secondary">Secondary</option>}
        </select>
      </Field>
      <Field label={`Stacking for ${suffix}`}>
        <select
          aria-label={`Stacking for ${suffix}`}
          className={control}
          style={INPUT_STYLE}
          value={chartStackingMode(style)}
          onChange={(e) => onStackingChange(e.target.value as ChartStackingMode)}
        >
          <option value="none">None</option>
          <option value="stacked">Stacked</option>
          <option value="normalized">100% stacked</option>
        </select>
      </Field>
      {multiGroup && style.stack_group !== null ? (
        <StackGroupInput
          key={style.stack_group}
          suffix={suffix}
          group={style.stack_group}
          onRename={onGroupRename}
        />
      ) : (
        <div aria-hidden="true" />
      )}
      <ColourControl
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

function StackGroupInput({
  suffix,
  group,
  onRename,
}: {
  suffix: string
  group: string
  onRename: (name: string) => string | null
}) {
  const [draft, setDraft] = useState(group)
  const [error, setError] = useState<string | null>(null)

  const commit = () => {
    if (draft.trim() === group) {
      setError(null)
      return
    }
    setError(onRename(draft))
  }

  return (
    <div>
      <Field label={`Stack group for ${suffix}`}>
        <input
          aria-label={`Stack group for ${suffix}`}
          className="rounded px-1.5 py-1 text-xs"
          style={INPUT_STYLE}
          value={draft}
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

function AxisFields({
  axis,
  config,
  updateAxis,
}: {
  axis: "primary" | "secondary"
  config: ChartAxisConfig
  updateAxis: (
    axis: "primary" | "secondary",
    change: Record<string, unknown>,
  ) => void
}) {
  const prefix = axis === "primary" ? "Primary" : "Secondary"
  return (
    <div className="grid grid-cols-2 gap-2">
      <Field label={`${prefix} axis title`}>
        <input
          className="rounded px-2 py-1 text-xs"
          style={INPUT_STYLE}
          value={config.title}
          onChange={(e) => updateAxis(axis, { title: e.target.value })}
        />
      </Field>
      <Field label={`${prefix} number format`}>
        <select
          aria-label={`${prefix} number format`}
          className="rounded px-2 py-1 text-xs"
          style={INPUT_STYLE}
          value={config.number_format}
          onChange={(e) => updateAxis(axis, { number_format: e.target.value })}
        >
          {formats.map((f) => (
            <option key={f} value={f}>
              {f === "inherit" ? "General (automatic)" : f}
            </option>
          ))}
        </select>
      </Field>
      <AxisBoundInput
        key={`${axis}-minimum-${config.minimum ?? "automatic"}`}
        axis={axis}
        bound="minimum"
        value={config.minimum}
        other={config.maximum}
        onCommit={(value) => updateAxis(axis, { minimum: value })}
      />
      <AxisBoundInput
        key={`${axis}-maximum-${config.maximum ?? "automatic"}`}
        axis={axis}
        bound="maximum"
        value={config.maximum}
        other={config.minimum}
        onCommit={(value) => updateAxis(axis, { maximum: value })}
      />
    </div>
  )
}

function AxisFormattingBox({
  title,
  axis,
  config,
  updateAxis,
}: {
  title: string
  axis: "primary" | "secondary"
  config: ChartAxisConfig
  updateAxis: (
    axis: "primary" | "secondary",
    change: Record<string, unknown>,
  ) => void
}) {
  return (
    <div
      role="group"
      aria-label={title}
      className="flex flex-col gap-2 rounded p-2"
      style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
    >
      <div className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
        {title}
      </div>
      <AxisFields axis={axis} config={config} updateAxis={updateAxis} />
    </div>
  )
}

function ValueSeriesOverrides({
  valueName,
  series,
  overrides,
  multiGroup,
  secondaryEnabled,
  onCreateOverride,
  onRemoveOverride,
  onStyleChange,
  onAxisChange,
  onStackingChange,
  onGroupRename,
}: {
  valueName: string
  series: ChartSeriesData[]
  overrides: readonly ChartSeriesOverride[]
  multiGroup: boolean
  secondaryEnabled: boolean
  onCreateOverride: (series: ChartSeriesData) => void
  onRemoveOverride: (overrideId: string) => void
  onStyleChange: (overrideId: string, change: Partial<ChartStyle>) => void
  onAxisChange: (overrideId: string, axis: ChartAxis) => void
  onStackingChange: (overrideId: string, mode: ChartStackingMode) => void
  onGroupRename: (overrideId: string, name: string) => string | null
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`Series overrides for ${valueName}`}
        onClick={() => setOpen((visible) => !visible)}
        className="text-[10px] font-semibold"
        style={{ color: "var(--text-secondary)" }}
      >
        {open ? "▾" : "▸"} Series overrides ({series.length})
      </button>
      {open && (
        <div className="mt-1 flex flex-col gap-2 pl-3">
          {series.map((entry) => {
            const override = overrides.find(
              (candidate) => candidate.series_key === entry.key,
            )
            return override ? (
              <div key={entry.key} role="group" aria-label={`Override ${entry.name}`}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-semibold">{entry.name}</div>
                  <button
                    type="button"
                    aria-label={`Reset ${entry.name} to Value default`}
                    onClick={() => onRemoveOverride(override.id)}
                    className="text-[10px] font-semibold"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    Use Value default
                  </button>
                </div>
                <StyleControls
                  style={override}
                  suffix={`${entry.name} exact series`}
                  multiGroup={multiGroup}
                  secondaryEnabled={secondaryEnabled}
                  onChange={(change) => onStyleChange(override.id, change)}
                  onAxisChange={(axis) => onAxisChange(override.id, axis)}
                  onStackingChange={(mode) => onStackingChange(override.id, mode)}
                  onGroupRename={(name) => onGroupRename(override.id, name)}
                />
              </div>
            ) : (
              <div
                key={entry.key}
                className="flex items-center justify-between text-xs"
              >
                <span>{entry.name}</span>
                <button
                  type="button"
                  aria-label={`Override ${entry.name}`}
                  onClick={() => onCreateOverride(entry)}
                >
                  Override {entry.name}
                </button>
              </div>
            )
          })}
        </div>
      )}
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
  const swatch = "h-5 w-5 rounded border"
  return (
    <div className="col-span-2">
      <Field label={`Colour for ${suffix}`}>
        <div className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            aria-label={`Automatic colour for ${suffix}`}
            aria-pressed={value === null}
            onClick={() => onCommit(null)}
            className="rounded px-1.5 py-0.5 text-[10px] font-semibold"
            style={{
              border: `1px solid ${value === null ? "var(--text-secondary)" : "var(--border)"}`,
              color: "var(--text-secondary)",
            }}
          >
            Automatic
          </button>
          {PIVOT_CHART_COLORS.defaultSeries.map((hex) => (
            <button
              key={hex}
              type="button"
              aria-label={`Colour ${hex} for ${suffix}`}
              aria-pressed={value === hex}
              onClick={() => onCommit(hex)}
              className={swatch}
              style={{
                background: hex,
                borderColor:
                  value === hex ? "var(--text-primary)" : "var(--border)",
              }}
            />
          ))}
          <CustomColourInput
            key={value ?? "automatic"}
            suffix={suffix}
            value={value}
            onCommit={onCommit}
          />
        </div>
      </Field>
    </div>
  )
}

function CustomColourInput({
  suffix,
  value,
  onCommit,
}: {
  suffix: string
  value: string | null
  onCommit: (value: string) => void
}) {
  // The native picker fires an input event per drag tick; committing each
  // tick would persist one graph edit (and one undo step) per tick. Track a
  // local draft and commit once on blur, like the other committed controls.
  // The parent remounts this input (keyed on the committed value) whenever
  // an external change lands, discarding the stale draft.
  const [draft, setDraft] = useState<string | null>(null)
  return (
    <input
      type="color"
      aria-label={`Custom colour for ${suffix}`}
      className="h-5 w-7 cursor-pointer rounded border"
      style={{ borderColor: "var(--border)", padding: 0 }}
      value={draft ?? value ?? PIVOT_CHART_COLORS.defaultSeries[0]}
      onChange={(event) => setDraft(event.target.value.toUpperCase())}
      onBlur={() => {
        if (draft !== null && draft !== value) onCommit(draft)
      }}
    />
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
  const configuredId = useUIStore(
    (s) => s.exploreConfiguredChartIds[nodeId] ?? null,
  )
  const setExploreConfiguredChart = useUIStore((s) => s.setExploreConfiguredChart)
  const [message, setMessage] = useState<string | null>(null)
  const pivotResults = useNodeResultsStore((s) => s.pivotResults)
  const pivotJobs = useNodeResultsStore((s) => s.pivotJobs)
  const retainedExplore = useNodeResultsStore((s) => s.exploreResults[nodeId] ?? null)
  // Parsing deep-clones every card and this editor re-renders on pivot
  // polling ticks, so parse once per config identity, as the Charts pane does.
  const parsedCharts = useMemo(() => parseExploreCharts(config), [config])
  const parsedPivots = useMemo(() => parseExplorePivots(config), [config])
  const configuredChartExists =
    !parsedCharts.ok ||
    configuredId === null ||
    parsedCharts.charts.some(({ id }) => id === configuredId)
  useEffect(() => {
    // A stored subview id whose card was deleted (from any surface) clears
    // itself so a later card reusing the id cannot reopen unexpectedly.
    if (!configuredChartExists) setExploreConfiguredChart(nodeId, null)
  }, [configuredChartExists, nodeId, setExploreConfiguredChart])
  const currentDataframeKey =
    retainedExplore !== null &&
    currentConfigHash !== null &&
    retainedExplore.configHash === currentConfigHash
      ? (retainedExplore.result?.dataframe_cache_key ?? null)
      : null
  if (!parsedCharts.ok) return <ConfigError error={parsedCharts.error} />
  if (!parsedPivots.ok) return <ConfigError error={parsedPivots.error} />
  const charts = parsedCharts.charts,
    pivots = parsedPivots.pivots
  const persistedChart = configuredId
    ? charts.find((c) => c.id === configuredId)
    : undefined
  const commit = (next: ExploreChartConfig) =>
    onUpdate(
      "charts",
      charts.map((c) => (c.id === next.id ? next : c)),
    )

  if (!persistedChart)
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
                  if (configuredId === c.id) setExploreConfiguredChart(nodeId, null)
                  onUpdate("charts", charts.filter((candidate) => candidate.id !== c.id))
                }}
                onConfigure={() => {
                  setMessage(null)
                  setExploreConfiguredChart(nodeId, c.id)
                }}
              />
            ))}
          </div>
        )}
      </div>
    )

  const pivot =
    persistedChart.pivot_id === null
      ? null
      : (pivots.find((p) => p.id === persistedChart.pivot_id) ?? null)
  const sourceMissing = persistedChart.pivot_id !== null && !pivot
  // The editor view reconciles pivot Values the persisted chart does not yet
  // encode; any committed edit persists the seeded encodings as one step.
  const chart = pivot
    ? reconcileValueEncodings(persistedChart, pivot)
    : persistedChart
  const persistedEncodingIds = new Set(
    persistedChart.value_encodings.map(({ id }) => id),
  )
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
  const commitStacking = (styleId: string, mode: ChartStackingMode) => {
    const next = setChartStacking(chart, styleId, mode)
    if (next !== chart) commit(next)
  }
  const commitStyleAxis = (styleId: string, axis: ChartAxis) => {
    const next = setChartStyleAxis(chart, styleId, axis)
    if (next !== chart) commit(next)
  }
  const commitGroupRename = (styleId: string, name: string): string | null => {
    const next = renameChartStackGroup(chart, styleId, name)
    if (typeof next === "string") return next
    if (next !== chart) commit(next)
    return null
  }
  const multiGroup =
    new Set(
      [...chart.value_encodings, ...chart.series_overrides]
        .map(({ stack_group }) => stack_group)
        .filter((group): group is string => group !== null),
    ).size > 1
  const detectedPreset = detectChartPreset(chart)
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
        onClick={() => setExploreConfiguredChart(nodeId, null)}
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
            {pivots.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.enabled ? "" : " (Hidden)"}
              </option>
            ))}
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
          <SourceSchedulerMount
            nodeId={nodeId}
            pivot={pivot}
            currentConfigHash={currentConfigHash}
          />
          {pivot.values.length === 0 ? (
            <div role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
              Add at least one Value to the source Pivot before configuring this chart.
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-1">
                <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                  Chart type
                </span>
                <div
                  role="group"
                  aria-label="Chart type"
                  className="flex flex-wrap items-center gap-1"
                >
                  {presets.map((p) => {
                    const Icon = PRESET_ICONS[p]
                    const active = detectedPreset === p
                    return (
                      <button
                        key={p}
                        type="button"
                        aria-pressed={active}
                        onClick={() => commit(applyChartPreset(chart, p, pivot))}
                        className="focus-ring inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] font-semibold"
                        style={{
                          border: `1px solid ${active ? NODE_GROUP_COLORS.explore : "var(--border)"}`,
                          color: active
                            ? NODE_GROUP_COLORS.explore
                            : "var(--text-secondary)",
                        }}
                      >
                        <Icon size={12} aria-hidden="true" />
                        {PRESET_LABELS[p]}
                      </button>
                    )
                  })}
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                  Orientation
                </span>
                <div
                  role="group"
                  aria-label="Orientation"
                  className="flex items-center gap-1"
                >
                  {(
                    [
                      ["vertical", "Vertical columns"],
                      ["horizontal", "Horizontal bars"],
                    ] as const
                  ).map(([orientation, label]) => {
                    const active = chart.orientation === orientation
                    return (
                      <button
                        key={orientation}
                        type="button"
                        aria-pressed={active}
                        onClick={() =>
                          active ? undefined : commit({ ...chart, orientation })
                        }
                        className="focus-ring rounded px-1.5 py-1 text-[10px] font-semibold"
                        style={{
                          border: `1px solid ${active ? NODE_GROUP_COLORS.explore : "var(--border)"}`,
                          color: active
                            ? NODE_GROUP_COLORS.explore
                            : "var(--text-secondary)",
                        }}
                      >
                        {label}
                      </button>
                    )
                  })}
                </div>
              </div>
              <AxisFormattingBox
                title="Primary axis"
                axis="primary"
                config={chart.axes.primary}
                updateAxis={updateAxis}
              />
              <div
                role="group"
                aria-label="Secondary axis"
                className="flex flex-col gap-2 rounded p-2"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                }}
              >
                <label
                  className="flex items-center gap-2 text-xs font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  <input
                    type="checkbox"
                    aria-label="Use secondary axis"
                    checked={chart.axes.secondary.enabled}
                    onChange={(e) =>
                      commit(setSecondaryAxisEnabled(chart, e.target.checked))
                    }
                  />
                  Secondary axis
                </label>
                {chart.axes.secondary.enabled && (
                  <AxisFields
                    axis="secondary"
                    config={chart.axes.secondary}
                    updateAxis={updateAxis}
                  />
                )}
              </div>
              <div
                role="group"
                aria-label="Legend"
                className="flex flex-col gap-2 rounded p-2"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                }}
              >
                <label
                  className="flex items-center gap-2 text-xs font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  <input
                    type="checkbox"
                    aria-label="Show legend"
                    checked={chart.legend.visible}
                    onChange={(e) =>
                      commit({
                        ...chart,
                        legend: { ...chart.legend, visible: e.target.checked },
                      })
                    }
                  />
                  Legend
                </label>
                {chart.legend.visible && (
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
                            position: e.target
                              .value as ExploreChartConfig["legend"]["position"],
                          },
                        })
                      }
                    >
                      {["top", "right", "bottom", "left"].map((x) => (
                        <option key={x}>{x}</option>
                      ))}
                    </select>
                  </Field>
                )}
              </div>
              {pivotOutputs(pivot).map((value) => {
                const encoding = chart.value_encodings.find((x) => x.value_id === value.id)
                if (!encoding) return null
                const valueSeries =
                  data?.series.filter((series) => series.valueId === value.id) ?? []
                const hasOverrides = valueSeries.some((series) =>
                  chart.series_overrides.some(
                    (override) => override.series_key === series.key,
                  ),
                )
                return (
                  <div key={value.id}>
                    <div
                      className="mb-1 text-xs font-semibold"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {value.display_name}
                    </div>
                    {!persistedEncodingIds.has(encoding.id) && (
                      <div
                        className="mb-1 text-[10px]"
                        style={{ color: "var(--text-muted)" }}
                      >
                        New Value from the source Pivot — defaults applied.
                      </div>
                    )}
                    <StyleControls
                      style={encoding}
                      suffix={value.display_name}
                      multiGroup={multiGroup}
                      secondaryEnabled={chart.axes.secondary.enabled}
                      onChange={(change) => updateStyle("value_encodings", encoding.id, change)}
                      onAxisChange={(axis) => commitStyleAxis(encoding.id, axis)}
                      onStackingChange={(mode) => commitStacking(encoding.id, mode)}
                      onGroupRename={(name) => commitGroupRename(encoding.id, name)}
                    />
                    {(pivot.columns.length > 0 || hasOverrides) &&
                      valueSeries.length > 0 && (
                        <ValueSeriesOverrides
                          valueName={value.display_name}
                          series={valueSeries}
                          overrides={chart.series_overrides}
                          multiGroup={multiGroup}
                          secondaryEnabled={chart.axes.secondary.enabled}
                          onCreateOverride={(series) =>
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
                                  stack_normalize: series.style.stack_normalize,
                                  color: series.style.color,
                                  data_labels: series.style.data_labels,
                                  markers: series.style.markers,
                                },
                              ],
                            })
                          }
                          onRemoveOverride={(overrideId) =>
                            commit({
                              ...chart,
                              series_overrides: chart.series_overrides.filter(
                                (candidate) => candidate.id !== overrideId,
                              ),
                            })
                          }
                          onStyleChange={(overrideId, change) =>
                            updateStyle("series_overrides", overrideId, change)
                          }
                          onAxisChange={commitStyleAxis}
                          onStackingChange={commitStacking}
                          onGroupRename={commitGroupRename}
                        />
                      )}
                  </div>
                )
              })}
            </>
          )}
          <div className="grid grid-cols-2 gap-2">
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
              The source Pivot is updating. Series will refresh when it completes.
            </div>
          )}
          {!data && !dataError && sourceStatus === "stale" && (
            <div className="text-xs" style={{ color: "var(--warning)" }}>
              The source Pivot result is out of date. Update it to refresh its series.
            </div>
          )}
          {!data && !dataError && sourceStatus === "error" && (
            <div className="text-xs" style={{ color: "var(--danger)" }}>
              The source Pivot failed. Update it before configuring series overrides.
            </div>
          )}
          {!data &&
            !dataError &&
            (sourceStatus === "not_calculated" || sourceStatus === "unconfigured") && (
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
              Update the source Pivot to discover its series.
            </div>
            )}
          {dataError && (
            <div role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
              {dataError}
            </div>
          )}
          {data && (
            <div className="flex flex-col gap-2">
              {data.dormantOverrideIds.length > 0 && (
                <div role="status" className="text-xs" style={{ color: "var(--text-muted)" }}>
                  Series formatting kept for series not currently shown:{" "}
                  {chart.series_overrides
                    .filter(({ id }) => data.dormantOverrideIds.includes(id))
                    .map(({ series_key }) =>
                      exploreChartSeriesLabel(series_key, pivot),
                    )
                    .join(", ")}
                  .
                </div>
              )}
              {data.dormantEncodingIds.length > 0 && (
                <div role="status" className="text-xs" style={{ color: "var(--text-muted)" }}>
                  Value formatting kept for Values no longer in the source
                  Pivot:{" "}
                  {chart.value_encodings
                    .filter(({ id }) => data.dormantEncodingIds.includes(id))
                    .map(
                      ({ value_id }) =>
                        pivotOutputs(pivot).find(({ id }) => id === value_id)
                          ?.display_name ?? "a removed Value",
                    )
                    .join(", ")}
                  .
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
