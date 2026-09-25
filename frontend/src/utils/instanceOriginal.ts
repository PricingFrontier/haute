/** The node whose config an input instance runs: its original, else itself. */
export function instanceOriginal<T extends { id: string; data?: unknown }>(
  graphNode: T,
  byId: Map<string, T>,
): T {
  const config = (graphNode.data as { config?: unknown } | undefined)?.config
  const reference =
    config && typeof config === "object" && !Array.isArray(config)
      ? (config as { instanceOf?: unknown }).instanceOf
      : undefined
  return (typeof reference === "string" && byId.get(reference)) || graphNode
}
