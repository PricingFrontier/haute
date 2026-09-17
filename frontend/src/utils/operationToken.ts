let counter = 0

/**
 * A process-unique token for one asynchronous operation, so a callback from an
 * abandoned operation can be told apart from the operation that replaced it.
 */
export function nextOperationToken(): string {
  counter += 1
  return `op-${counter}`
}
