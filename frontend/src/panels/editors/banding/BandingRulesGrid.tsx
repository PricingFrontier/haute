import { useMemo, useCallback, useState } from "react"
import { Copy, Trash } from "lucide-react"
import { CHART_COLORS } from "../../../theme/colors"
import useToastStore from "../../../stores/useToastStore"
import { buildTsv, parsePastedGrid, writeClipboardText } from "../shared/tableClipboard"
import type { BandingFactor, CategoricalRule } from "../../../types/banding"

const CATEGORICAL_FIELDS = ["value", "assignment"] as const
const CATEGORICAL_COPY_HEADERS = ["Value", "Maps To"] as const

/** Generate a short unique key for a rule row. */
let _ruleIdSeq = 0
// eslint-disable-next-line react-refresh/only-export-components
export function nextRuleId(): string {
  return `rule_${++_ruleIdSeq}_${Date.now().toString(36)}`
}

/** Ensure every rule has a stable `_id` key. Ids generated for id-less rules
 *  are remembered per rule object, so a parent that recreates the rules array
 *  without an edit keeps the same row identity (and the user's focus). */
function ensureRuleIds(
  rules: CategoricalRule[],
  generatedIds: WeakMap<object, string>,
): CategoricalRule[] {
  let changed = false
  const result = rules.map((r) => {
    if ((r as Record<string, unknown>)._id) return r
    changed = true
    let id = generatedIds.get(r)
    if (!id) {
      id = nextRuleId()
      generatedIds.set(r, id)
    }
    return { ...r, _id: id }
  })
  return changed ? result : rules
}

/** Extract the stable key from a rule (falls back to index). */
function ruleKey(rule: CategoricalRule, index: number): string {
  return (rule as Record<string, unknown>)._id as string || `fallback_${index}`
}

function isHeaderRow(cols: string[], expected: readonly string[]): boolean {
  if (cols.length < expected.length) return false
  return expected.every((header, index) => cols[index]?.trim().toLowerCase() === header.toLowerCase())
}

function isCategoricalHeaderRow(cols: string[]): boolean {
  return isHeaderRow(cols, CATEGORICAL_COPY_HEADERS) || isHeaderRow(cols, CATEGORICAL_FIELDS)
}

function dropRecognizedHeaderRow(matrix: string[][], fieldIndex: number): string[][] {
  if (fieldIndex !== 0 || matrix.length === 0) return matrix
  return isCategoricalHeaderRow(matrix[0]) ? matrix.slice(1) : matrix
}

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
    if (parsed.length === 0 && isCategoricalHeaderRow(cols)) {
      continue
    }
    if (cols.length >= 2) {
      parsed.push({ value: cols[0], assignment: cols[1] })
    }
  }

  return parsed
}

function applyPastedRuleRange(
  rules: CategoricalRule[],
  rowIndex: number,
  fieldIndex: number,
  matrix: string[][],
): CategoricalRule[] {
  const next = [...rules]

  for (let rowOffset = 0; rowOffset < matrix.length; rowOffset++) {
    const targetRow = rowIndex + rowOffset
    next[targetRow] = next[targetRow] ? { ...next[targetRow] } : { value: "", assignment: "" }

    for (let colOffset = 0; colOffset < matrix[rowOffset].length; colOffset++) {
      const field = CATEGORICAL_FIELDS[fieldIndex + colOffset]
      if (!field) continue
      next[targetRow][field] = matrix[rowOffset][colOffset]
    }
  }

  return next
}

function rulesToTsv(rules: CategoricalRule[]): string {
  return buildTsv([
    [...CATEGORICAL_COPY_HEADERS],
    ...rules.map((rule) => [rule.value ?? "", rule.assignment ?? ""]),
  ])
}

const DELETE_BUTTON_CLASS = "p-0.5 rounded transition-colors text-[var(--text-muted)] hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
const ACTION_BUTTON_CLASS = "accent-hover-btn flex size-6 items-center justify-center rounded"
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
  onAddRule,
}: {
  factor: BandingFactor
  onUpdateFactor: (patch: Partial<BandingFactor>) => void
  accentColor?: string
  /** Rows each rule claims; null while a count is still being worked out. */
  matchCounts?: (number | null)[]
  onAddRule?: () => void
}) {
  const addToast = useToastStore(s => s.addToast)
  const rawRules = useMemo(() => (factor.rules || []) as CategoricalRule[], [factor.rules])

  const [generatedRuleIds] = useState(() => new WeakMap<object, string>())
  const rules = useMemo(() => ensureRuleIds(rawRules, generatedRuleIds), [rawRules, generatedRuleIds])

  const setRules = (r: CategoricalRule[]) => onUpdateFactor({ rules: r })
  const updateRule = (idx: number, field: string, value: string) => {
    const next = [...rules]; next[idx] = { ...next[idx], [field]: value }; setRules(next)
  }
  const removeRule = (idx: number) => setRules(rules.filter((_, i) => i !== idx))

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLDivElement>) => {
    const text = e.clipboardData.getData("text/plain")
    if (!text.includes("\t")) return // Not TSV data
    e.preventDefault()
    const parsed = parsePastedRules(text)
    if (parsed.length > 0) {
      setRules([...rules, ...parsed])
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rules])

  const handleCellPaste = useCallback((
    e: React.ClipboardEvent<HTMLInputElement>,
    ruleIndex: number,
    fieldIndex: number,
  ) => {
    const text = e.clipboardData.getData("text/plain")
    if (!text.includes("\t") && !text.includes("\n") && !text.includes("\r")) return

    e.preventDefault()
    e.stopPropagation()

    const matrix = dropRecognizedHeaderRow(parsePastedGrid(text), fieldIndex)
    if (matrix.length === 0) return

    setRules(applyPastedRuleRange(rules, ruleIndex, fieldIndex, matrix))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rules])

  const handleCopyBanding = useCallback(() => {
    void writeClipboardText(rulesToTsv(rules)).catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error)
      addToast("error", `Could not copy banding TSV: ${detail}`)
    })
  }, [rules, addToast])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>, ruleIndex: number) => {
    if (e.key === "Enter" && ruleIndex === rules.length - 1 && onAddRule) {
      onAddRule()
    }
  }, [rules.length, onAddRule])

  const showMatchCounts = !!matchCounts

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
              <tr key={ruleKey(rule, i)}>
                {COLUMNS.map((column, fieldIndex) => {
                  const common = {
                    value: String(rule[column.field] ?? ""),
                    onChange: (e: React.ChangeEvent<HTMLInputElement>) => updateRule(i, column.field, e.target.value),
                    onPaste: (e: React.ClipboardEvent<HTMLInputElement>) => handleCellPaste(e, i, fieldIndex),
                    "aria-label": column.ariaLabel(i + 1),
                  }
                  return (
                    <td key={column.field} className={CELL_CLASS}>
                      {column.cell === "value" ? (
                        <input type="text" {...common}
                          className={BOXED_INPUT_CLASS}
                          style={BOXED_CELL_STYLE} placeholder="" />
                      ) : (
                        <input type="text" {...common}
                          onKeyDown={(e) => handleKeyDown(e, i)}
                          className={BOXED_LABEL_INPUT_CLASS}
                          style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: accentColor }} placeholder="" />
                      )}
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
          onClick={handleCopyBanding}
          className={ACTION_BUTTON_CLASS}
          style={{ color: 'var(--text-secondary)', ['--node-accent' as string]: accentColor }}
        >
          <Copy size={13} aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
