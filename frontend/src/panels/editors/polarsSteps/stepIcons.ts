/**
 * One icon per step kind, and per calculation an Add column step is computed
 * as, shared by the `Add step` chooser and the card header so a step looks the
 * same where it is chosen and where it is read.
 */
import {
  ArrowDownWideNarrow,
  Braces,
  Calculator,
  CalendarDays,
  CaseSensitive,
  Columns3,
  CopyMinus,
  Divide,
  Equal,
  Eraser,
  FoldVertical,
  Hash,
  Layers,
  Link2,
  ListEnd,
  ListFilter,
  Merge,
  PaintBucket,
  PencilLine,
  Play,
  Plus,
  Rows3,
  Sigma,
  Split,
  SquarePlus,
  SquareSigma,
  TableProperties,
  Type,
  Variable,
  type LucideIcon,
} from "lucide-react"

import type { Calculation, CalculationId, CalculationSection } from "./calculations"
import type { StepKind } from "./types"

/** The calculations with an icon of their own; the rest take their section's. */
const CALCULATION_ICONS: Partial<Record<CalculationId, LucideIcon>> = {
  formula: Calculator,
  if_then: Split,
  window: SquareSigma,
  sum_across: Plus,
  join_text: Link2,
  value: Equal,
  clip: FoldVertical,
  divide: Divide,
}

const SECTION_ICONS: Record<CalculationSection, LucideIcon> = {
  General: SquarePlus,
  Number: Hash,
  Text: CaseSensitive,
  Dates: CalendarDays,
}

export function calculationIcon(calculation: Calculation): LucideIcon {
  return CALCULATION_ICONS[calculation.id] ?? SECTION_ICONS[calculation.section]
}

export const STEP_ICONS: Record<StepKind, LucideIcon> = {
  source: Play,
  filter: ListFilter,
  with_column: SquarePlus,
  select: Columns3,
  drop: Eraser,
  rename: PencilLine,
  cast: Type,
  sort: ArrowDownWideNarrow,
  unique: CopyMinus,
  group_by: Sigma,
  join: Merge,
  concat: Rows3,
  fill_null: PaintBucket,
  limit: ListEnd,
  variable: Variable,
  pivot: TableProperties,
  unpivot: Layers,
  free_code: Braces,
}
