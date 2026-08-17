import { useEffect, useMemo, useState } from "react"
import { ArrowLeft } from "lucide-react"

import { EditorLabel } from "../../components/form"
import useUIStore from "../../stores/useUIStore"
import { PIVOT_CONDITIONAL_FORMAT_COLORS } from "../../theme/colors"
import {
  dependentChartsForPivot,
  parseExploreCharts,
  type ExploreChartConfig,
} from "../explore/chartConfig"
import {
  PIVOT_AGGREGATION_LABELS,
  createExplorePivot,
  parseExplorePivots,
  isNumericPivotDtype,
} from "../explore/pivotConfig"
import type { ExplorePivotConfig, PivotValuePlacement } from "../explore/pivotConfig"
import { INPUT_STYLE } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import {
  ExploreConfigCard,
  ExploreConfigCardEmptyState,
  ExploreConfigCardListHeader,
} from "./ExploreConfigCardList"
import PivotFieldWell from "./explorePivots/PivotFieldWell"
import PivotFormattingSection from "./explorePivots/PivotFormattingSection"
import { normalizePivotOrdering } from "./explorePivots/placements"
import type { Column, LoadPivotFilterMembers } from "./explorePivots/placements"

export type { LoadPivotFilterMembers } from "./explorePivots/placements"

type ExplorePivotsConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeId: string
  upstreamColumns?: Column[]
  loadFilterMembers?: LoadPivotFilterMembers
  /**
   * Hash of the node's current Explore cache identity (graph + source), or
   * null when unknown. Displayed filter members are keyed to it so a
   * graph/source change hides them immediately, while display-only pivot
   * edits leave them untouched.
   */
  currentConfigHash?: string | null
}

type PivotEditorProps = {
  pivot: ExplorePivotConfig
  pivots: ExplorePivotConfig[]
  upstreamColumns: Column[]
  persistPivot: (pivot: ExplorePivotConfig) => void
  onBack: () => void
  loadFilterMembers?: LoadPivotFilterMembers
  currentConfigHash: string | null
}

function PivotEditor({
  pivot,
  pivots,
  upstreamColumns,
  persistPivot,
  onBack,
  loadFilterMembers,
  currentConfigHash,
}: PivotEditorProps) {
  const [nameDraft, setNameDraft] = useState(pivot.name)
  const [nameError, setNameError] = useState<string | null>(null)
  const columnsByName = useMemo(
    () => new Map(upstreamColumns.map((column) => [column.name, column])),
    [upstreamColumns],
  )
  const selectedSortBy = pivot.options.sort_by ?? ""
  const selectedSortRow = pivot.rows.find((row) => row.id === selectedSortBy)
  const selectedSortValue = pivot.values.find((value) => value.id === selectedSortBy)
  const selectedSortValueNumeric = selectedSortValue !== undefined && (
    selectedSortValue.aggregation === "count" ||
    selectedSortValue.aggregation === "distinct_count" ||
    isNumericPivotDtype(columnsByName.get(selectedSortValue.field)?.dtype ?? "")
  )
  const isNumericProducingValue = (value: PivotValuePlacement) =>
    value.aggregation === "count" ||
    value.aggregation === "distinct_count" ||
    isNumericPivotDtype(columnsByName.get(value.field)?.dtype ?? "")
  const valueDisplayLabel = (value: PivotValuePlacement) => {
    const sameNameCount = pivot.values.filter((candidate) => candidate.display_name === value.display_name).length
    return sameNameCount > 1
      ? `${value.display_name} (${PIVOT_AGGREGATION_LABELS[value.aggregation]})`
      : value.display_name
  }
  const conditionalFormattingRules = pivot.values.filter(
    (value) => value.color_scale !== "none",
  )
  const unformattedCompatibleValues = pivot.values.filter(
    (value) => value.color_scale === "none" && isNumericProducingValue(value),
  )
  const compatibleConditionalFormattingValues = pivot.values.filter(
    isNumericProducingValue,
  )

  const commitName = () => {
    const name = nameDraft.trim()
    if (!name) {
      setNameError("Pivot name cannot be blank.")
      return
    }
    const duplicate = pivots.some(
      (candidate) =>
        candidate.id !== pivot.id &&
        candidate.name.trim().toLowerCase() === name.toLowerCase(),
    )
    if (duplicate) {
      setNameError("Pivot name must be unique.")
      return
    }
    setNameError(null)
    if (name !== pivot.name) persistPivot({ ...pivot, name })
  }

  return (
    <div data-testid="explore-pivots-config" className="flex flex-col gap-4 px-4 py-3">
      <button
        type="button"
        onClick={onBack}
        className="focus-ring inline-flex self-start items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold hover:bg-[var(--bg-hover)]"
      >
        <ArrowLeft size={13} aria-hidden="true" />
        Back to pivots
      </button>

      <div>
        <label
          className="block text-[11px] font-semibold"
          style={{ color: "var(--text-secondary)" }}
        >
          Pivot name
          <input
            aria-label="Pivot name"
            value={nameDraft}
            onChange={(event) => setNameDraft(event.target.value)}
            onBlur={commitName}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault()
                commitName()
              }
            }}
            className="mt-1 block w-full rounded-md px-2 py-1.5 text-xs"
            style={INPUT_STYLE}
          />
        </label>
        {nameError && (
          <div role="alert" className="mt-1 text-xs" style={{ color: "var(--danger)" }}>
            {nameError}
          </div>
        )}
      </div>

      <PivotFieldWell
        pivot={pivot}
        persistPivot={persistPivot}
        upstreamColumns={upstreamColumns}
        loadFilterMembers={loadFilterMembers}
        currentConfigHash={currentConfigHash}
      />

      <section data-testid="pivot-sorting-section">
        <h4><EditorLabel as="span">Sorting</EditorLabel></h4>
        <div
          className="mt-1.5 rounded-lg border p-3"
          style={{ borderColor: "var(--border)", background: "var(--bg-input)" }}
        >
          <div data-testid="pivot-sorting-controls" className="grid grid-cols-2 gap-2">
            <label className="min-w-0 text-[10px]">
              Sort by
              <select
                aria-label="Sort by"
                value={selectedSortBy}
                onChange={(event) => persistPivot(normalizePivotOrdering(pivot, event.target.value || null))}
                className="mt-1 block w-full min-w-0 rounded px-1 py-0.5 text-[10px]"
                style={INPUT_STYLE}
              >
                <option value="">Default — Row labels</option>
                <optgroup label="Rows">
                  {pivot.rows.map((row) => <option key={row.id} value={row.id}>Row — {row.field}</option>)}
                </optgroup>
                <optgroup label="Values">
                  {pivot.values.map((value) => <option key={value.id} value={value.id}>Value — {valueDisplayLabel(value)}</option>)}
                </optgroup>
              </select>
            </label>
            <label className="min-w-0 text-[10px]">
              Order
              <select
                aria-label="Order"
                disabled={!selectedSortRow && !selectedSortValue}
                value={selectedSortRow?.sort ?? selectedSortValue?.sort_rows ?? "ascending"}
                onChange={(event) => {
                  const order = event.target.value as "ascending" | "descending"
                  if (selectedSortRow) {
                    persistPivot(normalizePivotOrdering({
                      ...pivot,
                      rows: pivot.rows.map((row) => row.id === selectedSortRow.id ? { ...row, sort: order } : row),
                    }, selectedSortRow.id))
                  } else if (selectedSortValue) {
                    persistPivot(normalizePivotOrdering({
                      ...pivot,
                      values: pivot.values.map((value) => value.id === selectedSortValue.id ? { ...value, sort_rows: order } : value),
                    }, selectedSortValue.id))
                  }
                }}
                className="mt-1 block w-full min-w-0 rounded px-1 py-0.5 text-[10px] disabled:opacity-50"
                style={INPUT_STYLE}
              >
                <option value="ascending">{selectedSortValueNumeric ? "Low → High" : "A → Z"}</option>
                <option value="descending">{selectedSortValueNumeric ? "High → Low" : "Z → A"}</option>
              </select>
            </label>
          </div>
        </div>
      </section>

      <PivotFormattingSection
        pivot={pivot}
        persistPivot={persistPivot}
        upstreamColumns={upstreamColumns}
      />

      <section data-testid="pivot-conditional-formatting-section">
        <h4><EditorLabel as="span">Conditional Formatting</EditorLabel></h4>
        <div
          className="mt-1.5 rounded-lg border p-3"
          style={{ borderColor: "var(--border)", background: "var(--bg-input)" }}
        >
          {conditionalFormattingRules.length === 0 ? (
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              No conditional formatting rules.
            </p>
          ) : (
            <div className="mt-2 flex flex-col gap-2">
              {conditionalFormattingRules.map((rule, index) => {
              const compatibleTargets = pivot.values.filter(
                (value) =>
                  value.id === rule.id ||
                  (value.color_scale === "none" && isNumericProducingValue(value)),
              )
              const previewLabel = rule.display_name || rule.field
              return (
                <div
                  key={rule.id}
                  role="group"
                  aria-label={`Conditional formatting rule for ${valueDisplayLabel(rule)}`}
                  className="flex flex-wrap items-end gap-2 rounded p-2"
                  style={{ border: "1px solid var(--border)" }}
                >
                  <label className="text-[10px]">
                    Value field
                    <select
                      aria-label={`Value field for conditional formatting rule ${index + 1}`}
                      value={rule.id}
                      onChange={(event) => {
                        const targetId = event.target.value
                        persistPivot({
                          ...pivot,
                          values: pivot.values.map((value) => {
                            if (value.id === rule.id) {
                              return {
                                ...value,
                                color_scale: "none",
                                color_scale_split_by: null,
                              }
                            }
                            if (value.id === targetId) {
                              return {
                                ...value,
                                color_scale: rule.color_scale,
                                color_scale_split_by: rule.color_scale_split_by ?? null,
                              }
                            }
                            return value
                          }),
                        })
                      }}
                      className="mt-1 block rounded px-1 py-0.5 text-[10px]"
                      style={INPUT_STYLE}
                    >
                      {compatibleTargets.map((value) => (
                        <option key={value.id} value={value.id}>
                          {valueDisplayLabel(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-[10px]">
                    Colour scale
                    <select
                      aria-label={`Colour scale for conditional formatting rule ${index + 1}`}
                      value={rule.color_scale}
                      onChange={(event) =>
                        persistPivot({
                          ...pivot,
                          values: pivot.values.map((value) =>
                            value.id === rule.id
                              ? {
                                  ...value,
                                  color_scale: event.target.value as PivotValuePlacement["color_scale"],
                                }
                              : value,
                          ),
                        })
                      }
                      className="mt-1 block rounded px-1 py-0.5 text-[10px]"
                      style={INPUT_STYLE}
                    >
                      <option value="low_red_high_green">Low red → High green</option>
                      <option value="low_green_high_red">Low green → High red</option>
                    </select>
                  </label>
                  <label className="text-[10px]">
                    Split scale by
                    <select
                      aria-label={`Split scale by for conditional formatting rule ${index + 1}`}
                      value={rule.color_scale_split_by ?? ""}
                      onChange={(event) =>
                        persistPivot({
                          ...pivot,
                          values: pivot.values.map((value) =>
                            value.id === rule.id
                              ? {
                                  ...value,
                                  color_scale_split_by: event.target.value || null,
                                }
                              : value,
                          ),
                        })
                      }
                      className="mt-1 block rounded px-1 py-0.5 text-[10px]"
                      style={INPUT_STYLE}
                    >
                      <option value="">None — entire Value</option>
                      {pivot.rows.length > 0 && (
                        <optgroup label="Rows">
                          {pivot.rows.map((row) => (
                            <option key={row.id} value={row.id}>Row — {row.field}</option>
                          ))}
                        </optgroup>
                      )}
                      {pivot.columns.length > 0 && (
                        <optgroup label="Columns">
                          {pivot.columns.map((column) => (
                            <option key={column.id} value={column.id}>Column — {column.field}</option>
                          ))}
                        </optgroup>
                      )}
                    </select>
                  </label>
                  <div
                    role="img"
                    aria-label={`Colour scale preview for ${previewLabel}`}
                    className="flex items-center gap-1 text-[10px]"
                  >
                    <span>Low</span>
                    <span
                      className="h-2 w-12 rounded"
                      style={{
                        background:
                          rule.color_scale === "low_red_high_green"
                            ? `linear-gradient(to right, ${PIVOT_CONDITIONAL_FORMAT_COLORS.low.hex}, ${PIVOT_CONDITIONAL_FORMAT_COLORS.midpoint.hex}, ${PIVOT_CONDITIONAL_FORMAT_COLORS.high.hex})`
                            : `linear-gradient(to right, ${PIVOT_CONDITIONAL_FORMAT_COLORS.high.hex}, ${PIVOT_CONDITIONAL_FORMAT_COLORS.midpoint.hex}, ${PIVOT_CONDITIONAL_FORMAT_COLORS.low.hex})`,
                      }}
                    />
                    <span>High</span>
                  </div>
                  <button
                    type="button"
                    aria-label={`Remove conditional formatting rule for ${valueDisplayLabel(rule)}`}
                    onClick={() =>
                      persistPivot({
                        ...pivot,
                        values: pivot.values.map((value) =>
                          value.id === rule.id
                            ? {
                                ...value,
                                color_scale: "none",
                                color_scale_split_by: null,
                              }
                            : value,
                        ),
                      })
                    }
                    className="ml-auto rounded px-1 text-[10px]"
                  >
                    Remove
                  </button>
                </div>
              )
              })}
            </div>
          )}
          {pivot.values.length === 0 && (
            <p className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
              Add a Value field to create a rule.
            </p>
          )}
          {pivot.values.length > 0 && compatibleConditionalFormattingValues.length === 0 && (
            <p className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
              Add a numeric-producing Value field to create a rule.
            </p>
          )}
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              aria-label="Add conditional formatting rule"
              disabled={unformattedCompatibleValues.length === 0}
              onClick={() => {
                const nextValue = unformattedCompatibleValues[0]
                if (!nextValue) return
                persistPivot({
                  ...pivot,
                  values: pivot.values.map((value) =>
                    value.id === nextValue.id
                      ? {
                          ...value,
                          color_scale: "low_red_high_green",
                          color_scale_split_by: null,
                        }
                      : value,
                  ),
                })
              }}
              className="focus-ring rounded px-2 py-1 text-[10px] font-semibold disabled:opacity-50"
              style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
            >
              Add rule
            </button>
          </div>
        </div>
      </section>

      <div className="flex flex-col gap-1">
        <label className="text-xs">
          <input
            type="checkbox"
            checked={pivot.options.row_grand_totals}
            onChange={(event) =>
              persistPivot({
                ...pivot,
                options: { ...pivot.options, row_grand_totals: event.target.checked },
              })
            }
          />{" "}
          Show row grand totals
        </label>
        <label className="text-xs">
          <input
            type="checkbox"
            checked={pivot.options.column_grand_totals}
            onChange={(event) =>
              persistPivot({
                ...pivot,
                options: { ...pivot.options, column_grand_totals: event.target.checked },
              })
            }
          />{" "}
          Show column grand totals
        </label>
      </div>

    </div>
  )
}

function PivotCardList({
  pivots,
  charts,
  onUpdate,
  onConfigure,
  onDelete,
}: {
  pivots: ExplorePivotConfig[]
  charts: ExploreChartConfig[]
  onUpdate: (pivot: ExplorePivotConfig) => void
  onConfigure: (pivotId: string) => void
  onDelete: (pivot: ExplorePivotConfig) => void
}) {
  return (
    <div data-testid="explore-pivots-config" className="flex flex-col gap-3 px-4 py-3">
      <ExploreConfigCardListHeader
        title="Pivots"
        description="Add pivot layouts and configure their fields."
        addLabel="Add Pivot"
        onAdd={() => onUpdate(createExplorePivot(pivots))}
      />

      {pivots.length === 0 ? (
        <ExploreConfigCardEmptyState>
          No pivots yet. Add one to start defining a pivot layout.
        </ExploreConfigCardEmptyState>
      ) : (
        <div className="flex flex-col gap-2">
          {pivots.map((pivot) => {
            const dependents = dependentChartsForPivot(charts, pivot.id)
            return (
              <ExploreConfigCard
                key={pivot.id}
                name={pivot.name}
                enabled={pivot.enabled}
                detail={
                  dependents.length > 0
                    ? `Used by ${dependents.map(({ name }) => name).join(", ")}`
                    : undefined
                }
                onEnabledChange={(enabled) => onUpdate({ ...pivot, enabled })}
                onConfigure={() => onConfigure(pivot.id)}
                onDelete={() => onDelete(pivot)}
                deleteDisabled={dependents.length > 0}
                deleteTitle={
                  dependents.length > 0
                    ? `Reassign or remove ${dependents.map(({ name }) => name).join(", ")} first.`
                    : `Delete ${pivot.name}`
                }
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function ExplorePivotsConfig({
  config,
  onUpdate,
  nodeId,
  upstreamColumns = [],
  loadFilterMembers,
  currentConfigHash = null,
}: ExplorePivotsConfigProps) {
  const configuredPivotId = useUIStore(
    (s) => s.exploreConfiguredPivotIds[nodeId] ?? null,
  )
  const setExploreConfiguredPivot = useUIStore((s) => s.setExploreConfiguredPivot)
  const parsed = parseExplorePivots(config)
  const configuredPivotExists =
    !parsed.ok ||
    configuredPivotId === null ||
    parsed.pivots.some(({ id }) => id === configuredPivotId)
  useEffect(() => {
    // A stored subview id whose card was deleted (from any surface) clears
    // itself so a later card reusing the id cannot reopen unexpectedly.
    if (!configuredPivotExists) setExploreConfiguredPivot(nodeId, null)
  }, [configuredPivotExists, nodeId, setExploreConfiguredPivot])
  const parsedCharts = parseExploreCharts(config)

  if (!parsed.ok) return <ConfigError error={parsed.error} />
  if (!parsedCharts.ok) return <ConfigError error={parsedCharts.error} />
  const { pivots } = parsed
  const configuredPivot = configuredPivotId
    ? pivots.find((candidate) => candidate.id === configuredPivotId)
    : undefined

  const persistPivot = (nextPivot: ExplorePivotConfig) => {
    const exists = pivots.some((candidate) => candidate.id === nextPivot.id)
    onUpdate(
      "pivots",
      exists
        ? pivots.map((candidate) =>
            candidate.id === nextPivot.id ? nextPivot : candidate,
          )
        : [...pivots, nextPivot],
    )
  }

  if (configuredPivot) {
    return (
      <PivotEditor
        key={configuredPivot.id}
        pivot={configuredPivot}
        pivots={pivots}
        upstreamColumns={upstreamColumns}
        persistPivot={persistPivot}
        onBack={() => setExploreConfiguredPivot(nodeId, null)}
        loadFilterMembers={loadFilterMembers}
        currentConfigHash={currentConfigHash}
      />
    )
  }

  return (
    <PivotCardList
      pivots={pivots}
      charts={parsedCharts.charts}
      onUpdate={persistPivot}
      onConfigure={(pivotId) => setExploreConfiguredPivot(nodeId, pivotId)}
      onDelete={(pivot) => {
        if (!window.confirm(`Delete ${pivot.name}?`)) return
        if (configuredPivotId === pivot.id) {
          setExploreConfiguredPivot(nodeId, null)
        }
        onUpdate(
          "pivots",
          pivots.filter((candidate) => candidate.id !== pivot.id),
        )
      }}
    />
  )
}

function ConfigError({ error }: { error: string }) {
  return (
    <div data-testid="explore-pivots-config" className="px-4 py-3">
      <div
        role="alert"
        className="rounded-lg px-3 py-2 text-xs leading-relaxed"
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
