import { EDGE_JOIN_INSERTION_STATUS } from "../utils/edgeJoinInsertionFeedback"

export default function EdgeJoinInsertionFeedback({
  candidateEdgeId,
}: {
  candidateEdgeId: string | null
}) {
  if (!candidateEdgeId) return null
  return (
    <span
      className="sr-only"
      role="status"
      aria-label={EDGE_JOIN_INSERTION_STATUS}
      aria-live="polite"
    >
      {EDGE_JOIN_INSERTION_STATUS}
    </span>
  )
}
