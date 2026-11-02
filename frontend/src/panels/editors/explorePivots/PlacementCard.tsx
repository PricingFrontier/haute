import { GripVertical } from "lucide-react"
import type { ReactNode } from "react"

import { pivotOutputs } from "../../explore/pivotConfig"
import type { ExplorePivotConfig } from "../../explore/pivotConfig"
import { PIVOT_PLACEMENT_MIME, ZONES, zoneLabel, zonePlacements } from "./placements"
import type { DraggedPlacement, PlacementDropTarget, Zone } from "./placements"

/** The pivot grid's drag state and moves, which each zone and each of its cards share. */
export type PlacementDrag = {
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
}

type PlacementKind = "field" | "formula"

type PlacementCardProps = PlacementDrag & {
  pivot: ExplorePivotConfig
  zone: Zone
  index: number
  placementId: string
  /** A formula moves only within Values; a field moves to the zone beside its own too. */
  kind: PlacementKind
  /** The field or formula shown after the grip, which names the card and its Remove. */
  name: string
  /** The field is no longer among the upstream columns. */
  invalid?: boolean
  onRemove: () => void
  /** What the card's row shows between the name and Remove. */
  children?: ReactNode
  /** What the card shows under its row. */
  below?: ReactNode
}

const KEYBOARD_INSTRUCTIONS: Record<PlacementKind, string> = {
  field: "pivot-field-keyboard-instructions",
  formula: "pivot-formula-keyboard-instructions",
}

/**
 * Where a key moves the placement at `index` of `zone`: ArrowUp and ArrowDown within the
 * zone, and for a field ArrowLeft and ArrowRight to the end of the zone beside it.
 */
function keyboardTarget(
  pivot: ExplorePivotConfig,
  zone: Zone,
  index: number,
  kind: PlacementKind,
  key: string,
): PlacementDropTarget | null {
  if (key === "ArrowUp") return { zone, index: index - 1 }
  if (key === "ArrowDown") return { zone, index: index + 2 }
  if (kind === "formula" || (key !== "ArrowLeft" && key !== "ArrowRight")) return null
  const zoneIndex = ZONES.findIndex((candidate) => candidate.key === zone)
  const beside = ZONES[zoneIndex + (key === "ArrowLeft" ? -1 : 1)]
  if (!beside) return null
  return {
    zone: beside.key,
    index: beside.key === "values"
      ? pivotOutputs(pivot).length
      : zonePlacements(pivot, beside.key).length,
  }
}

/**
 * One placement's card in a pivot zone, the formula's and the field's alike: dragged by
 * itself, moved by the keyboard, and the drop target that places another before it.
 */
export default function PlacementCard({
  pivot,
  zone,
  index,
  placementId,
  kind,
  name,
  invalid = false,
  onRemove,
  children,
  below,
  draggedPlacement,
  activeDropTarget,
  canPositionPlacement,
  onDragPlacementStart,
  onDragPlacementOver,
  onDropPlacement,
  onDragPlacementEnd,
  onPositionPlacement,
}: PlacementCardProps) {
  const label = zoneLabel(zone)
  const dragging =
    draggedPlacement?.sourceZone === zone &&
    draggedPlacement.placementId === placementId
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
      role="group"
      aria-label={`${name} in ${label}`}
      aria-invalid={invalid || undefined}
      aria-describedby={KEYBOARD_INSTRUCTIONS[kind]}
      aria-grabbed={dragging}
      data-drop-position={isDropBeforeActive ? "before" : undefined}
      tabIndex={0}
      draggable
      onDragStart={(event) => {
        const dragged = { sourceZone: zone, placementId }
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
        const target = keyboardTarget(pivot, zone, index, kind, event.key)
        if (
          target !== null &&
          canPositionPlacement(zone, placementId, target.zone, target.index)
        ) {
          event.preventDefault()
          onPositionPlacement(zone, placementId, target.zone, target.index)
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
        opacity: dragging ? 0.55 : 1,
      }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <GripVertical
          size={12}
          aria-hidden="true"
          style={{ color: "var(--text-muted)" }}
        />
        <span className="text-xs font-medium">{name}</span>
        {children}
        <button
          type="button"
          aria-label={`Remove ${name} from ${label}`}
          onClick={onRemove}
          className="ml-auto rounded px-1 text-[10px]"
        >
          Remove
        </button>
      </div>
      {below}
    </div>
  )
}
