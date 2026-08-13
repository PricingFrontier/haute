import { useMemo, useState } from "react"

import type { ExplorePivotMemberKey, ExplorePivotResult } from "../../api/types"
import { PIVOT_CONDITIONAL_FORMAT_COLORS } from "../../theme/colors"
import type { ExplorePivotConfig } from "./pivotConfig"

type PivotTableGridProps = {
  result: ExplorePivotResult
  pivot: ExplorePivotConfig
}

const ROW_HEIGHT = 32
const VIEWPORT_HEIGHT = 320
const OVERSCAN = 5
const ROW_HEADER_WIDTH = 140
const STRICT_DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?$/

type Color = readonly [number, number, number]
const PALE_RED: Color = PIVOT_CONDITIONAL_FORMAT_COLORS.low.rgb
const YELLOW: Color = PIVOT_CONDITIONAL_FORMAT_COLORS.midpoint.rgb
const GREEN: Color = PIVOT_CONDITIONAL_FORMAT_COLORS.high.rgb

function numericCellValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null
  if (typeof value === "string" && STRICT_DECIMAL_PATTERN.test(value)) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function median(sorted: readonly number[]): number {
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle]
}

function colorCss(channels: readonly number[]): string {
  return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`
}

function interpolate(left: Color, right: Color, ratio: number): string {
  const channels = left.map((channel, index) => Math.round(channel + (right[index] - channel) * ratio))
  return colorCss(channels)
}

function conditionalColor(value: number, domain: readonly [number, number, number], scale: "low_red_high_green" | "low_green_high_red"): string {
  const [minimum, midpoint, maximum] = domain
  if (minimum === maximum) return colorCss(YELLOW)
  const [low, high] = scale === "low_red_high_green" ? [PALE_RED, GREEN] : [GREEN, PALE_RED]
  if (value <= midpoint) {
    const ratio = midpoint === minimum ? 1 : (value - minimum) / (midpoint - minimum)
    return interpolate(low, YELLOW, ratio)
  }
  const ratio = maximum === midpoint ? 1 : (value - midpoint) / (maximum - midpoint)
  return interpolate(YELLOW, high, ratio)
}

function memberLabel(member: ExplorePivotMemberKey): string {
  if (member.kind === "null") return "(blank)"
  if (member.kind === "nan") return "(NaN)"
  return String(member.value)
}

function pathLabel(
  path: ExplorePivotResult["row_paths"][number],
  level: number,
): string {
  if (path.is_grand_total) return level === 0 ? "Grand total" : ""
  const member = path.members[level]
  return member ? memberLabel(member) : ""
}

function cellKey(
  rowIndex: number,
  columnIndex: number,
  valueId: string,
): string {
  return `${rowIndex}:${columnIndex}:${valueId}`
}

export default function PivotTableGrid({ result, pivot }: PivotTableGridProps) {
  const [scrollTop, setScrollTop] = useState(0)
  const rowCount = result.row_paths.length
  const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2
  const unclampedStart = Math.max(
    0,
    Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN,
  )
  const start = Math.min(Math.max(0, rowCount - visibleCount), unclampedStart)
  const end = Math.min(rowCount, start + visibleCount)
  const visibleRows = result.row_paths.slice(start, end)

  const valuesById = useMemo(
    () => new Map(pivot.values.map((value) => [value.id, value])),
    [pivot.values],
  )
  const cells = useMemo(() => {
    const indexed = new Map<
      string,
      ExplorePivotResult["cells"][number]["value"]
    >()
    for (const cell of result.cells) {
      indexed.set(
        cellKey(cell.row_index, cell.column_index, cell.value_id),
        cell.value,
      )
    }
    return indexed
  }, [result.cells])
  const conditionalDomains = useMemo(() => {
    const values = new Map<string, number[]>()
    for (const cell of result.cells) {
      if (result.row_paths[cell.row_index]?.is_grand_total || result.column_paths[cell.column_index]?.is_grand_total) continue
      const numeric = numericCellValue(cell.value)
      if (numeric === null) continue
      const bucket = values.get(cell.value_id) ?? []
      bucket.push(numeric)
      values.set(cell.value_id, bucket)
    }
    return new Map(
      [...values].map(([valueId, numbers]) => {
        const sorted = [...numbers].sort((left, right) => left - right)
        return [valueId, [sorted[0], median(sorted), sorted[sorted.length - 1]] as const]
      }),
    )
  }, [result.cells, result.column_paths, result.row_paths])

  const dataColumnCount = result.column_paths.length * result.values.length
  const totalColumns = Math.max(
    1,
    result.row_fields.length + dataColumnCount,
  )
  const columnHeaderDepth = result.column_fields.length

  const rowFieldHeaders = result.row_fields.map((rowField, index) => (
    <th
      key={rowField}
      rowSpan={columnHeaderDepth + 1}
      scope="col"
      className="sticky z-10 min-w-[140px] px-2 py-1.5"
      style={{
        left: index * ROW_HEADER_WIDTH,
        background: "var(--bg-input)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      {rowField}
    </th>
  ))

  return (
    <div
      data-testid="pivot-table-scroll"
      className="max-h-80 overflow-auto"
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
    >
      <table
        className="w-max min-w-full border-collapse text-left text-[11px]"
        aria-label={`${pivot.name} results`}
      >
        <thead>
          {result.column_fields.map((field, level) => (
            <tr key={`${level}:${field}`}>
              {level === 0 && rowFieldHeaders}
              {result.column_paths.map((path, columnIndex) => (
                <th
                  key={columnIndex}
                  colSpan={result.values.length}
                  scope="colgroup"
                  className="px-2 py-1.5 font-medium"
                  style={{ borderBottom: "1px solid var(--border)" }}
                >
                  {pathLabel(path, level)}
                </th>
              ))}
            </tr>
          ))}
          <tr>
            {columnHeaderDepth === 0 && rowFieldHeaders}
            {result.column_paths.flatMap((_, columnIndex) =>
              result.values.map((value) => (
                <th
                  key={`${columnIndex}:${value.id}`}
                  scope="col"
                  className="px-2 py-1.5 font-semibold"
                  style={{ borderBottom: "1px solid var(--border)" }}
                >
                  {valuesById.get(value.id)?.display_name ?? value.field}
                </th>
              )),
            )}
          </tr>
        </thead>
        <tbody>
          {rowCount === 0 && (
            <tr>
              <td
                colSpan={totalColumns}
                className="px-3 py-5 text-center"
                style={{ color: "var(--text-muted)" }}
              >
                No rows match this pivot configuration.
              </td>
            </tr>
          )}
          {start > 0 && (
            <tr aria-hidden="true">
              <td
                colSpan={totalColumns}
                style={{ height: start * ROW_HEIGHT, padding: 0 }}
              />
            </tr>
          )}
          {visibleRows.map((rowPath, visibleIndex) => {
            const rowIndex = start + visibleIndex
            return (
              <tr key={rowIndex} style={{ height: ROW_HEIGHT }}>
                {result.row_fields.map((field, level) => (
                  <th
                    key={field}
                    scope="row"
                    className="sticky z-[1] min-w-[140px] whitespace-nowrap px-2 font-medium"
                    style={{
                      left: level * ROW_HEADER_WIDTH,
                      background: "var(--bg-input)",
                      borderBottom: "1px solid var(--border)",
                    }}
                  >
                    {pathLabel(rowPath, level)}
                  </th>
                ))}
                {result.column_paths.flatMap((_, columnIndex) =>
                  result.values.map((value) => {
                    const cell = cells.get(
                      cellKey(rowIndex, columnIndex, value.id),
                    )
                    const configuredValue = valuesById.get(value.id)
                    const numeric = numericCellValue(cell)
                    const scale = configuredValue?.color_scale
                    const domain = conditionalDomains.get(value.id)
                    const eligible =
                      !rowPath.is_grand_total &&
                      !result.column_paths[columnIndex]?.is_grand_total &&
                      numeric !== null &&
                      scale !== undefined &&
                      scale !== "none" &&
                      domain !== undefined
                    return (
                      <td
                        key={`${columnIndex}:${value.id}`}
                        className="whitespace-nowrap px-2"
                        data-conditional-format={eligible ? scale : undefined}
                        style={{
                          borderBottom: "1px solid var(--border)",
                          ...(eligible
                            ? {
                                background: conditionalColor(numeric, domain, scale),
                                color: PIVOT_CONDITIONAL_FORMAT_COLORS.cellText,
                              }
                            : {}),
                        }}
                      >
                        {cell === null || cell === undefined
                          ? "\u2014"
                          : String(cell)}
                      </td>
                    )
                  }),
                )}
              </tr>
            )
          })}
          {end < rowCount && (
            <tr aria-hidden="true">
              <td
                colSpan={totalColumns}
                style={{ height: (rowCount - end) * ROW_HEIGHT, padding: 0 }}
              />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
