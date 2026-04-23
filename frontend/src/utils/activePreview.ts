import type { PreviewData } from "../panels/DataPreview"

export function previewForActiveNode(
  previewData: PreviewData | null,
  activeNodeId: string | null,
): PreviewData | null {
  if (!previewData || !activeNodeId) return null
  return previewData.nodeId === activeNodeId ? previewData : null
}
