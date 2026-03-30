import { useRef, useEffect, useMemo, useCallback } from "react"
import { Trash } from "lucide-react"
import type { BandingFactor, ContinuousRule, CategoricalRule } from "../../../types/banding"

const OPS = ["<", "<=", ">", ">=", "="]

/** Generate a short unique key for a rule row. */
let _ruleIdSeq = 0
// eslint-disable-next-line react-refresh/only-export-components
export function nextRuleId(): string {
  return `rule_${++_ruleIdSeq}_${Date.now().toString(36)}`
}

/** Ensure every rule has a stable `_id` key. */
function ensureRuleIds(rules: (ContinuousRule | CategoricalRule)[]): (ContinuousRule | CategoricalRule)[] {
  let changed = false
  const result = rules.map((r) => {
    if ((r as Record<string, unknown>)._id) return r
    changed = true
    return { ...r, _id: nextRuleId() }
  })
  return changed ? result : rules
}

/** Extract the stable key from a rule (falls back to index). */
function ruleKey(rule: ContinuousRule | CategoricalRule, index: number): string {
  return (rule as Record<string, unknown>)._id as string || `fallback_${index}`
}

/** Parse pasted TSV text into rules. */
function parsePastedRules(
  text: string,
  mode: "continuous" | "categorical",
): (ContinuousRule | CategoricalRule)[] {
  const lines = text.split("\n").map(l => l.trim()).filter(l => l.length > 0)
  const parsed: (ContinuousRule | CategoricalRule)[] = []

  for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
    const cols = lines[lineIdx].split("\t")

    if (mode === "categorical") {
      if (cols.length >= 2) {
        parsed.push({ value: cols[0], assignment: cols[1] })
      }
    } else {
      if (cols.length === 2) {
        // val1, assignment — auto-set op1
        parsed.push({
          op1: lineIdx === 0 ? ">=" : ">",
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

const DELETE_BUTTON_CLASS = "p-0.5 rounded transition-colors text-[var(--text-muted)] hover:text-[#ef4444] focus-visible:text-[#ef4444]"

export function BandingRulesGrid({
  factor,
  onUpdateFactor,
  accentColor = '#22d3ee',
  matchCounts,
  onAddRule,
}: {
  factor: BandingFactor
  onUpdateFactor: (patch: Partial<BandingFactor>) => void
  accentColor?: string
  matchCounts?: number[]
  onAddRule?: () => void
}) {
  const rawRules = useMemo(() => factor.rules || [], [factor.rules])

  // Ensure rules have stable _id keys (assign on first render, persist via onUpdateFactor)
  const didAssignIds = useRef(false)
  const prevRulesRef = useRef(rawRules)
  useEffect(() => {
    if (prevRulesRef.current !== rawRules) {
      didAssignIds.current = false
      prevRulesRef.current = rawRules
    }
  }, [rawRules])
  const rules = ensureRuleIds(rawRules)
  useEffect(() => {
    if (rules !== rawRules && !didAssignIds.current) {
      didAssignIds.current = true
      // Persist the assigned ids back so subsequent renders have them
      onUpdateFactor({ rules })
    }
  }) // intentionally no deps — runs on every render to catch first assignment

  const bt = factor.banding || "continuous"

  const setRules = (r: (ContinuousRule | CategoricalRule)[]) => onUpdateFactor({ rules: r })
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

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>, ruleIndex: number) => {
    if (e.key === "Enter" && ruleIndex === rules.length - 1 && onAddRule) {
      onAddRule()
    }
  }, [rules.length, onAddRule])

  const showMatchCounts = !!matchCounts

  const continuousCols = showMatchCounts ? 7 : 6
  const categoricalCols = showMatchCounts ? 4 : 3

  return (
    <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--bg-input)' }}>
      <div className="max-h-[300px] overflow-y-auto" data-testid="banding-scroll-container" onPaste={handlePaste}>
        {bt === "continuous" ? (
          <table className="w-full text-[11px]">
            <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 52 }}>From</th>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 60 }}>Value</th>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 52 }}>
                  To <span style={{ color: 'var(--text-muted)', opacity: 0.55, fontWeight: 'normal' }}>(opt.)</span>
                </th>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 60 }}>Value</th>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)' }}>Label</th>
                {showMatchCounts && (
                  <th className="text-right px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 50 }}>Matches</th>
                )}
                <th style={{ width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {rules.length === 0 ? (
                <tr><td colSpan={continuousCols} className="px-2 py-3 text-center" style={{ color: 'var(--text-muted)' }}>No rules yet</td></tr>
              ) : (rules as ContinuousRule[]).map((rule, i) => (
                <tr key={ruleKey(rule, i)} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td className="px-1 py-1.5">
                    <select value={rule.op1 || ""} onChange={(e) => updateRule(i, "op1", e.target.value)}
                      aria-label={`Rule ${i + 1} lower operator`}
                      className="w-full px-1 py-1 rounded text-[11px] font-mono appearance-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
                      <option value="">—</option>
                      {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </td>
                  <td className="px-1 py-1.5">
                    <input type="text" value={rule.val1 ?? ""} onChange={(e) => updateRule(i, "val1", e.target.value)}
                      aria-label={`Rule ${i + 1} lower value`}
                      className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} placeholder="" />
                  </td>
                  <td className="px-1 py-1.5">
                    <select value={rule.op2 || ""} onChange={(e) => updateRule(i, "op2", e.target.value)}
                      aria-label={`Rule ${i + 1} upper operator`}
                      className="w-full px-1 py-1 rounded text-[11px] font-mono appearance-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
                      <option value="">—</option>
                      {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </td>
                  <td className="px-1 py-1.5">
                    <input type="text" value={rule.val2 ?? ""} onChange={(e) => updateRule(i, "val2", e.target.value)}
                      aria-label={`Rule ${i + 1} upper value`}
                      className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} placeholder="" />
                  </td>
                  <td className="px-1 py-1.5">
                    <input type="text" value={rule.assignment ?? ""} onChange={(e) => updateRule(i, "assignment", e.target.value)}
                      aria-label={`Rule ${i + 1} label`}
                      onKeyDown={(e) => handleKeyDown(e, i)}
                      className="w-full px-1.5 py-1 rounded text-[11px] font-mono font-semibold focus:outline-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: accentColor }} placeholder="" />
                  </td>
                  {showMatchCounts && (
                    <td className="px-1 py-1.5 text-right text-[10px]">
                      <span style={{ color: matchCounts[i] === 0 ? '#ef4444b3' : 'var(--text-muted)' }}>
                        {matchCounts[i] ?? ""}
                      </span>
                    </td>
                  )}
                  <td className="px-1 py-1.5 text-center">
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
        ) : (
          <table className="w-full text-[11px]">
            <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)' }}>Value</th>
                <th className="text-left px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)' }}>Maps To</th>
                {showMatchCounts && (
                  <th className="text-right px-2 py-1.5 font-semibold" style={{ color: 'var(--text-muted)', width: 50 }}>Matches</th>
                )}
                <th style={{ width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {rules.length === 0 ? (
                <tr><td colSpan={categoricalCols} className="px-2 py-3 text-center" style={{ color: 'var(--text-muted)' }}>No rules yet</td></tr>
              ) : (rules as CategoricalRule[]).map((rule, i) => (
                <tr key={ruleKey(rule, i)} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td className="px-1 py-1.5">
                    <input type="text" value={rule.value ?? ""} onChange={(e) => updateRule(i, "value", e.target.value)}
                      aria-label={`Rule ${i + 1} match value`}
                      className="w-full px-1.5 py-1 rounded text-[11px] font-mono focus:outline-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} placeholder="" />
                  </td>
                  <td className="px-1 py-1.5">
                    <input type="text" value={rule.assignment ?? ""} onChange={(e) => updateRule(i, "assignment", e.target.value)}
                      aria-label={`Rule ${i + 1} group name`}
                      onKeyDown={(e) => handleKeyDown(e, i)}
                      className="w-full px-1.5 py-1 rounded text-[11px] font-mono font-semibold focus:outline-none"
                      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: accentColor }} placeholder="" />
                  </td>
                  {showMatchCounts && (
                    <td className="px-1 py-1.5 text-right text-[10px]">
                      <span style={{ color: matchCounts[i] === 0 ? '#ef4444b3' : 'var(--text-muted)' }}>
                        {matchCounts[i] ?? ""}
                      </span>
                    </td>
                  )}
                  <td className="px-1 py-1.5 text-center">
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
        )}
      </div>
    </div>
  )
}
