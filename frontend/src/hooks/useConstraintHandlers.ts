import { useCallback } from "react"
import type { OnUpdateConfig } from "../panels/editors/_shared"

/**
 * Pure constraint CRUD handlers for optimiser config.
 * Operates on a constraints dict: { columnName: { constraintType: number } }.
 * The UI supports one active absolute threshold per row, matching the
 * optimiser's sum-constraint contract.
 */
export function useConstraintHandlers(
  constraints: Record<string, Record<string, number>>,
  objective: string,
  dataInputColumns: { name: string; dtype: string }[],
  onUpdate: OnUpdateConfig,
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
    onUpdate("constraints", newConstraints)
  }, [constraints, onUpdate])

  const handleConstraintColumnChange = useCallback((oldName: string, newName: string) => {
    if (oldName === newName) return
    const newConstraints: Record<string, Record<string, number>> = {}
    for (const [k, v] of Object.entries(constraints)) {
      newConstraints[k === oldName ? newName : k] = v
    }
    onUpdate("constraints", newConstraints)
  }, [constraints, onUpdate])

  const handleConstraintValueChange = useCallback((name: string, type: string, value: number) => {
    onUpdate("constraints", { ...constraints, [name]: { [type]: value } })
  }, [constraints, onUpdate])

  return {
    handleAddConstraint,
    handleRemoveConstraint,
    handleConstraintColumnChange,
    handleConstraintValueChange,
  }
}
