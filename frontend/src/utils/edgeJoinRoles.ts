// Edge roles are structural: incoming target handles are the sole authority.
export const EDGE_JOIN_BASE_HANDLE = "base"
export const EDGE_JOIN_JOIN_HANDLE = "join"
export const EDGE_JOIN_JOIN_BOTTOM_HANDLE = "join-bottom"

export type EdgeJoinHandle =
  | typeof EDGE_JOIN_BASE_HANDLE
  | typeof EDGE_JOIN_JOIN_HANDLE

export function edgeJoinCanonicalTargetHandle(
  targetHandle: string | null | undefined,
): EdgeJoinHandle | null {
  if (targetHandle === EDGE_JOIN_BASE_HANDLE) return EDGE_JOIN_BASE_HANDLE
  if (targetHandle === EDGE_JOIN_JOIN_HANDLE || targetHandle === EDGE_JOIN_JOIN_BOTTOM_HANDLE) {
    return EDGE_JOIN_JOIN_HANDLE
  }
  return null
}
