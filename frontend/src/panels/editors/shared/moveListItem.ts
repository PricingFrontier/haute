/**
 * The move a {@link SearchableItemList} row makes when it is dragged onto
 * another, or moved with Alt+Up/Down: the item at `from` is taken out and put
 * back at `to`. Banding's factors and Rating Step's tables both move this way.
 */
export function moveListItem<T>(items: readonly T[], from: number, to: number): T[] {
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

/**
 * Where the item at `current` sits once the item at `from` has moved to `to`,
 * so the editor keeps the same item selected: the moved one goes with it, and
 * the ones it passed shift by one.
 */
export function indexAfterMove(current: number, from: number, to: number): number {
  if (current === from) return to
  if (from < current && current <= to) return current - 1
  if (to <= current && current < from) return current + 1
  return current
}

/** Whether a move from `from` to `to` changes a list of `length` items. */
export function isListMove(length: number, from: number, to: number): boolean {
  return from !== to && from >= 0 && from < length && to >= 0 && to < length
}
