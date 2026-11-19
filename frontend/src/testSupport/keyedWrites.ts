import { memoryHold, sendHeld, type KeyedTicket } from "../api/keyedWrites"

/** Send one keyed write through a hold of its own, as a caller holding nothing else does. */
export function sentHeld<T>(send: (ticket: KeyedTicket) => Promise<T>): Promise<T> {
  return sendHeld(memoryHold<string>((key) => key), "test-operation", (_key, ticket) => send(ticket))
}
