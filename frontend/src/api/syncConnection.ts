/**
 * The live-sync socket's connection id, as the server named it when the socket opened
 * (`sync_connection`, specs/server-api). A save sends it so the server leaves this tab out
 * of its broadcast of the saved document; null while no socket is open.
 */
let connectionId: string | null = null

export function setSyncConnectionId(id: string | null): void {
  connectionId = id
}

/**
 * Forget *id* if saves still send it. A socket gives up only its own id: one that
 * closes after its replacement opened must leave the replacement's.
 */
export function releaseSyncConnectionId(id: string): void {
  if (connectionId === id) connectionId = null
}

export function syncConnectionId(): string | null {
  return connectionId
}
