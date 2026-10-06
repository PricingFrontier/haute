import { isNumericDtype } from "../../utils/polarsDtypes"

export type ModelColumn = { name: string; dtype: string }

/**
 * The columns each role may take. One column plays one role: the target is
 * never the weight or offset, and the weight and offset are numeric.
 */
export function modelColumnChoices(
  columns: ModelColumn[],
  { target, weight, offset }: { target: string; weight: string; offset: string },
): { targetColumns: ModelColumn[]; weightColumns: ModelColumn[]; offsetColumns: ModelColumn[] } {
  return {
    targetColumns: columns.filter((column) => column.name !== weight && column.name !== offset),
    weightColumns: columns.filter(
      (column) => isNumericDtype(column.dtype) && column.name !== target && column.name !== offset,
    ),
    offsetColumns: columns.filter(
      (column) => isNumericDtype(column.dtype) && column.name !== target && column.name !== weight,
    ),
  }
}
