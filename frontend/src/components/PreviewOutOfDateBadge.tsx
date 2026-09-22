import { Clock } from "lucide-react"

import type { PreviewData } from "../panels/DataPreview"
import useGraphStore from "../stores/useGraphStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useUIStore from "../stores/useUIStore"

/**
 * Marks a preview shown under manual calculation whose pipeline or input data
 * has changed since it was calculated. Its own component so the graph and
 * node-data subscriptions re-render only this badge, not the preview table.
 */
export default function PreviewOutOfDateBadge({ data }: { data: PreviewData }) {
  const manual = useUIStore((s) => s.calculationMode === "manual")
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  const nodeDataEpoch = useNodeDataStore((s) => s.epoch)
  const stored = useNodeResultsStore((s) => s.previews[data.nodeId])
  if (!manual || data.status !== "ok" || !stored || stored.data !== data) return null
  if (stored.structuralVersion === structuralVersion && stored.nodeDataEpoch === nodeDataEpoch) return null
  return (
    <span
      data-testid="preview-out-of-date"
      className="flex items-center gap-1 text-[11px]"
      style={{ color: "var(--warning-strong)" }}
      title="The pipeline or its data changed since this preview was calculated. Press Refresh to recalculate."
    >
      <Clock size={11} aria-hidden="true" />
      Out of date
    </span>
  )
}
