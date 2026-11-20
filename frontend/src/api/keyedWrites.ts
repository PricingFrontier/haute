/**
 * Keyed writes (specs/frontend-shared, "API client"): a write whose body names an operation
 * key the server keeps an answer for. `sendHeld` is the one call that sends one: it holds the
 * write while its fate is unknown, refuses anything newer on that hold before it, and lets
 * it go once an answer settles it. The body itself leaves through the client's `sendKeyed`,
 * which takes only a ticket `sendHeld` gave.
 */

import { ApiError } from "./client"

/**
 * Where one kind of keyed write is held while its fate is unknown: in memory, or in
 * browser storage so a reload sends it again. `key` is the operation key a write is
 * sent under, and `content` what that key is bound to: a write whose content changes takes
 * a new key.
 */
export interface KeyedHold<W> {
  held(): W | null
  hold(write: W | null): void
  key(write: W): string
  content(write: W): string
}

/** A hold in this page's memory, for writes a reload need not send again. */
export function memoryHold<W>(key: (write: W) => string, content: (write: W) => string): KeyedHold<W> {
  let held: W | null = null
  return {
    held: () => held,
    hold: (write) => {
      held = write
    },
    key,
    content,
  }
}

/** `body` as the content an operation key is bound to: everything but `field`, the key. */
export function contentWithout(body: object, field: string): string {
  const { [field]: _key, ...content } = body as Record<string, unknown>
  return JSON.stringify(content)
}

/** Proof that a keyed write is being sent by `sendHeld`: what `sendKeyed` asks for. */
export interface KeyedTicket {
  readonly sending: true
}

const liveTickets = new WeakSet<KeyedTicket>()

/** Whether `ticket` is one `sendHeld` gave a send still under way: what `sendKeyed` asks. */
export function ticketIsLive(ticket: KeyedTicket): boolean {
  return liveTickets.has(ticket)
}

/**
 * Whether a keyed write's failure settles it: an answer the server gave, below 500. No
 * answer, a timeout, an answer of 500 or more, or one that could not be read leaves its
 * fate unknown, so it stays held.
 */
export function keyedWriteSettled(error: unknown): boolean {
  return error instanceof ApiError && error.status > 0 && error.status < 500
}

/**
 * Send `write`, a keyed write, through `send`, holding it in `hold` from the moment it is
 * sent until an answer settles it (`keyedWriteSettled`); its answer is returned and its
 * failure thrown. While a write is held, only that write goes, exactly: one under another
 * key, or other content under its key, is refused before it is sent. `send` sends the body
 * through `sendKeyed` with the ticket it is given.
 */
export async function sendHeld<W, T>(
  hold: KeyedHold<W>,
  write: W,
  send: (write: W, ticket: KeyedTicket) => Promise<T>,
): Promise<T> {
  const held = hold.held()
  if (held !== null && hold.key(held) !== hold.key(write)) {
    throw new Error("Another keyed write is held: it is sent again before anything newer.")
  }
  if (held !== null && hold.content(held) !== hold.content(write)) {
    throw new Error("A held keyed write is sent again exactly: other content takes a new key.")
  }
  const ticket: KeyedTicket = { sending: true }
  liveTickets.add(ticket)
  hold.hold(write)
  try {
    const answer = await send(write, ticket)
    hold.hold(null)
    return answer
  } catch (error: unknown) {
    if (keyedWriteSettled(error)) hold.hold(null)
    throw error
  } finally {
    liveTickets.delete(ticket)
  }
}
