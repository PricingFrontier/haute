import { useMemo, useCallback, useState, type ReactNode } from "react"
import { Copy, Trash } from "lucide-react"
import { CHART_COLORS } from "../../../theme/colors"
import useToastStore from "../../../stores/useToastStore"
import { buildTsv, parsePastedGrid, writeClipboardText } from "../shared/tableClipboard"
import type { BandingFactor, ContinuousRule, CategoricalRule, BreakpointRule } from "../../../types/banding"

const OPS = ["<", "<=", ">", ">=", "="]
const CONTINUOUS_FIELDS = ["op1", "val1", "op2", "val2", "assignment"] as const
const CATEGORICAL_FIELDS = ["value", "assignment"] as const
const CONTINUOUS_COPY_HEADERS = ["From", "Value", "To", "Value", "Label"] as const
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
  rules: (ContinuousRule | CategoricalRule | BreakpointRule)[],
  generatedIds: WeakMap<object, string>,
): (ContinuousRule | CategoricalRule | BreakpointRule)[] {
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
function ruleKey(rule: ContinuousRule | CategoricalRule | BreakpointRule, index: number): string {
  return (rule as Record<string, unknown>)._id as string || `fallback_${index}`
}

function isHeaderRow(cols: string[], expected: readonly string[]): boolean {
  if (cols.length < expected.length) return false
  return expected.every((header, index) => cols[index]?.trim().toLowerCase() === header.toLowerCase())
}

function isContinuousHeaderRow(cols: string[]): boolean {
  return isHeaderRow(cols, CONTINUOUS_COPY_HEADERS) || isHeaderRow(cols, CONTINUOUS_FIELDS)
}

function isCategoricalHeaderRow(cols: string[]): boolean {
  return isHeaderRow(cols, CATEGORICAL_COPY_HEADERS) || isHeaderRow(cols, CATEGORICAL_FIELDS)
}

function dropRecognizedHeaderRow(
  matrix: string[][],
  mode: "continuous" | "categorical",
  fieldIndex: number,
): string[][] {
  if (fieldIndex !== 0 || matrix.length === 0) return matrix
  const isHeader = mode === "categorical" ? isCategoricalHeaderRow(matrix[0]) : isContinuousHeaderRow(matrix[0])
  return isHeader ? matrix.slice(1) : matrix
}

function isBlankPastedRow(cols: string[]): boolean {
  return cols.every(col => col.trim() === "")
}

/** Parse pasted TSV text into rules. */
function parsePastedRules(
  text: string,
  mode: "continuous" | "categorical" | "breakpoints",
): (ContinuousRule | CategoricalRule)[] {
  const rows = parsePastedGrid(text)
  const parsed: (ContinuousRule | CategoricalRule)[] = []

  for (const cols of rows) {
    if (isBlankPastedRow(cols)) {
      continue
    }
    if (parsed.length === 0 && (mode === "categorical" ? isCategoricalHeaderRow(cols) : isContinuousHeaderRow(cols))) {
      continue
    }

    if (mode === "categorical") {
      if (cols.length >= 2) {
        parsed.push({ value: cols[0], assignment: cols[1] })
      }
    } else {
      if (cols.length === 2) {
        // val1, assignment — auto-set op1
        parsed.push({
          op1: parsed.length === 0 ? ">=" : ">",
          val1: cols[0],
          op2: "",
          val2: "",
          assignment: cols[1],
        })
      } else if (cols.length === 3) {
        // val1, val2, assignment — auto-set op1=">", op2="<="
        parsed.push({
          op1: ">",
          val1: cols[0],
          op2: "<=",
          val2: cols[1],
          assignment: cols[2],
        })
      } else if (cols.length >= 5) {
        // op1, val1, op2, val2, assignment
        parsed.push({
          op1: cols[0],
          val1: cols[1],
          op2: cols[2],
          val2: cols[3],
          assignment: cols[4],
        })
      }
    }
  }

  return parsed
}

function emptyRule(mode: "continuous" | "categorical"): ContinuousRule | CategoricalRule {
  if (mode === "categorical") {
    return { value: "", assignment: "" }
  }
  return { op1: "", val1: "", op2: "", val2: "", assignment: "" }
}

function applyPastedRuleRange(
  rules: (ContinuousRule | CategoricalRule | BreakpointRule)[],
  mode: "continuous" | "categorical",
  rowIndex: number,
  fieldIndex: number,
  matrix: string[][],
): (ContinuousRule | CategoricalRule | BreakpointRule)[] {
  const fields = mode === "categorical" ? CATEGORICAL_FIELDS : CONTINUOUS_FIELDS
  const next = [...rules]

  for (let rowOffset = 0; rowOffset < matrix.length; rowOffset++) {
    const targetRow = rowIndex + rowOffset
    if (!next[targetRow]) {
      next[targetRow] = emptyRule(mode)
    } else {
      next[targetRow] = { ...next[targetRow] }
    }

    for (let colOffset = 0; colOffset < matrix[rowOffset].length; colOffset++) {
      const field = fields[fieldIndex + colOffset]
      if (!field) continue
      ;(next[targetRow] as Record<string, string>)[field] = matrix[rowOffset][colOffset]
    }
  }

  return next
}

function rulesToTsv(rules: (ContinuousRule | CategoricalRule | BreakpointRule)[], mode: "continuous" | "categorical"): string {
  if (mode === "categorical") {
    return buildTsv([
      [...CATEGORICAL_COPY_HEADERS],
      ...(rules as CategoricalRule[]).map((rule) => [rule.value ?? "", rule.assignment ?? ""]),
    ])
  }

  return buildTsv([
    [...CONTINUOUS_COPY_HEADERS],
    ...(rules as ContinuousRule[]).map((rule) => [
      rule.op1 ?? "",
      rule.val1 ?? "",
      rule.op2 ?? "",
      rule.val2 ?? "",
      rule.assignment ?? "",
    ]),
  ])
}

const DELETE_BUTTON_CLASS = "p-0.5 rounded transition-colors text-[var(--text-muted)] hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
const ACTION_BUTTON_CLASS = "accent-hover-btn flex size-6 items-center justify-center rounded"
const CELL_CLASS = "px-0.5 py-0.5"
const DELETE_CELL_CLASS = `${CELL_CLASS} text-center`
const MATCH_CELL_CLASS = `${CELL_CLASS} text-right text-[10px]`
const BOXED_INPUT_CLASS = "w-full px-1 py-0.5 rounded text-[11px] font-mono focus:outline-none"
const BOXED_LABEL_INPUT_CLASS = `${BOXED_INPUT_CLASS} font-semibold`
const BOXED_SELECT_CLASS = "w-full px-1 py-0.5 rounded text-[11px] font-mono appearance-none"
const BOXED_CELL_STYLE = { background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-primary)' }
const HEADER_CELL_CLASS = "text-left px-2 py-1.5 font-semibold"

/** One editable rule column: its field, header, and the kind of cell it edits. */
type RuleColumn = {
  field: string
  header: ReactNode
  width?: number
  cell: "operator" | "value" | "label"
  ariaLabel: (ruleNumber: number) => string
}

const CONTINUOUS_COLUMNS: readonly RuleColumn[] = [
  { field: CONTINUOUS_FIELDS[0], header: "From", width: 52, cell: "operator", ariaLabel: (n) => `Rule ${n} lower operator` },
  { field: CONTINUOUS_FIELDS[1], header: "Value", width: 60, cell: "value", ariaLabel: (n) => `Rule ${n} lower value` },
  {
    field: CONTINUOUS_FIELDS[2],
    header: <>To <span style={{ color: 'var(--text-muted)', opacity: 0.55, fontWeight: 'normal' }}>(opt.)</span></>,
    width: 52,
    cell: "operator",
    ariaLabel: (n) => `Rule ${n} upper operator`,
  },
  { field: CONTINUOUS_FIELDS[3], header: "Value", width: 60, cell: "value", ariaLabel: (n) => `Rule ${n} upper value` },
  { field: CONTINUOUS_FIELDS[4], header: "Label", cell: "label", ariaLabel: (n) => `Rule ${n} label` },
]

const CATEGORICAL_COLUMNS: readonly RuleColumn[] = [
  { field: CATEGORICAL_FIELDS[0], header: "Value", cell: "value", ariaLabel: (n) => `Rule ${n} match value` },
  { field: CATEGORICAL_FIELDS[1], header: "Maps To", cell: "label", ariaLabel: (n) => `Rule ${n} group name` },
]

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
  matchCounts?: number[]
  onAddRule?: () => void
}) {
  const addToast = useToastStore(s => s.addToast)
  const rawRules = useMemo(() => factor.rules || [], [factor.rules])

  const [generatedRuleIds] = useState(() => new WeakMap<object, string>())
  const rules = useMemo(() => ensureRuleIds(rawRules, generatedRuleIds), [rawRules, generatedRuleIds])

  const bt = factor.banding || "continuous"

  const setRules = (r: (ContinuousRule | CategoricalRule | BreakpointRule)[]) => onUpdateFactor({ rules: r })
  const updateRule = (idx: number, field: string, value: string) => {
    const next = [...rules]; next[idx] = { ...next[idx], [field]: value }; setRules(next)
  }
  const removeRule = (idx: number) => setRules(rules.filter((_, i) => i !== idx))

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLDivElement>) => {
    const text = e.clipboardData.getData("text/plain")
    if (!text.includes("\t")) return // Not TSV data
    e.preventDefault()
    const parsed = parsePastedRules(text, bt)
    if (parsed.length > 0) {
      setRules([...rules, ...parsed])
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rules, bt])

  const handleCellPaste = useCallback((
    e: React.ClipboardEvent<HTMLInputElement | HTMLSelectElement>,
    ruleIndex: number,
    fieldIndex: number,
  ) => {
    const text = e.clipboardData.getData("text/plain")
    if (!text.includes("\t") && !text.includes("\n") && !text.includes("\r")) return

    e.preventDefault()
    e.stopPropagation()

    const matrix = dropRecognizedHeaderRow(
      parsePastedGrid(text),
      bt === "categorical" ? "categorical" : "continuous",
      fieldIndex,
    )
    if (matrix.length === 0) return

    setRules(applyPastedRuleRange(rules, bt === "categorical" ? "categorical" : "continuous", ruleIndex, fieldIndex, matrix))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rules, bt])

  const handleCopyBanding = useCallback(() => {
    void writeClipboardText(rulesToTsv(rules, bt === "categorical" ? "categorical" : "continuous")).catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error)
      addToast("error", `Could not copy banding TSV: ${detail}`)
    })
  }, [rules, bt, addToast])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>, ruleIndex: number) => {
    if (e.key === "Enter" && ruleIndex === rules.length - 1 && onAddRule) {
      onAddRule()
    }
  }, [rules.length, onAddRule])

  const showMatchCounts = !!matchCounts

  const columns = bt === "continuous" ? CONTINUOUS_COLUMNS : CATEGORICAL_COLUMNS

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--bg-input)' }}>
      <div className="max-h-[300px] overflow-y-auto" data-testid="banding-scroll-container" onPaste={handlePaste}>
        <table className="w-full text-[11px]">
          <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
              {columns.map((column, index) => (
                <th key={index} className={HEADER_CELL_CLASS} style={{ color: 'var(--text-muted)', width: column.width }}>
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
              <tr><td colSpan={columns.length + (showMatchCounts ? 2 : 1)} className="px-2 py-3 text-center" style={{ color: 'var(--text-muted)' }}>No rules yet</td></tr>
            ) : rules.map((rule, i) => (
              <tr key={ruleKey(rule, i)}>
                {columns.map((column, fieldIndex) => {
                  const value = String((rule as Record<string, unknown>)[column.field] ?? "")
                  const common = {
                    value,
                    onPaste: (e: React.ClipboardEvent<HTMLInputElement | HTMLSelectElement>) => handleCellPaste(e, i, fieldIndex),
                    "aria-label": column.ariaLabel(i + 1),
                  }
                  return (
                    <td key={column.field} className={CELL_CLASS}>
                      {column.cell === "operator" ? (
                        <select {...common} onChange={(e) => updateRule(i, column.field, e.target.value)}
                          className={BOXED_SELECT_CLASS}
                          style={BOXED_CELL_STYLE}>
                          <option value="">-</option>
                          {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : column.cell === "value" ? (
                        <input type="text" {...common} onChange={(e) => updateRule(i, column.field, e.target.value)}
                          className={BOXED_INPUT_CLASS}
                          style={BOXED_CELL_STYLE} placeholder="" />
                      ) : (
                        <input type="text" {...common} onChange={(e) => updateRule(i, column.field, e.target.value)}
                          onKeyDown={(e) => handleKeyDown(e, i)}
                          className={BOXED_LABEL_INPUT_CLASS}
                          style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: accentColor }} placeholder="" />
                      )}
                    </td>
                  )
                })}
                {showMatchCounts && (
                  <td className={MATCH_CELL_CLASS}>
                    <span style={{ color: matchCounts[i] === 0 ? 'var(--danger)' : 'var(--text-muted)', opacity: matchCounts[i] === 0 ? 0.7 : 1 }}>
                      {matchCounts[i] ?? ""}
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
