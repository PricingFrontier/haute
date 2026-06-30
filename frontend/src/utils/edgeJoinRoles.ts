// Keep these role handles/config keys in sync with haute._edge_join.
export const EDGE_JOIN_BASE_HANDLE = "base"
export const EDGE_JOIN_JOIN_HANDLE = "join"
export const EDGE_JOIN_JOIN_BOTTOM_HANDLE = "join-bottom"
export const EDGE_JOIN_BASE_CONFIG_KEY = "baseInput"
export const EDGE_JOIN_JOIN_CONFIG_KEY = "joinInput"

export type EdgeJoinHandle =
  | typeof EDGE_JOIN_BASE_HANDLE
  | typeof EDGE_JOIN_JOIN_HANDLE

export type EdgeJoinConfigKey =
  | typeof EDGE_JOIN_BASE_CONFIG_KEY
  | typeof EDGE_JOIN_JOIN_CONFIG_KEY

export function edgeJoinRoleConfigKey(
  targetHandle: string | null | undefined,
): EdgeJoinConfigKey | null {
  const canonicalHandle = edgeJoinCanonicalTargetHandle(targetHandle)
  if (canonicalHandle === EDGE_JOIN_BASE_HANDLE) return EDGE_JOIN_BASE_CONFIG_KEY
  if (canonicalHandle === EDGE_JOIN_JOIN_HANDLE) return EDGE_JOIN_JOIN_CONFIG_KEY
  return null
}

export function edgeJoinCanonicalTargetHandle(
  targetHandle: string | null | undefined,
): EdgeJoinHandle | null {
  if (targetHandle === EDGE_JOIN_BASE_HANDLE) return EDGE_JOIN_BASE_HANDLE
  if (targetHandle === EDGE_JOIN_JOIN_HANDLE || targetHandle === EDGE_JOIN_JOIN_BOTTOM_HANDLE) {
    return EDGE_JOIN_JOIN_HANDLE
  }
  return null
}
