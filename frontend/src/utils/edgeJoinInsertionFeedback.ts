import type { Edge } from "@xyflow/react"

export const EDGE_JOIN_INSERTION_CANDIDATE_CLASS = "edge-join-insertion-candidate"
export const EDGE_JOIN_INSERTION_STATUS = "Release to insert an Edge Join on this connection"

export function withEdgeJoinInsertionCandidate(
  edges: Edge[],
  candidateEdgeId: string | null,
): Edge[] {
  if (!candidateEdgeId) return edges
  const candidate = edges.find((edge) => edge.id === candidateEdgeId)
  if (!candidate) return edges
  return edges.map((edge) => (
    edge.id !== candidateEdgeId
      ? edge
      : {
          ...edge,
          className: [edge.className, EDGE_JOIN_INSERTION_CANDIDATE_CLASS]
            .filter(Boolean)
            .join(" "),
          ariaLabel: EDGE_JOIN_INSERTION_STATUS,
        }
  ))
}
