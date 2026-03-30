import type { BandingFactor, ContinuousRule, CategoricalRule, BreakpointRule } from "../../../types/banding"

export function normaliseBandingFactors(config: Record<string, unknown>): BandingFactor[] {
  const raw = config.factors as BandingFactor[] | undefined
  if (Array.isArray(raw) && raw.length > 0) return raw
  return [{ banding: "continuous", column: "", outputColumn: "", rules: [], default: null }]
}

export function isNumericDtype(dtype: string): boolean {
  const d = dtype.toLowerCase()
  return d.startsWith("int") || d.startsWith("uint") || d.startsWith("float") || d === "f32" || d === "f64" || d === "i8" || d === "i16" || d === "i32" || d === "i64" || d === "u8" || d === "u16" || d === "u32" || d === "u64"
}

export function inferBandingType(colName: string, colMap: Record<string, string>): string | null {
  const dtype = colMap[colName]
  if (!dtype) return null
  return isNumericDtype(dtype) ? "breakpoints" : "categorical"
}

// ---------------------------------------------------------------------------
// Rule interval parsing
// ---------------------------------------------------------------------------

export type ParsedInterval = {
  lower: number | null
  upper: number | null
  lowerInclusive: boolean
  upperInclusive: boolean
}

export function parseRuleInterval(rule: ContinuousRule): ParsedInterval | null {
  let lower: number | null = null
  let upper: number | null = null
  let lowerInclusive = false
  let upperInclusive = false

  // Process op1/val1
  const op1 = (rule.op1 ?? "").trim()
  const v1 = rule.val1 != null && rule.val1 !== "" ? Number(rule.val1) : NaN
  if (op1 && !isNaN(v1)) {
    if (op1 === ">" || op1 === ">=") {
      lower = v1
      lowerInclusive = op1 === ">="
    } else if (op1 === "<" || op1 === "<=") {
      upper = v1
      upperInclusive = op1 === "<="
    } else if (op1 === "=" || op1 === "==") {
      lower = v1
      upper = v1
      lowerInclusive = true
      upperInclusive = true
    }
  }

  // Process op2/val2
  const op2 = (rule.op2 ?? "").trim()
  const v2 = rule.val2 != null && rule.val2 !== "" ? Number(rule.val2) : NaN
  if (op2 && !isNaN(v2)) {
    if (op2 === ">" || op2 === ">=") {
      lower = v2
      lowerInclusive = op2 === ">="
    } else if (op2 === "<" || op2 === "<=") {
      upper = v2
      upperInclusive = op2 === "<="
    } else if (op2 === "=" || op2 === "==") {
      lower = v2
      upper = v2
      lowerInclusive = true
      upperInclusive = true
    }
  }

  if (lower === null && upper === null) return null
  return { lower, upper, lowerInclusive, upperInclusive }
}

// ---------------------------------------------------------------------------
// Overlap detection
// ---------------------------------------------------------------------------

function intervalsOverlap(a: ParsedInterval, b: ParsedInterval): boolean {
  // Check if a's upper < b's lower (or vice versa)
  // If either is open-ended, they can overlap
  if (a.upper !== null && b.lower !== null) {
    if (a.upper < b.lower) return false
    if (a.upper === b.lower && !(a.upperInclusive && b.lowerInclusive)) return false
  }
  if (b.upper !== null && a.lower !== null) {
    if (b.upper < a.lower) return false
    if (b.upper === a.lower && !(b.upperInclusive && a.lowerInclusive)) return false
  }
  return true
}

export function detectOverlaps(
  rules: ContinuousRule[],
): { ruleA: number; ruleB: number; desc: string }[] {
  const intervals = rules.map((r) => parseRuleInterval(r))
  const result: { ruleA: number; ruleB: number; desc: string }[] = []

  for (let i = 0; i < intervals.length; i++) {
    const a = intervals[i]
    if (!a) continue
    for (let j = i + 1; j < intervals.length; j++) {
      const b = intervals[j]
      if (!b) continue
      if (intervalsOverlap(a, b)) {
        const descA = formatInterval(a)
        const descB = formatInterval(b)
        result.push({
          ruleA: i,
          ruleB: j,
          desc: `Rule ${i + 1} ${descA} overlaps with Rule ${j + 1} ${descB}`,
        })
      }
    }
  }
  return result
}

function formatInterval(iv: ParsedInterval): string {
  const lo = iv.lower !== null ? `${iv.lowerInclusive ? "[" : "("}${iv.lower}` : "(-∞"
  const hi = iv.upper !== null ? `${iv.upper}${iv.upperInclusive ? "]" : ")"}` : "∞)"
  return `${lo}, ${hi}`
}

// ---------------------------------------------------------------------------
// Gap detection
// ---------------------------------------------------------------------------

export function detectGaps(rules: ContinuousRule[]): string[] {
  const intervals = rules
    .map((r, i) => ({ interval: parseRuleInterval(r), index: i }))
    .filter((x): x is { interval: ParsedInterval; index: number } => x.interval !== null)

  if (intervals.length < 2) return []

  // Sort by lower bound (nulls = -∞ go first)
  intervals.sort((a, b) => {
    const aLo = a.interval.lower ?? -Infinity
    const bLo = b.interval.lower ?? -Infinity
    return aLo - bLo
  })

  const gaps: string[] = []
  for (let i = 0; i < intervals.length - 1; i++) {
    const curr = intervals[i].interval
    const next = intervals[i + 1].interval

    if (curr.upper === null) continue // open-ended upper, no gap possible
    if (next.lower === null) continue // open-ended lower, no gap possible

    if (curr.upper < next.lower) {
      gaps.push(`Gap between ${curr.upper} and ${next.lower}`)
    } else if (curr.upper === next.lower && !curr.upperInclusive && !next.lowerInclusive) {
      gaps.push(`Gap at ${curr.upper} (not covered by either rule)`)
    }
  }
  return gaps
}

// ---------------------------------------------------------------------------
// Single rule validation
// ---------------------------------------------------------------------------

export function validateRule(rule: ContinuousRule): string | null {
  const iv = parseRuleInterval(rule)
  if (!iv) return null
  if (iv.lower !== null && iv.upper !== null) {
    if (iv.lower > iv.upper) {
      return `Contradictory conditions: lower bound ${iv.lower} > upper bound ${iv.upper}`
    }
    if (iv.lower === iv.upper && !(iv.lowerInclusive && iv.upperInclusive)) {
      return `Contradictory conditions: bounds equal at ${iv.lower} but not both inclusive`
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// Categorical duplicate detection
// ---------------------------------------------------------------------------

export function detectDuplicateCategorical(
  rules: CategoricalRule[],
): { indices: number[]; value: string }[] {
  const byValue = new Map<string, number[]>()
  rules.forEach((r, i) => {
    const v = r.value ?? ""
    if (!v) return
    const arr = byValue.get(v)
    if (arr) arr.push(i)
    else byValue.set(v, [i])
  })
  const result: { indices: number[]; value: string }[] = []
  for (const [value, indices] of byValue) {
    if (indices.length > 1) result.push({ indices, value })
  }
  return result
}

// ---------------------------------------------------------------------------
// Output column suggestion
// ---------------------------------------------------------------------------

export function suggestOutputColumn(inputColumn: string): string {
  if (!inputColumn) return "_band"
  if (inputColumn.endsWith("_band")) return inputColumn
  return `${inputColumn}_band`
}

// ---------------------------------------------------------------------------
// Match a value against a continuous rule
// ---------------------------------------------------------------------------

export function matchesContinuousRule(value: number, rule: ContinuousRule): boolean {
  if (!isFinite(value)) return false
  const iv = parseRuleInterval(rule)
  if (!iv) return false
  if (iv.lower !== null) {
    if (iv.lowerInclusive ? value < iv.lower : value <= iv.lower) return false
  }
  if (iv.upper !== null) {
    if (iv.upperInclusive ? value > iv.upper : value >= iv.upper) return false
  }
  return true
}

// ---------------------------------------------------------------------------
// Breakpoints → continuous rules conversion
// ---------------------------------------------------------------------------

export function breakpointsToRules(
  breakpoints: BreakpointRule[],
  rightClosed: boolean,
): ContinuousRule[] {
  if (!breakpoints.length) return []

  const bounded: { boundary: number; label: string }[] = []
  let openEndedLabel: string | null = null

  for (const bp of breakpoints) {
    const bStr = (bp.boundary ?? "").trim()
    if (!bStr) {
      openEndedLabel = bp.label ?? ""
    } else {
      const num = Number(bStr)
      if (!isNaN(num) && isFinite(num)) bounded.push({ boundary: num, label: bp.label ?? "" })
    }
  }

  bounded.sort((a, b) => a.boundary - b.boundary)

  const rules: ContinuousRule[] = []
  let prevBoundary: number | null = null

  for (const entry of bounded) {
    const b = entry.boundary
    const rule: ContinuousRule = { op1: "", val1: "", op2: "", val2: "", assignment: entry.label }

    if (prevBoundary === null) {
      if (rightClosed) {
        rule.op1 = "<="
        rule.val1 = String(b)
      } else {
        rule.op1 = "<"
        rule.val1 = String(b)
      }
    } else {
      if (rightClosed) {
        rule.op1 = ">"
        rule.val1 = String(prevBoundary)
        rule.op2 = "<="
        rule.val2 = String(b)
      } else {
        rule.op1 = ">="
        rule.val1 = String(prevBoundary)
        rule.op2 = "<"
        rule.val2 = String(b)
      }
    }

    rules.push(rule)
    prevBoundary = b
  }

  if (openEndedLabel !== null && prevBoundary !== null) {
    if (rightClosed) {
      rules.push({ op1: ">", val1: String(prevBoundary), op2: "", val2: "", assignment: openEndedLabel })
    } else {
      rules.push({ op1: ">=", val1: String(prevBoundary), op2: "", val2: "", assignment: openEndedLabel })
    }
  }

  return rules
}
