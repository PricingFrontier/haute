import { useMemo } from "react"
import { AlertCircle, MousePointer2, X } from "lucide-react"

import { contextSelection } from "../../stores/useAssistantStore"
import useGraphStore from "../../stores/useGraphStore"
import useUIStore from "../../stores/useUIStore"

/** Selected labels the chip names before summarising the rest as a count. */
const NAMED_LABELS = 3

function labelOf(node: { id: string; data: Record<string, unknown> }): string {
  return typeof node.data.label === "string" && node.data.label ? node.data.label : node.id
}

/**
 * What the next message carries besides its text: the selected canvas nodes,
 * and the node whose run error the analyst asked the assistant to fix.
 */
export default function ContextChips() {
  // Selected as one string: dragging a node rebuilds the node array but leaves
  // this unchanged, so the chips do not re-render on every move. (zustand's
  // useShallow would do the same from the startup vendor chunk.)
  const selectionKey = useGraphStore((state) => JSON.stringify(contextSelection(state.nodes).map(labelOf)))
  const selectedLabels = useMemo(() => JSON.parse(selectionKey) as string[], [selectionKey])
  const errorNodeId = useUIStore((state) => state.assistantPreviewErrorNodeId)
  const errorLabel = useGraphStore((state) => {
    if (errorNodeId === null) return null
    const node = state.nodes.find((candidate) => candidate.id === errorNodeId)
    return node ? labelOf(node) : errorNodeId
  })
  const clearError = useUIStore((state) => state.clearAssistantPreviewError)

  if (selectedLabels.length === 0 && errorLabel === null) return null
  const named = selectedLabels.slice(0, NAMED_LABELS).join(", ")
  const more = selectedLabels.length - NAMED_LABELS
  return (
    <div className="flex flex-wrap gap-1.5">
      {selectedLabels.length > 0 && (
        <span
          data-testid="assistant-context-selection"
          className="inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[10px]"
          style={{ background: "var(--accent-soft)", color: "var(--text-primary)" }}
          title="The selected nodes are sent with your message, so the assistant knows which you mean"
        >
          <MousePointer2 size={10} aria-hidden="true" className="shrink-0" />
          <span className="truncate">
            Selected: {named}
            {more > 0 && ` +${more} more`}
          </span>
        </span>
      )}
      {errorLabel !== null && (
        <span
          data-testid="assistant-context-error"
          className="inline-flex max-w-full items-center gap-1 rounded-full py-0.5 pl-2 pr-1 text-[10px]"
          style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
          title="The assistant reproduces this node's run error when you send"
        >
          <AlertCircle size={10} aria-hidden="true" className="shrink-0" />
          <span className="truncate">Run error: {errorLabel}</span>
          <button
            type="button"
            data-testid="assistant-context-error-dismiss"
            onClick={clearError}
            aria-label="Do not send this run error"
            className="rounded-full p-0.5 hover:bg-[var(--bg-hover)]"
          >
            <X size={10} aria-hidden="true" />
          </button>
        </span>
      )}
    </div>
  )
}
