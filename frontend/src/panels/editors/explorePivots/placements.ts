import type { ExplorePivotMembersResponse } from "../../../api/types"
import { defaultPivotAggregation } from "../../explore/pivotConfig"
import type {
  ExplorePivotConfig,
  PivotAxisPlacement,
  PivotFilterPlacement,
  PivotMember,
  PivotValuePlacement,
} from "../../explore/pivotConfig"

export type Column = { name: string; dtype: string }
export type Zone = "filters" | "columns" | "rows" | "values"
export type Placement = PivotFilterPlacement | PivotAxisPlacement | PivotValuePlacement
export type DraggedPlacement = { sourceZone: Zone; placementId: string }
export type PlacementDropTarget = { zone: Zone; index: number }
export type ZoneDropState = "idle" | "available" | "active" | "blocked"

export type LoadPivotFilterMembers = (
  field: string,
  search: string,
  signal: AbortSignal,
) => Promise<ExplorePivotMembersResponse>

export const ZONES: readonly { key: Zone; label: string }[] = [
  { key: "filters", label: "Filters" },
  { key: "columns", label: "Columns" },
  { key: "rows", label: "Rows" },
  { key: "values", label: "Values" },
]

export const PIVOT_PLACEMENT_MIME = "application/haute-pivot-placement"

export function zonePlacements(pivot: ExplorePivotConfig, zone: Zone): Placement[] {
  return pivot[zone]
}

export function zoneLabel(zone: Zone): string {
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

export function removeFromZone(
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

export function appendToZone(
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

export function normalizePivotOrdering(
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

export function hasDuplicateInZone(
  pivot: ExplorePivotConfig,
  zone: Zone,
  field: string,
): boolean {
  return zone !== "values" && zonePlacements(pivot, zone).some((item) => item.field === field)
}

export function memberIdentity(member: Pick<PivotMember, "kind" | "value">): string {
  return JSON.stringify([member.kind, member.value])
}
