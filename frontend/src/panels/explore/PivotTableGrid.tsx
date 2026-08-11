import { useMemo, useState } from "react"

import type { ExplorePivotMemberKey, ExplorePivotResult } from "../../api/types"
import type { ExplorePivotConfig } from "./pivotConfig"

type PivotTableGridProps = {
  result: ExplorePivotResult
  pivot: ExplorePivotConfig
}

const ROW_HEIGHT = 32
const VIEWPORT_HEIGHT = 320
const OVERSCAN = 5
const ROW_HEADER_WIDTH = 140

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
                    return (
                      <td
                        key={`${columnIndex}:${value.id}`}
                        className="whitespace-nowrap px-2"
                        style={{ borderBottom: "1px solid var(--border)" }}
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
