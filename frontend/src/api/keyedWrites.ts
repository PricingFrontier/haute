/**
 * Keyed writes (specs/frontend-shared, "API client"): a write whose body names an operation
 * key the server keeps an answer for. `sendHeld` is the one call that sends one: it holds the
 * write while its fate is unknown, refuses anything newer on that hold before it, and lets
 * it go once an answer settles it. The body itself leaves through the client's `sendKeyed`,
 * which takes only a ticket `sendHeld` gave.
 */

import { ApiError } from "./client"
import { apiErrorCode } from "./errors"

/**
 * Where one kind of keyed write is held while its fate is unknown: in memory, or in
 * browser storage so a reload sends it again. `key` is the operation key a write is
 * sent under, and `content` what that key is bound to: a write whose content changes takes
 * a new key. `held` is a write sent before that no answer settled, and only that: one
 * never sent is not held, so a write `sendHeld` finds held is being sent again.
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
 * The codes of a 503 that refused a write before anything was written: the underwriter
 * app's quote store whose tables do not fit, or none named (specs/workbench).
 */
const REFUSED_UNWRITTEN: ReadonlySet<string> = new Set(["quote_store_mismatch", "quote_store_environment"])

/**
 * The failures that answered a held write sent again: the server refuses a store that does
 * not fit before it reads what an earlier send of the key wrote, so such a refusal says
 * nothing of that send, which may have landed.
 */
const answeredResends = new WeakSet<ApiError>()

/**
 * Whether a keyed write's failure settles it: an answer the server gave, below 500, or a 503
 * that refused it before anything was written, on the write's first send. No answer, a
 * timeout, any other answer of 500 or more, or one that could not be read leaves its fate
 * unknown, so it stays held.
 */
export function keyedWriteSettled(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status <= 0) return false
  if (error.status < 500) return true
  return error.status === 503 && REFUSED_UNWRITTEN.has(apiErrorCode(error) ?? "") && !answeredResends.has(error)
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
    // Sent again, the write was held with its fate unknown (`answeredResends`).
    if (held !== null && error instanceof ApiError) answeredResends.add(error)
    if (keyedWriteSettled(error)) hold.hold(null)
    throw error
  } finally {
    liveTickets.delete(ticket)
  }
}
