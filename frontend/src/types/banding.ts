export type ContinuousRule = { op1: string; val1: string; op2: string; val2: string; assignment: string }
export type CategoricalRule = { value: string; assignment: string }
export type BreakpointRule = { boundary: string; label: string }
export type BandingMode = "continuous" | "categorical" | "breakpoints"
export type BandingFactor = {
  banding: BandingMode
  column: string
  outputColumn: string
  rules: (ContinuousRule | CategoricalRule | BreakpointRule)[]
  default?: string | null
  rightClosed?: boolean  // for breakpoint mode: whether upper bound is inclusive (default true)
  _prevRules?: Record<string, (ContinuousRule | CategoricalRule | BreakpointRule)[]>  // stash for type-toggle restore
}
