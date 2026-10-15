/**
 * A turn queue (specs/frontend-shared): each piece of work given to it runs once the one
 * before has settled, failed or not, so each acts on the state as it is when its turn
 * comes. Its promise is the work's own, a failure included.
 */
export type Serial = <T>(work: () => Promise<T>) => Promise<T>

export function serial(): Serial {
  let turns: Promise<unknown> = Promise.resolve()
  return <T>(work: () => Promise<T>): Promise<T> => {
    const turn = turns.then(work)
    turns = turn.catch(() => undefined)
    return turn
  }
}
