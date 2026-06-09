type ConnectionLike = {
  source: string | null | undefined
  target: string | null | undefined
  sourceHandle?: string | null
  targetHandle?: string | null
}

export function isPipelineConnectionValid(connection: ConnectionLike): boolean {
  if (!connection.source || !connection.target) return false
  return connection.source !== connection.target
}
