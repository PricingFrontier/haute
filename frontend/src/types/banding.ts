export type CategoricalRule = { value: string; assignment: string }
export type BreakpointRule = { boundary: string; label: string }
/** "breakpoints" is shown as Numeric; these are the only two banding types. */
export type BandingMode = "breakpoints" | "categorical"
export type BandingFactor = {
  banding: BandingMode
  column: string
  outputColumn: string
  rules: (CategoricalRule | BreakpointRule)[]
  default?: string | null
  rightClosed?: boolean  // for breakpoint mode: whether upper bound is inclusive (default true)
  _prevRules?: Partial<Record<BandingMode, (CategoricalRule | BreakpointRule)[]>>  // stash for type-toggle restore
}
