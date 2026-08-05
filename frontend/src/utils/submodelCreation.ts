import type { Node } from "@xyflow/react"

interface SubmodelCreationRequest {
  nodes: readonly Node[]
  readOnly: boolean
  isInsideSubmodel: boolean
  setSubmodelDialog: (dialog: { nodeIds: string[] }) => void
  addToast: (type: "info", text: string) => void
}

/**
 * Apply the one Submodel-creation policy shared by Ctrl+G and the toolbar.
 * Callers supply their current node snapshot; this function owns both the
 * eligibility rules and the user-facing refusal messages.
 */
export function requestSubmodelCreation({
  nodes,
  readOnly,
  isInsideSubmodel,
  setSubmodelDialog,
  addToast,
}: SubmodelCreationRequest): void {
  if (readOnly) {
    addToast("info", "This submodel instance is read-only")
    return
  }
  if (isInsideSubmodel) {
    addToast("info", "Submodels cannot be nested inside other submodels")
    return
  }
  const selectedNodeIds = nodes.filter((node) => node.selected).map((node) => node.id)
  if (selectedNodeIds.length < 2) {
    addToast("info", "Select at least 2 nodes to create a submodel (Ctrl+G)")
    return
  }
  setSubmodelDialog({ nodeIds: selectedNodeIds })
}
