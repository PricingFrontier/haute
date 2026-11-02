import {
  PIVOT_AGGREGATION_LABELS,
  isNumericPivotDtype,
  nextPivotValueReference,
  pivotOutputs,
  pivotAggregationsForDtype,
} from "../../explore/pivotConfig"
import type {
  ExplorePivotConfig,
  PivotAggregation,
  PivotFilterPlacement,
  PivotFormulaPlacement,
  PivotOutputPlacement,
  PivotValuePlacement,
} from "../../explore/pivotConfig"
import { INPUT_STYLE } from "../_shared"
import FilterMemberPicker from "./FilterMemberPicker"
import PlacementCard from "./PlacementCard"
import type { PlacementDrag } from "./PlacementCard"
import {
  normalizePivotOrdering,
  removeFromZone,
  zoneLabel,
  zonePlacements,
} from "./placements"
import type {
  Column,
  LoadPivotFilterMembers,
  Placement,
  Zone,
  ZoneDropState,
} from "./placements"

type ZoneSectionProps = PlacementDrag & {
  pivot: ExplorePivotConfig
  zone: Zone
  columnsByName: ReadonlyMap<string, Column>
  persistPivot: (pivot: ExplorePivotConfig) => void
  loadFilterMembers?: LoadPivotFilterMembers
  currentConfigHash: string | null
}

function isFormulaPlacement(
  placement: Placement | PivotOutputPlacement,
): placement is PivotFormulaPlacement {
  return "expression" in placement
}

export default function ZoneSection({
  pivot,
  zone,
  columnsByName,
  persistPivot,
  loadFilterMembers,
  currentConfigHash,
  ...drag
}: ZoneSectionProps) {
  const {
    draggedPlacement,
    activeDropTarget,
    canPositionPlacement,
    onDragPlacementOver,
    onDropPlacement,
    onDragPlacementEnd,
  } = drag
  const label = zoneLabel(zone)
  const placements = zone === "values" ? pivotOutputs(pivot) : zonePlacements(pivot, zone)
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
          if (zone === "values" && isFormulaPlacement(placement)) {
            return (
              <PlacementCard
                key={placement.id}
                {...drag}
                pivot={pivot}
                zone={zone}
                index={index}
                placementId={placement.id}
                kind="formula"
                name={placement.display_name}
                onRemove={() => persistPivot({
                  ...pivot,
                  formulas: pivot.formulas.filter(
                    (candidate) => candidate.id !== placement.id,
                  ),
                  value_order: pivot.value_order.filter(
                    (id) => id !== placement.id,
                  ),
                })}
              >
                <span
                  className="rounded px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide"
                  style={{
                    color: "var(--text-muted)",
                    background: "var(--bg-input)",
                  }}
                >
                  Formula
                </span>
                <span
                  className="rounded px-1 py-0.5 font-mono text-[9px]"
                  style={{
                    color: "var(--text-muted)",
                    background: "var(--bg-input)",
                  }}
                >
                  {placement.reference}
                </span>
              </PlacementCard>
            )
          }
          const column = columnsByName.get(placement.field as string)
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

          return (
            <PlacementCard
              key={placement.id}
              {...drag}
              pivot={pivot}
              zone={zone}
              index={index}
              placementId={placement.id}
              kind="field"
              name={placement.field as string}
              invalid={missing}
              onRemove={() => persistPivot(normalizePivotOrdering(removeFromZone(pivot, zone, placement.id)))}
              below={filter && (
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
            >
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
                    const reference = nextPivotValueReference(
                      pivot,
                      value.field,
                      aggregation,
                      value.id,
                    )
                    persistPivot({
                      ...pivot,
                      values: pivot.values.map((candidate) =>
                        candidate.id === value.id
                          ? {
                              ...candidate,
                              aggregation,
                              reference,
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
            </PlacementCard>
          )
        })}
      </div>
    </section>
  )
}
