/**
 * One icon per step kind, shared by the `Add step` menu and the card header
 * so a kind looks the same where it is chosen and where it is read.
 */
import {
  ArrowDownWideNarrow,
  Braces,
  Columns3,
  CopyMinus,
  Eraser,
  Layers,
  ListEnd,
  ListFilter,
  Merge,
  PaintBucket,
  PencilLine,
  Play,
  Rows3,
  Sigma,
  SquarePlus,
  TableProperties,
  Type,
  Variable,
  type LucideIcon,
} from "lucide-react"

import type { StepKind } from "./types"

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
