import { useState, useMemo, useCallback } from "react"
import { Search, ChevronDown, ChevronRight } from "lucide-react"
import { configField } from "../../utils/configField"
import type { OnUpdateConfig } from "./_shared"
import type { ColumnInfo } from "../../types/node"
import { getDtypeColor } from "../../utils/dtypeColors"
import { EditorLabel } from "../../components/form"

interface GroupedColumnsTabProps {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  availableColumns: ColumnInfo[]
  columns: ColumnInfo[]
}

// ---------------------------------------------------------------------------
// Pattern grouping: replace numeric segments with * to collapse arrays
// ---------------------------------------------------------------------------

function toPattern(name: string): string {
  return name.split(".").map((s) => (/^\d+$/.test(s) ? "*" : s)).join(".")
}

function topPrefix(name: string): string {
  const i = name.indexOf(".")
  return i > 0 ? name.slice(0, i) : name
}

// ---------------------------------------------------------------------------
// Prefix stripping
// ---------------------------------------------------------------------------

/** Strip the first N dot-segments from a column name. */
function stripSegments(name: string, count: number): string {
  const parts = name.split(".")
  if (count >= parts.length) return parts[parts.length - 1]
  return parts.slice(count).join(".")
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PatternRow {
  pattern: string
  concreteColumns: string[]
  dtype: string
  shortPattern: string
}

interface ColumnGroup {
  prefix: string
  rows: PatternRow[]
  allConcreteNames: string[]
}

function buildGroups(columns: ColumnInfo[]): ColumnGroup[] {
  const patternMap = new Map<string, { cols: string[]; dtype: string }>()
  for (const col of columns) {
    const pat = toPattern(col.name)
    if (!patternMap.has(pat)) patternMap.set(pat, { cols: [], dtype: col.dtype })
    patternMap.get(pat)!.cols.push(col.name)
  }

  const groupMap = new Map<string, PatternRow[]>()
  for (const [pat, { cols, dtype }] of patternMap) {
    const prefix = topPrefix(pat)
    if (!groupMap.has(prefix)) groupMap.set(prefix, [])
    const shortPattern = pat.startsWith(prefix + ".") ? pat.slice(prefix.length + 1) : pat
    groupMap.get(prefix)!.push({ pattern: pat, concreteColumns: cols, dtype, shortPattern })
  }

  return Array.from(groupMap.entries()).map(([prefix, rows]) => ({
    prefix,
    rows,
    allConcreteNames: rows.flatMap((r) => r.concreteColumns),
  }))
}

// ---------------------------------------------------------------------------
// Clickable segment display
// ---------------------------------------------------------------------------

/** Parse a pattern like "licence.licence_type" into clickable segments. */
function SegmentedName({
  pattern,
  columnRenames,
  concreteColumns,
  onStripSegment,
  color,
}: {
  pattern: string
  columnRenames: Record<string, string>
  concreteColumns: string[]
  onStripSegment: (segmentIndex: number) => void
  color: string
}) {
  // The pattern segments (with group prefix already stripped)
  const segments = pattern.split(".")

  if (segments.length <= 1) {
    return <span style={{ color }}>{pattern}</span>
  }

  // Figure out which segments have been stripped by comparing the
  // shortPattern against what the renamed column looks like (also with
  // the group prefix removed).
  const strippedSegments = new Set<number>()
  const exampleCol = concreteColumns[0]
  const renamed = columnRenames[exampleCol]
  if (renamed) {
    // The renamed value might still include the group prefix (if only a
    // mid-level segment was stripped) or might have it removed (group strip).
    // Compare against shortPattern by stripping the same group prefix from
    // the renamed value.
    const groupPrefix = topPrefix(exampleCol)
    const renamedShort = renamed.startsWith(groupPrefix + ".")
      ? renamed.slice(groupPrefix.length + 1)
      : renamed
    const renamedParts = toPattern(renamedShort).split(".")

    // Only detect stripped segments if the renamed version is actually shorter
    if (renamedParts.length < segments.length) {
      let ri = 0
      for (let oi = 0; oi < segments.length; oi++) {
        if (ri < renamedParts.length && segments[oi] === renamedParts[ri]) {
          ri++
        } else {
          strippedSegments.add(oi)
        }
      }
    }
  }

  return (
    <span>
      {segments.map((seg, i) => {
        const isStripped = strippedSegments.has(i)
        const isLast = i === segments.length - 1
        const isStrippable = !isLast && !isStripped && seg !== "*"

        return (
          <span key={i}>
            {isStripped ? (
              // Struck-through segment — use a positioned line overlay instead of text-decoration
              <span
                style={{ position: "relative", color: "var(--text-muted)", cursor: "pointer" }}
                title="Click to restore this segment"
                onClick={(e) => { e.stopPropagation(); onStripSegment(i) }}
              >
                {seg}
                <span style={{ position: "absolute", left: 0, right: 0, top: "50%", height: "1px", background: "var(--danger)", pointerEvents: "none" }} />
              </span>
            ) : isStrippable ? (
              // Clickable segment that can be stripped
              <span
                style={{ color, cursor: "pointer", borderBottom: "1px dashed var(--border)" }}
                title={`Click to strip "${seg}" from the column name`}
                onClick={(e) => { e.stopPropagation(); onStripSegment(i) }}
              >
                {seg}
              </span>
            ) : (
              <span style={{ color }}>{seg}</span>
            )}
            {i < segments.length - 1 && <span style={{ color: "var(--text-muted)" }}>.</span>}
          </span>
        )
      })}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GroupedColumnsTab({ config, onUpdate, availableColumns, columns }: GroupedColumnsTabProps) {
  const [search, setSearch] = useState("")
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const selectedColumns = configField<string[]>(config, "selected_columns", [])
  const columnRenames = configField<Record<string, string>>(config, "column_renames", {})
  const allColumns = availableColumns.length > 0 ? availableColumns : columns
  const allNames = useMemo(() => allColumns.map((c) => c.name), [allColumns])
  const isAllSelected = selectedColumns.length === 0

  /** Strip the top-level group prefix from all columns in a group. */
  const stripGroupPrefix = useCallback((group: ColumnGroup) => {
    const newRenames = { ...columnRenames }
    for (const row of group.rows) {
      for (const col of row.concreteColumns) {
        const stripped = stripSegments(col, 1)
        if (stripped !== col) {
          newRenames[col] = stripped
        }
      }
    }
    onUpdate("column_renames", newRenames)
  }, [columnRenames, onUpdate])

  /** Restore all renames for a group. */
  const restoreGroupPrefix = useCallback((group: ColumnGroup) => {
    const newRenames = { ...columnRenames }
    const groupCols = new Set(group.allConcreteNames)
    for (const key of Object.keys(newRenames)) {
      if (groupCols.has(key)) delete newRenames[key]
    }
    onUpdate("column_renames", Object.keys(newRenames).length > 0 ? newRenames : {})
  }, [columnRenames, onUpdate])

  const isGroupStripped = useCallback((group: ColumnGroup): boolean => {
    return group.allConcreteNames.some((c) => c in columnRenames)
  }, [columnRenames])

  /**
   * Toggle stripping of a specific segment within a pattern row.
   * If already stripped, restore it. If not stripped, strip it.
   */
  const toggleSegmentStrip = useCallback((row: PatternRow, segmentIndex: number) => {
    const newRenames = { ...columnRenames }

    for (const col of row.concreteColumns) {
      // The segmentIndex is relative to the shortPattern (group prefix already stripped).
      // We need to find the corresponding index in the current (possibly already partially stripped) name.
      const exampleOriginal = row.concreteColumns[0]
      const originalFull = exampleOriginal
      const groupPrefix = topPrefix(originalFull)

      // Work on the part after the group prefix (or the full name if no group prefix rename)
      const currentAfterGroup = newRenames[col]
        ? newRenames[col]
        : (col.startsWith(groupPrefix + ".") ? col.slice(groupPrefix.length + 1) : col)

      const afterSegments = currentAfterGroup.split(".")

      // Check if this segment is currently present
      const patternSegments = row.shortPattern.split(".")
      const targetSeg = patternSegments[segmentIndex]

      if (!targetSeg || targetSeg === "*") return

      // Find if the target segment is in the current name
      const idxInCurrent = afterSegments.indexOf(targetSeg)

      if (idxInCurrent >= 0 && idxInCurrent < afterSegments.length - 1) {
        // Segment is present -> strip it
        const newAfter = [...afterSegments.slice(0, idxInCurrent), ...afterSegments.slice(idxInCurrent + 1)].join(".")
        // Reconstruct full renamed name
        const isGroupRenamed = col in newRenames && !newRenames[col].startsWith(groupPrefix + ".")
        const newFull = isGroupRenamed ? newAfter : (groupPrefix + "." + newAfter)
        if (newFull !== col) {
          newRenames[col] = newFull
        } else {
          delete newRenames[col]
        }
      } else {
        // Segment was already stripped -> restore it (put it back)
        // Restore to the version with only the group prefix stripped
        const withGroupStripped = col.startsWith(groupPrefix + ".") ? col.slice(groupPrefix.length + 1) : col
        const isGroupRenamed = col in newRenames && !newRenames[col].startsWith(groupPrefix + ".")
        if (isGroupRenamed) {
          newRenames[col] = withGroupStripped
        } else {
          delete newRenames[col]
        }
      }
    }

    onUpdate("column_renames", Object.keys(newRenames).length > 0 ? newRenames : {})
  }, [columnRenames, onUpdate])

  const filtered = useMemo(() => {
    if (!search) return allColumns
    const q = search.toLowerCase()
    return allColumns.filter((c) => {
      const pat = toPattern(c.name)
      return c.name.toLowerCase().includes(q) || pat.toLowerCase().includes(q)
    })
  }, [allColumns, search])

  const groups = useMemo(() => buildGroups(filtered), [filtered])

  const isSelected = (col: string) => isAllSelected || selectedColumns.includes(col)

  const togglePattern = (row: PatternRow) => {
    const concreteSet = new Set(row.concreteColumns)
    const allPatternSelected = row.concreteColumns.every((c) => isSelected(c))

    if (isAllSelected) {
      if (allPatternSelected) {
        const next = allNames.filter((c) => !concreteSet.has(c))
        onUpdate("selected_columns", next.length > 0 ? next : [allNames[0]])
      }
      return
    }

    if (allPatternSelected) {
      const next = selectedColumns.filter((c) => !concreteSet.has(c))
      onUpdate("selected_columns", next.length > 0 ? next : [allNames[0]])
    } else {
      const current = new Set(selectedColumns)
      for (const c of row.concreteColumns) current.add(c)
      const next = Array.from(current)
      onUpdate("selected_columns", next.length >= allNames.length ? [] : next)
    }
  }

  const toggleGroup = (group: ColumnGroup) => {
    const groupSet = new Set(group.allConcreteNames)
    const allGroupSelected = group.allConcreteNames.every((c) => isSelected(c))

    if (isAllSelected) {
      if (allGroupSelected) {
        const next = allNames.filter((c) => !groupSet.has(c))
        onUpdate("selected_columns", next.length > 0 ? next : [allNames[0]])
      }
      return
    }

    if (allGroupSelected) {
      const next = selectedColumns.filter((c) => !groupSet.has(c))
      onUpdate("selected_columns", next.length > 0 ? next : [allNames[0]])
    } else {
      const current = new Set(selectedColumns)
      for (const c of group.allConcreteNames) current.add(c)
      const next = Array.from(current)
      onUpdate("selected_columns", next.length >= allNames.length ? [] : next)
    }
  }

  const toggleCollapse = (prefix: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(prefix)) next.delete(prefix)
      else next.add(prefix)
      return next
    })
  }

  const selectAll = () => onUpdate("selected_columns", [])
  const selectNone = () => {
    if (allNames.length > 0) onUpdate("selected_columns", [allNames[0]])
  }

  const selectedCount = isAllSelected ? allNames.length : selectedColumns.length

  if (allColumns.length === 0) {
    return (
      <div className="px-4 py-6 text-center">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Preview or run this node to see its output columns
        </p>
      </div>
    )
  }

  return (
    <div className="px-4 py-3 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <EditorLabel as="span">Output Columns</EditorLabel>
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
          {selectedCount} / {allNames.length}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <div className="flex-1 relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter columns..."
            className="w-full pl-7 pr-2 py-1 text-xs rounded-md border bg-transparent focus:outline-none focus:ring-1"
            style={{ color: "var(--text-primary)", borderColor: "var(--border)", background: "var(--bg-input)" }}
          />
        </div>
        <button
          onClick={selectAll}
          className="text-[10px] font-medium px-1.5 py-0.5 rounded"
          style={{ color: isAllSelected ? "var(--text-muted)" : "var(--accent)" }}
          disabled={isAllSelected}
        >
          All
        </button>
        <button
          onClick={selectNone}
          className="text-[10px] font-medium px-1.5 py-0.5 rounded"
          style={{ color: "var(--accent)" }}
        >
          None
        </button>
      </div>

      <div
        className="rounded-lg overflow-hidden max-h-[400px] overflow-y-auto"
        style={{ border: "1px solid var(--border)", background: "var(--bg-input)" }}
      >
        {groups.map((group) => {
          const isCollapsed = collapsed.has(group.prefix)
          const groupSelectedCount = group.allConcreteNames.filter((c) => isSelected(c)).length
          const allGroupSelected = groupSelectedCount === group.allConcreteNames.length
          const someGroupSelected = groupSelectedCount > 0 && !allGroupSelected
          const stripped = isGroupStripped(group)

          return (
            <div key={group.prefix}>
              {/* Group header */}
              <div
                className="grouped-col-group-header flex items-center gap-1.5 px-2.5 py-1.5 select-none"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <input
                  type="checkbox"
                  checked={allGroupSelected}
                  ref={(el) => { if (el) el.indeterminate = someGroupSelected }}
                  onChange={() => toggleGroup(group)}
                  className="accent-blue-500 rounded"
                  aria-label={`Select all ${group.prefix} columns`}
                />
                <span
                  className="cursor-pointer"
                  onClick={() => toggleCollapse(group.prefix)}
                  title={isCollapsed ? "Expand group" : "Collapse group"}
                >
                  {isCollapsed
                    ? <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
                    : <ChevronDown size={12} style={{ color: "var(--text-muted)" }} />
                  }
                </span>
                {/* Group prefix name -- clickable to strip/restore */}
                {group.rows.some((r) => r.concreteColumns.some((c) => c.includes("."))) ? (
                  <span
                    className="text-xs font-semibold cursor-pointer"
                    style={{
                      position: "relative",
                      color: stripped ? "var(--text-muted)" : "var(--text-primary)",
                      borderBottom: stripped ? "none" : "1px dashed var(--border)",
                    }}
                    onClick={() => {
                      if (stripped) restoreGroupPrefix(group)
                      else stripGroupPrefix(group)
                    }}
                    title={stripped ? `Restore "${group.prefix}" prefix` : `Strip "${group.prefix}" prefix from column names`}
                  >
                    {group.prefix}
                    {stripped && (
                      <span style={{ position: "absolute", left: 0, right: 0, top: "50%", height: "1px", background: "var(--danger)", pointerEvents: "none" }} />
                    )}
                  </span>
                ) : (
                  <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                    {group.prefix}
                  </span>
                )}
                <span className="text-[10px] ml-auto" style={{ color: "var(--text-muted)" }}>
                  {groupSelectedCount}/{group.allConcreteNames.length}
                </span>
              </div>

              {/* Pattern rows */}
              {!isCollapsed && group.rows.map((row) => {
                const allPatternSelected = row.concreteColumns.every((c) => isSelected(c))
                const somePatternSelected = row.concreteColumns.some((c) => isSelected(c)) && !allPatternSelected
                const count = row.concreteColumns.length
                const nameColor = allPatternSelected ? "var(--text-primary)" : somePatternSelected ? "var(--text-secondary)" : "var(--text-muted)"

                return (
                  <div
                    key={row.pattern}
                    className="grouped-col-pattern-row flex items-center cursor-pointer"
                    style={{ borderBottom: "1px solid var(--border)" }}
                    onClick={() => togglePattern(row)}
                  >
                    <div className="px-2.5 py-1 pl-7 shrink-0">
                      <input
                        type="checkbox"
                        checked={allPatternSelected}
                        ref={(el) => { if (el) el.indeterminate = somePatternSelected }}
                        onChange={() => togglePattern(row)}
                        onClick={(e) => e.stopPropagation()}
                        className="accent-blue-500 rounded"
                        aria-label={`Select ${row.shortPattern}`}
                      />
                    </div>
                    <div className="px-1.5 py-1 font-mono text-xs flex-1 min-w-0 truncate">
                      <SegmentedName
                        pattern={row.shortPattern}
                        columnRenames={columnRenames}
                        concreteColumns={row.concreteColumns}
                        onStripSegment={(idx) => toggleSegmentStrip(row, idx)}
                        color={nameColor}
                      />
                    </div>
                    {count > 1 && (
                      <div className="px-1 shrink-0">
                        <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
                          ×{count}
                        </span>
                      </div>
                    )}
                    <div className="px-2.5 py-1 shrink-0">
                      <span className={`text-[11px] font-medium ${getDtypeColor(row.dtype)}`}>
                        {row.dtype}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>

      {!isAllSelected && (
        <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Deselected columns will be dropped via <code className="font-mono">.select()</code> on this node's output.
        </p>
      )}
    </div>
  )
}
