/**
 * The live-sync socket's connection id, as the server named it when the socket opened
 * (`sync_connection`, specs/server-api). A save sends it so the server leaves this tab out
 * of its broadcast of the saved document; null while no socket is open.
 */
let connectionId: string | null = null

export function setSyncConnectionId(id: string | null): void {
  connectionId = id
}

export function syncConnectionId(): string | null {
  return connectionId
}
