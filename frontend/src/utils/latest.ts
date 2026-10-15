/**
 * The ticket of the newest of a kind of request (specs/frontend-shared), so an answer that
 * comes back after a newer request was made is dropped: `take()` stamps a new request,
 * putting every earlier ticket out of date; `current()` reads the newest ticket without
 * taking one, for work done under the request it belongs to (a further page of a list, a
 * save of the quote open); `is(ticket)` says whether a ticket is still the newest; and
 * `bump()` puts every ticket out of date, as leaving the scope they were taken in does.
 * What else makes an answer stale (the document, the quote, the form it was asked of)
 * stays with the caller.
 */
export interface Latest {
  take: () => number
  current: () => number
  is: (ticket: number) => boolean
  bump: () => void
}

export function latest(): Latest {
  let newest = 0
  return {
    take: () => {
      newest += 1
      return newest
    },
    current: () => newest,
    is: (ticket) => ticket === newest,
    bump: () => {
      newest += 1
    },
  }
}
