import { useEffect, useMemo, useState } from "react"
import { ArrowLeft, GripVertical, Loader2 } from "lucide-react"

import type { ExplorePivotMembersResponse } from "../../api/types"
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
  pivotAggregationsForDtype,
  isNumericPivotDtype,
} from "../explore/pivotConfig"
import type {
  ExplorePivotConfig,
  PivotAggregation,
  PivotAxisPlacement,
  PivotFilterPlacement,
  PivotMember,
  PivotValuePlacement,
} from "../explore/pivotConfig"
import type { OnUpdateConfig } from "./_shared"
import {
  ExploreConfigCard,
  ExploreConfigCardEmptyState,
  ExploreConfigCardListHeader,
} from "./ExploreConfigCardList"

type Column = { name: string; dtype: string }
type Zone = "filters" | "columns" | "rows" | "values"
type Placement = PivotFilterPlacement | PivotAxisPlacement | PivotValuePlacement
type DraggedPlacement = { sourceZone: Zone; placementId: string }
type PlacementDropTarget = { zone: Zone; index: number }
type ZoneDropState = "idle" | "available" | "active" | "blocked"

export type LoadPivotFilterMembers = (
  field: string,
  search: string,
  signal: AbortSignal,
) => Promise<ExplorePivotMembersResponse>

type ExplorePivotsConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  upstreamColumns?: Column[]
  loadFilterMembers?: LoadPivotFilterMembers
}

const ZONES: readonly { key: Zone; label: string }[] = [
  { key: "filters", label: "Filters" },
  { key: "columns", label: "Columns" },
  { key: "rows", label: "Rows" },
  { key: "values", label: "Values" },
]

const PIVOT_PLACEMENT_MIME = "application/haute-pivot-placement"
const FILTER_MEMBER_SEARCH_DEBOUNCE_MS = 250

const INPUT_STYLE = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
}

function zonePlacements(pivot: ExplorePivotConfig, zone: Zone): Placement[] {
  return pivot[zone]
}

function zoneLabel(zone: Zone): string {
  return ZONES.find((candidate) => candidate.key === zone)?.label ?? zone
}

function futurePlacementFields(placement: Placement): Record<string, unknown> {
  const known = new Set(["id", "field", "members", "aggregation", "display_name", "sort", "sort_rows", "color_scale"])
  return Object.fromEntries(
    Object.entries(placement).filter(([key]) => !known.has(key)),
  )
}


function isFilterPlacement(placement: Placement): placement is PivotFilterPlacement {
  return Array.isArray(placement.members)
}

function isValuePlacement(placement: Placement): placement is PivotValuePlacement {
  return (
    typeof placement.aggregation === "string" &&
    typeof placement.display_name === "string"
  )
}

function removeFromZone(
  pivot: ExplorePivotConfig,
  zone: Zone,
  placementId: string,
): ExplorePivotConfig {
  switch (zone) {
    case "filters":
      return {
        ...pivot,
        filters: pivot.filters.filter((placement) => placement.id !== placementId),
      }
    case "columns":
      return {
        ...pivot,
        columns: pivot.columns.filter((placement) => placement.id !== placementId),
      }
    case "rows":
      return {
        ...pivot,
        rows: pivot.rows.filter((placement) => placement.id !== placementId),
      }
    case "values":
      return {
        ...pivot,
        values: pivot.values.filter((placement) => placement.id !== placementId),
      }
  }
}

function appendToZone(
  pivot: ExplorePivotConfig,
  zone: Zone,
  placement: Placement,
  dtype: string,
): ExplorePivotConfig {
  const common = {
    ...futurePlacementFields(placement),
    id: placement.id,
    field: placement.field,
  }
  switch (zone) {
    case "filters":
      return {
        ...pivot,
        filters: [
          ...pivot.filters,
          {
            ...common,
            members: isFilterPlacement(placement) ? placement.members : [],
          },
        ],
      }
    case "columns":
      return { ...pivot, columns: [...pivot.columns, common] }
    case "rows":
      return {
        ...pivot,
        rows: [
          ...pivot.rows,
          {
            ...common,
            sort: isValuePlacement(placement) && placement.sort_rows !== "none"
              ? placement.sort_rows
              : "ascending",
          },
        ],
      }
    case "values":
      return {
        ...pivot,
        values: [
          ...pivot.values,
          {
            ...common,
            aggregation:
              isValuePlacement(placement)
                ? placement.aggregation
                : defaultPivotAggregation(dtype),
            display_name:
              isValuePlacement(placement) ? placement.display_name : placement.field,
            sort_rows: isValuePlacement(placement)
              ? placement.sort_rows
              : (placement as PivotAxisPlacement).sort ?? "none",
            color_scale: isValuePlacement(placement) ? placement.color_scale : "none",
          },
        ],
      }
  }
}

function normalizePivotOrdering(
  pivot: ExplorePivotConfig,
  requestedSortBy: string | null = pivot.options.sort_by ?? null,
): ExplorePivotConfig {
  const selectedRow = pivot.rows.find((row) => row.id === requestedSortBy)
  const selectedValue = pivot.values.find((value) => value.id === requestedSortBy)
  if (!selectedRow && !selectedValue) {
    return {
      ...pivot,
      rows: pivot.rows.map((row) => ({ ...row, sort: "ascending" })),
      values: pivot.values.map((value) => ({ ...value, sort_rows: "none" })),
      options: { ...pivot.options, sort_by: null },
    }
  }
  if (selectedRow) {
    return {
      ...pivot,
      rows: pivot.rows.map((row) => ({
        ...row,
        sort: row.id === selectedRow.id ? row.sort ?? "ascending" : "ascending",
      })),
      values: pivot.values.map((value) => ({ ...value, sort_rows: "none" })),
      options: { ...pivot.options, sort_by: selectedRow.id },
    }
  }
  return {
    ...pivot,
    rows: pivot.rows.map((row) => ({ ...row, sort: "ascending" })),
    values: pivot.values.map((value) => ({
      ...value,
      sort_rows: value.id === selectedValue?.id
        ? value.sort_rows === "ascending" || value.sort_rows === "descending"
          ? value.sort_rows
          : "descending"
        : "none",
    })),
    options: { ...pivot.options, sort_by: selectedValue?.id ?? null },
  }
}

function hasDuplicateInZone(
  pivot: ExplorePivotConfig,
  zone: Zone,
  field: string,
): boolean {
  return zone !== "values" && zonePlacements(pivot, zone).some((item) => item.field === field)
}

function memberIdentity(member: Pick<PivotMember, "kind" | "value">): string {
  return JSON.stringify([member.kind, member.value])
}

function FilterMemberPicker({
  placement,
  loadMembers,
  onChange,
}: {
  placement: PivotFilterPlacement
  loadMembers?: LoadPivotFilterMembers
  onChange: (members: PivotMember[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const requestKey = `${placement.field}\u0000${search}`
  const [loadState, setLoadState] = useState<{
    requestKey: string | null
    response: ExplorePivotMembersResponse | null
    error: string | null
  }>({ requestKey: null, response: null, error: null })

  useEffect(() => {
    if (!open || !loadMembers) return

    const controller = new AbortController()
    const load = () => loadMembers(placement.field, search, controller.signal)
      .then((nextResponse) => {
        if (controller.signal.aborted) return
        if (nextResponse.field !== null && nextResponse.field !== placement.field) {
          throw new Error("Filter member response did not match the requested field.")
        }
        setLoadState({ requestKey, response: nextResponse, error: null })
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setLoadState({
          requestKey,
          response: null,
          error: reason instanceof Error ? reason.message : String(reason),
        })
      })
    const timer = search === ""
      ? null
      : window.setTimeout(load, FILTER_MEMBER_SEARCH_DEBOUNCE_MS)
    if (timer === null) load()

    return () => {
      if (timer !== null) window.clearTimeout(timer)
      controller.abort()
    }
  }, [loadMembers, open, placement.field, requestKey, search])

  const currentLoadState = loadState.requestKey === requestKey ? loadState : null
  const response = currentLoadState?.response ?? null
  const loading = open && !!loadMembers && currentLoadState === null
  const unavailableMessage = loadMembers
    ? null
    : "Filter members are unavailable until the Explore dataset is cached."
  const error = unavailableMessage ?? currentLoadState?.error ?? null

  const selected = useMemo(
    () => new Set(placement.members.map(memberIdentity)),
    [placement.members],
  )

  const toggleMember = (member: PivotMember) => {
    const identity = memberIdentity(member)
    if (selected.has(identity)) {
      onChange(placement.members.filter((candidate) => memberIdentity(candidate) !== identity))
    } else {
      onChange([...placement.members, { kind: member.kind, value: member.value }])
    }
  }

  const failure = response?.failure
  const summary =
    placement.members.length === 0
      ? "All members"
      : `${placement.members.length} selected`

  return (
    <div className="mt-2 w-full">
      <button
        type="button"
        aria-label={`Choose members for ${placement.field}`}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="focus-ring rounded px-2 py-1 text-[10px] font-semibold"
        style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
      >
        Choose members for {placement.field}: {summary}
      </button>

      {open && (
        <div
          className="mt-2 rounded-md p-2"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}
        >
          <label className="block text-[10px] font-semibold">
            Search members for {placement.field}
            <input
              type="search"
              aria-label={`Search members for ${placement.field}`}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="mt-1 block w-full rounded px-2 py-1 text-xs"
              style={INPUT_STYLE}
            />
          </label>

          {loading && (
            <div
              role="status"
              className="mt-2 flex items-center gap-1 text-[10px]"
              style={{ color: "var(--text-muted)" }}
            >
              <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              Loading members
            </div>
          )}

          {(error || failure) && (
            <div
              role="alert"
              className="mt-2 rounded px-2 py-1.5 text-[10px] leading-relaxed"
              style={{ color: "var(--danger)", background: "var(--danger-soft)" }}
            >
              {error ?? failure?.message}
              {failure?.remediation ? ` ${failure.remediation}` : ""}
            </div>
          )}

          {!loading && response?.status === "ok" && response.members.length === 0 && (
            <div className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
              No matching members.
            </div>
          )}

          {response?.status === "ok" && response.members.length > 0 && (
            <div className="mt-2 max-h-44 overflow-auto" role="group" aria-label="Filter members">
              {response.members.map((option) => {
                const member = option.key as PivotMember
                return (
                  <label
                    key={memberIdentity(member)}
                    className="flex items-center gap-2 rounded px-1.5 py-1 text-[11px]"
                  >
                    <input
                      type="checkbox"
                      aria-label={`${option.label} (${option.count})`}
                      checked={selected.has(memberIdentity(member))}
                      onChange={() => toggleMember(member)}
                    />
                    <span className="min-w-0 flex-1 truncate">{option.label}</span>
                    <span style={{ color: "var(--text-muted)" }}>{option.count}</span>
                  </label>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

type ZoneSectionProps = {
  pivot: ExplorePivotConfig
  zone: Zone
  columnsByName: ReadonlyMap<string, Column>
  persistPivot: (pivot: ExplorePivotConfig) => void
  draggedPlacement: DraggedPlacement | null
  activeDropTarget: PlacementDropTarget | null
  canPositionPlacement: (
    sourceZone: Zone,
    placementId: string,
    targetZone: Zone,
    targetIndex: number,
  ) => boolean
  onDragPlacementStart: (placement: DraggedPlacement) => void
  onDragPlacementOver: (target: PlacementDropTarget) => void
  onDropPlacement: (target: PlacementDropTarget) => void
  onDragPlacementEnd: () => void
  onPositionPlacement: (
    sourceZone: Zone,
    placementId: string,
    targetZone: Zone,
    targetIndex: number,
  ) => void
  loadFilterMembers?: LoadPivotFilterMembers
}

function ZoneSection({
  pivot,
  zone,
  columnsByName,
  persistPivot,
  draggedPlacement,
  activeDropTarget,
  canPositionPlacement,
  onDragPlacementStart,
  onDragPlacementOver,
  onDropPlacement,
  onDragPlacementEnd,
  onPositionPlacement,
  loadFilterMembers,
}: ZoneSectionProps) {
  const label = zoneLabel(zone)
  const placements = zonePlacements(pivot, zone)
  const endTarget = { zone, index: placements.length }
  const canDropAtEnd =
    draggedPlacement !== null &&
    canPositionPlacement(
      draggedPlacement.sourceZone,
      draggedPlacement.placementId,
      zone,
      endTarget.index,
    )
  const dropState: ZoneDropState = !draggedPlacement
    ? "idle"
    : activeDropTarget?.zone === zone
      ? "active"
      : canDropAtEnd
        ? "available"
        : "blocked"

  return (
    <section
      role="group"
      aria-label={`${label} fields`}
      data-drop-state={dropState}
      onDragOver={(event) => {
        if (!canDropAtEnd) return
        event.preventDefault()
        event.dataTransfer.dropEffect = "move"
        onDragPlacementOver(endTarget)
      }}
      onDrop={(event) => {
        if (!canDropAtEnd) {
          onDragPlacementEnd()
          return
        }
        event.preventDefault()
        onDropPlacement(endTarget)
      }}
      className="min-h-36 p-2 transition-colors"
      style={{
        background:
          dropState === "active" ? "var(--accent-soft)" : "var(--bg-input)",
        boxShadow:
          dropState === "active"
            ? "inset 0 0 0 1px var(--accent)"
            : undefined,
      }}
    >
      <h4
        className="text-[11px] font-bold uppercase tracking-wide"
        style={{ color: "var(--text-secondary)" }}
      >
        {label}
      </h4>
      <div className="mt-2 flex flex-col gap-1.5">
        {placements.length === 0 && (
          <div className="px-1 py-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
            No fields
          </div>
        )}
        {placements.map((placement, index) => {
          const column = columnsByName.get(placement.field)
          const missing = column === undefined
          const value = zone === "values" ? (placement as PivotValuePlacement) : undefined
          const filter = zone === "filters" ? (placement as PivotFilterPlacement) : undefined
          const aggregations = value
            ? [
                ...new Set([
                  ...pivotAggregationsForDtype(column?.dtype ?? ""),
                  value.aggregation,
                ]),
              ]
            : []
          const canDropBefore =
            draggedPlacement !== null &&
            canPositionPlacement(
              draggedPlacement.sourceZone,
              draggedPlacement.placementId,
              zone,
              index,
            )
          const isDropBeforeActive =
            activeDropTarget?.zone === zone && activeDropTarget.index === index

          return (
            <div
              key={placement.id}
              role="group"
              aria-label={`${placement.field} in ${label}`}
              aria-invalid={missing || undefined}
              aria-describedby="pivot-field-keyboard-instructions"
              aria-grabbed={
                draggedPlacement?.sourceZone === zone &&
                draggedPlacement.placementId === placement.id
              }
              data-drop-position={isDropBeforeActive ? "before" : undefined}
              tabIndex={0}
              draggable
              onDragStart={(event) => {
                const dragged = { sourceZone: zone, placementId: placement.id }
                event.dataTransfer.effectAllowed = "move"
                event.dataTransfer.setData(
                  PIVOT_PLACEMENT_MIME,
                  JSON.stringify(dragged),
                )
                onDragPlacementStart(dragged)
              }}
              onDragEnd={onDragPlacementEnd}
              onDragOver={(event) => {
                event.stopPropagation()
                if (!canDropBefore) return
                event.preventDefault()
                event.dataTransfer.dropEffect = "move"
                onDragPlacementOver({ zone, index })
              }}
              onDrop={(event) => {
                event.stopPropagation()
                if (!canDropBefore) {
                  onDragPlacementEnd()
                  return
                }
                event.preventDefault()
                onDropPlacement({ zone, index })
              }}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget) return

                let targetZone = zone
                let targetIndex: number | null = null
                if (event.key === "ArrowUp") {
                  targetIndex = index - 1
                } else if (event.key === "ArrowDown") {
                  targetIndex = index + 2
                } else if (event.key === "ArrowLeft") {
                  const zoneIndex = ZONES.findIndex((candidate) => candidate.key === zone)
                  const target = ZONES[zoneIndex - 1]
                  if (target) {
                    targetZone = target.key
                    targetIndex = zonePlacements(pivot, targetZone).length
                  }
                } else if (event.key === "ArrowRight") {
                  const zoneIndex = ZONES.findIndex((candidate) => candidate.key === zone)
                  const target = ZONES[zoneIndex + 1]
                  if (target) {
                    targetZone = target.key
                    targetIndex = zonePlacements(pivot, targetZone).length
                  }
                }

                if (
                  targetIndex !== null &&
                  canPositionPlacement(
                    zone,
                    placement.id,
                    targetZone,
                    targetIndex,
                  )
                ) {
                  event.preventDefault()
                  onPositionPlacement(
                    zone,
                    placement.id,
                    targetZone,
                    targetIndex,
                  )
                }
              }}
              className="focus-ring cursor-grab rounded p-1.5 active:cursor-grabbing"
              style={{
                background: "var(--bg-panel)",
                border: "1px solid var(--border)",
                boxShadow: isDropBeforeActive
                  ? "inset 0 2px 0 var(--accent)"
                  : undefined,
                color: "var(--text-primary)",
                opacity:
                  draggedPlacement?.sourceZone === zone &&
                  draggedPlacement.placementId === placement.id
                    ? 0.55
                    : 1,
              }}
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <GripVertical
                  size={12}
                  aria-hidden="true"
                  style={{ color: "var(--text-muted)" }}
                />
                <span className="text-xs font-medium">{placement.field}</span>
                {missing && (
                  <span className="text-[10px]" style={{ color: "var(--danger)" }}>
                    No longer available
                  </span>
                )}
                {value && (
                  <select
                    aria-label={`Aggregation for ${placement.field}`}
                    value={value.aggregation}
                    onChange={(event) => {
                      const aggregation = event.target.value as PivotAggregation
                      persistPivot({
                        ...pivot,
                        values: pivot.values.map((candidate) =>
                          candidate.id === value.id
                            ? {
                                ...candidate,
                                aggregation,
                                color_scale:
                                  aggregation === "count" ||
                                  aggregation === "distinct_count" ||
                                  (column !== undefined && isNumericPivotDtype(column.dtype))
                                    ? candidate.color_scale
                                    : "none",
                              }
                            : candidate,
                        ),
                      })
                    }}
                    className="rounded px-1 py-0.5 text-[10px]"
                    style={INPUT_STYLE}
                  >
                    {aggregations.map((aggregation) => (
                      <option key={aggregation} value={aggregation}>
                        {PIVOT_AGGREGATION_LABELS[aggregation]}
                      </option>
                    ))}
                  </select>
                )}

                <button
                  type="button"
                  aria-label={`Remove ${placement.field} from ${label}`}
                  onClick={() => persistPivot(normalizePivotOrdering(removeFromZone(pivot, zone, placement.id)))}
                  className="ml-auto rounded px-1 text-[10px]"
                >
                  Remove
                </button>
              </div>

              {filter && (
                <FilterMemberPicker
                  placement={filter}
                  loadMembers={loadFilterMembers}
                  onChange={(members) =>
                    persistPivot({
                      ...pivot,
                      filters: pivot.filters.map((candidate) =>
                        candidate.id === filter.id ? { ...candidate, members } : candidate,
                      ),
                    })
                  }
                />
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

type PivotEditorProps = {
  pivot: ExplorePivotConfig
  pivots: ExplorePivotConfig[]
  upstreamColumns: Column[]
  persistPivot: (pivot: ExplorePivotConfig) => void
  onBack: () => void
  loadFilterMembers?: LoadPivotFilterMembers
}

function PivotEditor({
  pivot,
  pivots,
  upstreamColumns,
  persistPivot,
  onBack,
  loadFilterMembers,
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
