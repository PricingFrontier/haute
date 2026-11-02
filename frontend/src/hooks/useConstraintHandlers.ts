import { useCallback } from "react"
import type { OnUpdateConfig } from "../panels/editors/_shared"
import {
  constraintBoundKind,
  constraintBoundValue,
  isPercentBound,
  startingBoundValue,
  type ConstraintBoundKind,
} from "../panels/optimiser/constraintBounds"

/**
 * Pure constraint CRUD handlers for optimiser config.
 * Operates on a constraints dict: { columnName: { boundKind: number } }.
 * Each constraint holds one bound (`min`, `max`, `min_pct` or `max_pct`),
 * matching the optimiser's sum-constraint contract.
 */
export function useConstraintHandlers(
  constraints: Record<string, Record<string, number>>,
  objective: string,
  dataInputColumns: { name: string; dtype: string }[],
  onUpdate: OnUpdateConfig,
  frontierRanges: Record<string, { min?: number; max?: number }> = {},
) {
  const handleAddConstraint = useCallback(() => {
    const usedCols = new Set(Object.keys(constraints))
    const available = dataInputColumns.find(c => !usedCols.has(c.name) && c.name !== objective)
    const colName = available ? available.name : `constraint_${Object.keys(constraints).length + 1}`
    const newConstraints = { ...constraints, [colName]: { min: 0 } }
    onUpdate("constraints", newConstraints)
  }, [constraints, dataInputColumns, objective, onUpdate])

  const handleRemoveConstraint = useCallback((name: string) => {
    const newConstraints = { ...constraints }
    delete newConstraints[name]
    if (Object.hasOwn(frontierRanges, name)) {
      const newRanges = { ...frontierRanges }
      delete newRanges[name]
      onUpdate({
        constraints: newConstraints,
        frontier_ranges: newRanges,
      })
      return
    }
    onUpdate("constraints", newConstraints)
  }, [constraints, frontierRanges, onUpdate])

  const handleConstraintColumnChange = useCallback((oldName: string, newName: string) => {
    if (oldName === newName) return
    const newConstraints: Record<string, Record<string, number>> = {}
    for (const [k, v] of Object.entries(constraints)) {
      newConstraints[k === oldName ? newName : k] = v
    }
    if (Object.hasOwn(frontierRanges, oldName)) {
      const newRanges: Record<string, { min?: number; max?: number }> = {}
      for (const [key, range] of Object.entries(frontierRanges)) {
        newRanges[key === oldName ? newName : key] = range
      }
      onUpdate({
        constraints: newConstraints,
        frontier_ranges: newRanges,
      })
      return
    }
    onUpdate("constraints", newConstraints)
  }, [constraints, frontierRanges, onUpdate])

  const handleConstraintValueChange = useCallback((name: string, kind: ConstraintBoundKind, value: number) => {
    onUpdate("constraints", { ...constraints, [name]: { [kind]: value } })
  }, [constraints, onUpdate])

  // Between at least and at most the value stays. Between an absolute and a
  // percentage bound it means something else, so the bound starts afresh, and
  // a swept constraint's range, in the old unit, restarts from it too.
  const handleConstraintKindChange = useCallback((name: string, kind: ConstraintBoundKind) => {
    const spec = constraints[name] ?? {}
    const currentKind = constraintBoundKind(spec)
    const sameUnit = currentKind !== null && isPercentBound(currentKind) === isPercentBound(kind)
    const kept = sameUnit ? constraintBoundValue(spec, currentKind) : undefined
    const value = kept ?? startingBoundValue(kind)
    const nextConstraints = { ...constraints, [name]: { [kind]: value } }
    if (!sameUnit && Object.hasOwn(frontierRanges, name)) {
      onUpdate({
        constraints: nextConstraints,
        frontier_ranges: { ...frontierRanges, [name]: { min: value } },
      })
      return
    }
    onUpdate("constraints", nextConstraints)
  }, [constraints, frontierRanges, onUpdate])

  return {
    handleAddConstraint,
    handleRemoveConstraint,
    handleConstraintColumnChange,
    handleConstraintValueChange,
    handleConstraintKindChange,
  }
}
