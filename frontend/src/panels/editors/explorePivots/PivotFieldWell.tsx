/**
 * Pivot field-authoring surface composed by the Pivots editor: field search,
 * dtype-labelled available-fields list with per-zone Add actions, the
 * four-zone grid, and pointer/keyboard placement state. Field authoring is
 * Pivots-editor-only — chart Configure is a formatting surface and does not
 * embed this component.
 */

import { useMemo, useState } from "react"

import {
  defaultPivotAggregation,
  nextPivotPlacementId,
} from "../../explore/pivotConfig"
import type { ExplorePivotConfig } from "../../explore/pivotConfig"
import { INPUT_STYLE } from "../_shared"
import ZoneSection from "./ZoneSection"
import {
  ZONES,
  appendToZone,
  hasDuplicateInZone,
  normalizePivotOrdering,
  removeFromZone,
  zonePlacements,
} from "./placements"
import type {
  Column,
  DraggedPlacement,
  LoadPivotFilterMembers,
  Placement,
  PlacementDropTarget,
  Zone,
} from "./placements"

type PivotFieldWellProps = {
  pivot: ExplorePivotConfig
  persistPivot: (pivot: ExplorePivotConfig) => void
  upstreamColumns: Column[]
  loadFilterMembers?: LoadPivotFilterMembers
  currentConfigHash: string | null
}

export default function PivotFieldWell({
  pivot,
  persistPivot,
  upstreamColumns,
  loadFilterMembers,
  currentConfigHash,
}: PivotFieldWellProps) {
  const [fieldSearch, setFieldSearch] = useState("")
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
    <>
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
    </>
  )
}
