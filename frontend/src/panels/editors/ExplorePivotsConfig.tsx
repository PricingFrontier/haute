import { useMemo, useState } from "react"
import { ArrowLeft } from "lucide-react"

import { PIVOT_CONDITIONAL_FORMAT_COLORS } from "../../theme/colors"
import {
  dependentChartsForPivot,
  parseExploreCharts,
  type ExploreChartConfig,
} from "../explore/chartConfig"
import {
  PIVOT_AGGREGATION_LABELS,
  createExplorePivot,
  defaultPivotAggregation,
  nextPivotPlacementId,
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
import ZoneSection from "./explorePivots/ZoneSection"
import {
  ZONES,
  appendToZone,
  hasDuplicateInZone,
  normalizePivotOrdering,
  removeFromZone,
  zonePlacements,
} from "./explorePivots/placements"
import type {
  Column,
  DraggedPlacement,
  LoadPivotFilterMembers,
  Placement,
  PlacementDropTarget,
  Zone,
} from "./explorePivots/placements"

export type { LoadPivotFilterMembers } from "./explorePivots/placements"

type ExplorePivotsConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
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
  const [fieldSearch, setFieldSearch] = useState("")
  const [nameDraft, setNameDraft] = useState(pivot.name)
  const [nameError, setNameError] = useState<string | null>(null)
  const [draggedPlacement, setDraggedPlacement] =
    useState<DraggedPlacement | null>(null)
  const [activeDropTarget, setActiveDropTarget] =
    useState<PlacementDropTarget | null>(null)
  const columnsByName = useMemo(
    () => new Map(upstreamColumns.map((column) => [column.name, column])),
    [upstreamColumns],
  )
  const availableColumns = useMemo(() => {
    const query = fieldSearch.trim().toLocaleLowerCase()
    return upstreamColumns.filter((column) => column.name.toLocaleLowerCase().includes(query))
  }, [fieldSearch, upstreamColumns])
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

  const addPlacement = (column: Column, zone: Zone) => {
    if (hasDuplicateInZone(pivot, zone, column.name)) return
    const prefix = zone.slice(0, -1)
    const id = nextPivotPlacementId(pivot, prefix)
    const placement: Placement =
      zone === "filters"
        ? { id, field: column.name, members: [] }
        : zone === "values"
          ? {
              id,
              field: column.name,
              aggregation: defaultPivotAggregation(column.dtype),
              display_name: column.name,
              sort_rows: "none",
              color_scale: "none",
            }
          : { id, field: column.name, sort: "ascending" }

    persistPivot(normalizePivotOrdering(appendToZone(pivot, zone, placement, column.dtype)))
  }

  const canPositionPlacement = (
    sourceZone: Zone,
    placementId: string,
    targetZone: Zone,
    targetIndex: number,
  ) => {
    const sourcePlacements = zonePlacements(pivot, sourceZone)
    const sourceIndex = sourcePlacements.findIndex(
      (candidate) => candidate.id === placementId,
    )
    if (sourceIndex < 0) return false

    const placement = sourcePlacements[sourceIndex]
    if (
      sourceZone !== targetZone &&
      hasDuplicateInZone(pivot, targetZone, placement.field)
    ) {
      return false
    }

    const targetLength = zonePlacements(pivot, targetZone).length
    const clampedIndex = Math.max(0, Math.min(targetIndex, targetLength))
    const insertionIndex =
      sourceZone === targetZone && sourceIndex < clampedIndex
        ? clampedIndex - 1
        : clampedIndex
    return sourceZone !== targetZone || insertionIndex !== sourceIndex
  }

  const positionPlacement = (
    sourceZone: Zone,
    placementId: string,
    targetZone: Zone,
    targetIndex: number,
  ) => {
    if (
      !canPositionPlacement(
        sourceZone,
        placementId,
        targetZone,
        targetIndex,
      )
    ) {
      return
    }

    const sourcePlacements = zonePlacements(pivot, sourceZone)
    const sourceIndex = sourcePlacements.findIndex(
      (candidate) => candidate.id === placementId,
    )
    const placement = sourcePlacements[sourceIndex]
    if (!placement) {
      throw new Error(`Pivot placement ${placementId} disappeared during movement.`)
    }

    const targetLength = zonePlacements(pivot, targetZone).length
    const clampedIndex = Math.max(0, Math.min(targetIndex, targetLength))
    const insertionIndex =
      sourceZone === targetZone && sourceIndex < clampedIndex
        ? clampedIndex - 1
        : clampedIndex

    if (sourceZone === targetZone) {
      const nextPlacements = sourcePlacements.filter(
        (candidate) => candidate.id !== placementId,
      )
      nextPlacements.splice(insertionIndex, 0, placement)
      persistPivot(normalizePivotOrdering({ ...pivot, [sourceZone]: nextPlacements }))
      return
    }

    const withoutSource = removeFromZone(pivot, sourceZone, placementId)
    const dtype = columnsByName.get(placement.field)?.dtype ?? ""
    const appended = appendToZone(withoutSource, targetZone, placement, dtype)
    const nextTargetPlacements = [...zonePlacements(appended, targetZone)]
    const convertedPlacement = nextTargetPlacements.pop()
    if (!convertedPlacement) {
      throw new Error(`Pivot placement ${placementId} was not appended to ${targetZone}.`)
    }
    nextTargetPlacements.splice(insertionIndex, 0, convertedPlacement)
    persistPivot(normalizePivotOrdering({ ...appended, [targetZone]: nextTargetPlacements }, pivot.options.sort_by ?? null))
  }

  const clearDragPlacement = () => {
    setDraggedPlacement(null)
    setActiveDropTarget(null)
  }

  const dropPlacement = (target: PlacementDropTarget) => {
    if (
      draggedPlacement &&
      canPositionPlacement(
        draggedPlacement.sourceZone,
        draggedPlacement.placementId,
        target.zone,
        target.index,
      )
    ) {
      positionPlacement(
        draggedPlacement.sourceZone,
        draggedPlacement.placementId,
        target.zone,
        target.index,
      )
    }
    clearDragPlacement()
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
        <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Configure {pivot.name}
        </h3>
        <label
          className="mt-3 block text-[11px] font-semibold"
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

      <div>
        <label className="text-[11px] font-semibold" style={{ color: "var(--text-secondary)" }}>
          Search pivot fields
          <input
            type="search"
            role="searchbox"
            aria-label="Search pivot fields"
            value={fieldSearch}
            onChange={(event) => setFieldSearch(event.target.value)}
            className="mt-1 block w-full rounded-md px-2 py-1.5 text-xs"
            style={INPUT_STYLE}
          />
        </label>
        <div
          role="group"
          aria-label="Available pivot fields"
          className="mt-2 h-52 overflow-y-auto rounded-md"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
          }}
        >
          {availableColumns.length === 0 ? (
            <div
              className="px-2 py-3 text-center text-[10px]"
              style={{ color: "var(--text-muted)" }}
            >
              No fields match your search.
            </div>
          ) : (
            availableColumns.map((column) => (
              <div
                key={column.name}
                role="group"
                aria-label={`${column.name} field actions`}
                className="flex min-h-8 flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1 text-[11px]"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <span className="min-w-[7rem] flex-1 truncate font-medium">
                  {column.name}
                </span>
                <span
                  className="shrink-0 text-[10px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  {column.dtype}
                </span>
                <div className="flex shrink-0 flex-wrap items-center gap-1">
                  <span
                    className="text-[10px]"
                    style={{ color: "var(--text-muted)" }}
                  >
                    Add to:
                  </span>
                  {ZONES.map(({ key, label }) => (
                    <button
                      key={key}
                      type="button"
                      aria-label={`Add ${column.name} to ${label}`}
                      disabled={hasDuplicateInZone(pivot, key, column.name)}
                      onClick={() => addPlacement(column, key)}
                      className="focus-ring rounded px-1.5 py-0.5 text-[10px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        border: "1px solid var(--border)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div>
        <div
          id="pivot-field-area-instructions"
          className="text-[11px] font-semibold"
          style={{ color: "var(--text-secondary)" }}
        >
          Drag fields between areas below:
          <span id="pivot-field-keyboard-instructions" className="sr-only">
            Focus a field card and use Up or Down to reorder it, or Left or Right
            to move it between areas.
          </span>
        </div>
        <div
          data-testid="pivot-field-areas"
          aria-describedby="pivot-field-area-instructions"
          className="mt-2 grid grid-cols-2 gap-px overflow-hidden rounded-lg"
          style={{
            background: "var(--border)",
            border: "1px solid var(--border)",
          }}
        >
          {ZONES.map(({ key }) => (
            <ZoneSection
              key={key}
              pivot={pivot}
              zone={key}
              columnsByName={columnsByName}
              persistPivot={persistPivot}
              draggedPlacement={draggedPlacement}
              activeDropTarget={activeDropTarget}
              canPositionPlacement={canPositionPlacement}
              onDragPlacementStart={(placement) => {
                setDraggedPlacement(placement)
                setActiveDropTarget(null)
              }}
              onDragPlacementOver={setActiveDropTarget}
              onDropPlacement={dropPlacement}
              onDragPlacementEnd={clearDragPlacement}
              onPositionPlacement={positionPlacement}
              loadFilterMembers={loadFilterMembers}
              currentConfigHash={currentConfigHash}
            />
          ))}
        </div>
      </div>

      <section data-testid="pivot-sorting-section" className="flex flex-col gap-2">
        <h4 className="text-[11px] font-bold uppercase tracking-wide" style={{ color: "var(--text-secondary)" }}>
          Sorting
        </h4>
        <label className="text-[10px]">
          Sort by
          <select
            aria-label="Sort by"
            value={selectedSortBy}
            onChange={(event) => persistPivot(normalizePivotOrdering(pivot, event.target.value || null))}
            className="mt-1 block rounded px-1 py-0.5 text-[10px]"
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
        <label className="text-[10px]">
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
            className="mt-1 block rounded px-1 py-0.5 text-[10px] disabled:opacity-50"
            style={INPUT_STYLE}
          >
            <option value="ascending">{selectedSortValueNumeric ? "Low → High" : "A → Z"}</option>
            <option value="descending">{selectedSortValueNumeric ? "High → Low" : "Z → A"}</option>
          </select>
        </label>
      </section>

      <section
        data-testid="pivot-conditional-formatting-section"
        className="rounded-md border p-3"
        style={{ borderColor: "var(--border)", background: "var(--bg-input)" }}
      >
        <div className="flex items-center justify-between gap-3">
          <h4
            className="text-[11px] font-bold uppercase tracking-wide"
            style={{ color: "var(--text-secondary)" }}
          >
            Conditional formatting
          </h4>
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
                    ? { ...value, color_scale: "low_red_high_green" }
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

        {conditionalFormattingRules.length === 0 ? (
          <p className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
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
                            if (value.id === rule.id) return { ...value, color_scale: "none" }
                            if (value.id === targetId) {
                              return { ...value, color_scale: rule.color_scale }
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
                            ? { ...value, color_scale: "none" }
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
        {compatibleConditionalFormattingValues.length > 0 && unformattedCompatibleValues.length === 0 && (
          <p className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
            All compatible Value fields already have rules.
          </p>
        )}
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
  upstreamColumns = [],
  loadFilterMembers,
  currentConfigHash = null,
}: ExplorePivotsConfigProps) {
  const [configuredPivotId, setConfiguredPivotId] = useState<string | null>(null)
  const parsed = parseExplorePivots(config)
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
        onBack={() => setConfiguredPivotId(null)}
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
      onConfigure={setConfiguredPivotId}
      onDelete={(pivot) => {
        if (!window.confirm(`Delete ${pivot.name}?`)) return
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
