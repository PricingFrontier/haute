import { useMemo } from "react"
import { Copy, Trash } from "lucide-react"
import { CHART_COLORS } from "../../../theme/colors"
import { parsePastedGrid } from "../shared/tableClipboard"
import { isGridHeaderRow, useTwoColumnGridClipboard } from "../shared/useTwoColumnGridClipboard"
import type { BandingFactor, CategoricalRule } from "../../../types/banding"
import { bandName, categoricalRuleIssues } from "./bandingUtils"
import { useRowKeys } from "../shared/useRowKeys"
import { CellIssueMark } from "../shared/CellIssueMark"
import { cellIssueStyle } from "../shared/cellIssueStyle"
import { GridCell } from "../shared/GridCell"

const CATEGORICAL_FIELDS = ["value", "assignment"] as const
const RULE_GRID = { name: "banding", fields: CATEGORICAL_FIELDS, copyHeaders: ["Value", "Maps To"] } as const

function isBlankPastedRow(cols: string[]): boolean {
  return cols.every(col => col.trim() === "")
}

/** Parse pasted TSV text into rules: a value and the label it maps to per row. */
function parsePastedRules(text: string): CategoricalRule[] {
  const rows = parsePastedGrid(text)
  const parsed: CategoricalRule[] = []

  for (const cols of rows) {
    if (isBlankPastedRow(cols)) {
      continue
    }
    if (parsed.length === 0 && isGridHeaderRow(RULE_GRID, cols)) {
      continue
    }
    if (cols.length >= 2) {
      parsed.push({ value: cols[0], assignment: cols[1] })
    }
  }

  return parsed
}

const EMPTY_RULE: CategoricalRule = { value: "", assignment: "" }

const DELETE_BUTTON_CLASS = "p-0.5 rounded transition-colors text-[var(--text-muted)] hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
const ACTION_BUTTON_CLASS = "accent-hover-btn -my-1 flex size-6 items-center justify-center rounded"
const CELL_CLASS = "px-0.5 py-0.5"
const DELETE_CELL_CLASS = `${CELL_CLASS} text-center`
const MATCH_CELL_CLASS = `${CELL_CLASS} text-right text-[10px]`
const BOXED_INPUT_CLASS = "w-full px-1 py-0.5 rounded text-[11px] font-mono focus:outline-none"
const BOXED_LABEL_INPUT_CLASS = `${BOXED_INPUT_CLASS} font-semibold`
const BOXED_CELL_STYLE = { background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-primary)' }
const HEADER_CELL_CLASS = "text-left px-2 py-1.5 font-semibold"

/** One editable rule column: its field, header, and the kind of cell it edits. */
type RuleColumn = {
  field: (typeof CATEGORICAL_FIELDS)[number]
  header: string
  cell: "value" | "label"
  ariaLabel: (ruleNumber: number) => string
}

const COLUMNS: readonly RuleColumn[] = [
  { field: CATEGORICAL_FIELDS[0], header: "Value", cell: "value", ariaLabel: (n) => `Rule ${n} match value` },
  { field: CATEGORICAL_FIELDS[1], header: "Maps To", cell: "label", ariaLabel: (n) => `Rule ${n} group name` },
]

/** Categorical rules: each value in the data and the label it maps to.
 *  Numeric breakpoints are edited in `BreakpointGrid`. */
export function BandingRulesGrid({
  factor,
  onUpdateFactor,
  accentColor = CHART_COLORS.bandingAccent,
  matchCounts,
  addsRuleOnEnter = false,
}: {
  factor: BandingFactor
  onUpdateFactor: (patch: Partial<BandingFactor>) => void
  accentColor?: string
  /** Rows each rule claims; null while a count is still being worked out. */
  matchCounts?: (number | null)[]
  /** Enter in the last rule's label cell commits it and adds a rule, in one update. */
  addsRuleOnEnter?: boolean
}) {
  const rules = useMemo(() => (factor.rules || []) as CategoricalRule[], [factor.rules])
  // Each row's key lives here, never in the config: the grid's own writes keep every
  // key, so focus stays in the cell being typed in and a cell's draft stays with its row.
  const rowKeys = useRowKeys(rules)

  // A rule is its value and mapping: any editor-only key (an `_id` an earlier
  // editor kept on the rule) is dropped from what is written, before the row keys
  // see the write, so the rules that come back are the ones they expect. Every
  // write (a cell, Enter, a paste) gives the mapping as a band name.
  const written = (r: CategoricalRule[]): CategoricalRule[] =>
    r.map(({ value, assignment }) => ({ value, assignment: bandName(String(assignment ?? "")) }))
  const setRules = (r: CategoricalRule[]) => onUpdateFactor({ rules: r })
  // A paste fills cells from the one pasted into and may add rows below.
  const pasteRules = (next: CategoricalRule[]) =>
    setRules(next.length > rules.length ? rowKeys.add(written(next)) : rowKeys.edit(written(next)))
  const { handleCellPaste, copyTsv } = useTwoColumnGridClipboard(RULE_GRID, rules, pasteRules)
  const withCell = (idx: number, field: string, value: string): CategoricalRule[] =>
    written(rules.map((rule, i) => (i === idx ? { ...rule, [field]: value } : rule)))
  const updateRule = (idx: number, field: string, value: string) => setRules(rowKeys.edit(withCell(idx, field, value)))
  const removeRule = (idx: number) => setRules(rowKeys.remove(idx, written(rules.filter((_, i) => i !== idx))))

  const handlePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    const text = e.clipboardData.getData("text/plain")
    if (!text.includes("\t")) return // Not TSV data
    e.preventDefault()
    const parsed = parsePastedRules(text)
    if (parsed.length > 0) setRules(rowKeys.add(written([...rules, ...parsed])))
  }

  const showMatchCounts = !!matchCounts
  const issues = categoricalRuleIssues(rules)

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--bg-input)' }}>
      <div className="max-h-[300px] overflow-y-auto" data-testid="banding-scroll-container" onPaste={handlePaste}>
        <table className="w-full text-[11px]">
          <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
              {COLUMNS.map((column) => (
                <th key={column.field} className={HEADER_CELL_CLASS} style={{ color: 'var(--text-muted)' }}>
                  {column.header}
                </th>
              ))}
              {showMatchCounts && (
                <th className="text-right px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 50 }}>Matches</th>
              )}
              <th style={{ width: 28 }}></th>
            </tr>
          </thead>
          <tbody>
            {rules.length === 0 ? (
              <tr><td colSpan={COLUMNS.length + (showMatchCounts ? 2 : 1)} className="px-2 py-3 text-center" style={{ color: 'var(--text-muted)' }}>No rules yet</td></tr>
            ) : rules.map((rule, i) => (
              <tr key={rowKeys.keys[i]}>
                {COLUMNS.map((column, fieldIndex) => {
                  const issue = issues.get(i)?.[column.field]
                  const issueId = `rule-${i + 1}-${column.field}-issue`
                  const common = {
                    value: String(rule[column.field] ?? ""),
                    row: rowKeys.keys[i],
                    onCommit: (next: string) => updateRule(i, column.field, next),
                    onPaste: (e: React.ClipboardEvent<HTMLInputElement>) => handleCellPaste(e, i, fieldIndex),
                    "aria-label": column.ariaLabel(i + 1),
                    "aria-invalid": issue ? true : undefined,
                    "aria-describedby": issue ? issueId : undefined,
                    title: issue,
                  }
                  const baseStyle = column.cell === "value" ? BOXED_CELL_STYLE : { ...BOXED_CELL_STYLE, color: accentColor }
                  const style = issue ? cellIssueStyle(baseStyle, "error") : baseStyle
                  const className = `${column.cell === "value" ? BOXED_INPUT_CLASS : BOXED_LABEL_INPUT_CLASS}${issue ? " pr-5" : ""}`
                  return (
                    <td key={column.field} className={CELL_CLASS}>
                      <div className="relative">
                        <GridCell
                          type="text"
                          {...common}
                          onEnter={column.cell === "label" && addsRuleOnEnter && i === rules.length - 1
                            ? (shown) => {
                                setRules(rowKeys.add([...withCell(i, column.field, shown), { ...EMPTY_RULE }]))
                                return true
                              }
                            : undefined}
                          className={className}
                          style={style}
                          placeholder=""
                        />
                        {issue && <CellIssueMark id={issueId} message={issue} severity="error" />}
                      </div>
                    </td>
                  )
                })}
                {showMatchCounts && (
                  <td className={MATCH_CELL_CLASS}>
                    <span
                      title={matchCounts[i] === null ? "Counting…" : undefined}
                      style={{ color: matchCounts[i] === 0 ? 'var(--danger)' : 'var(--text-muted)', opacity: matchCounts[i] === 0 ? 0.7 : 1 }}
                    >
                      {matchCounts[i] === null ? "…" : (matchCounts[i] ?? "")}
                    </span>
                  </td>
                )}
                <td className={DELETE_CELL_CLASS}>
                  <button onClick={() => removeRule(i)}
                    aria-label={`Delete rule ${i + 1}`}
                    className={DELETE_BUTTON_CLASS}>
                    <Trash size={11} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-end px-2 py-1.5" style={{ background: 'var(--bg-elevated)', borderTop: '1px solid var(--border)' }}>
        <button
          type="button"
          aria-label="Copy banding as TSV"
          title="Copy banding as TSV"
          onClick={copyTsv}
          className={ACTION_BUTTON_CLASS}
          style={{ color: 'var(--text-secondary)', ['--node-accent' as string]: accentColor }}
        >
          <Copy size={13} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
