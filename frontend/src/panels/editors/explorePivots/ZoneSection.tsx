import { GripVertical } from "lucide-react"

import {
  PIVOT_AGGREGATION_LABELS,
  isNumericPivotDtype,
  pivotAggregationsForDtype,
} from "../../explore/pivotConfig"
import type {
  ExplorePivotConfig,
  PivotAggregation,
  PivotFilterPlacement,
  PivotValuePlacement,
} from "../../explore/pivotConfig"
import { INPUT_STYLE } from "../_shared"
import FilterMemberPicker from "./FilterMemberPicker"
import {
  PIVOT_PLACEMENT_MIME,
  ZONES,
  normalizePivotOrdering,
  removeFromZone,
  zoneLabel,
  zonePlacements,
} from "./placements"
import type {
  Column,
  DraggedPlacement,
  LoadPivotFilterMembers,
  PlacementDropTarget,
  Zone,
  ZoneDropState,
} from "./placements"

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
  currentConfigHash: string | null
}

export default function ZoneSection({
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
  currentConfigHash,
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
                                color_scale_split_by:
                                  aggregation === "count" ||
                                  aggregation === "distinct_count" ||
                                  (column !== undefined && isNumericPivotDtype(column.dtype))
                                    ? candidate.color_scale_split_by ?? null
                                    : null,
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
                  currentConfigHash={currentConfigHash}
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
