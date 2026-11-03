/**
 * This tab's pipeline saves still waiting for their answer, by source file.
 *
 * The save's broadcast leaves the saving tab's socket out, but a reconnect's
 * resync sent while the save is in flight is answered with the document the
 * save wrote (or a formatter rewrote on disk), and that frame can arrive before the
 * save's own answer. Live sync holds a frame for the file until the saves
 * settle and then asks for the document again with the fingerprint the saves
 * left accepted, so the save's acknowledgement, not the frame, decides what the
 * canvas holds.
 */

const pending = new Map<string, Set<Promise<unknown>>>()

/** Register a save of *sourceFile*; it is forgotten once it settles, either way. */
export function trackPendingSave(sourceFile: string, save: Promise<unknown>): void {
  let saves = pending.get(sourceFile)
  if (!saves) {
    saves = new Set()
    pending.set(sourceFile, saves)
  }
  saves.add(save)
  const forget = () => {
    saves.delete(save)
    if (saves.size === 0 && pending.get(sourceFile) === saves) pending.delete(sourceFile)
  }
  save.then(forget, forget)
}

/**
 * Resolves once every save of *sourceFile* registered so far has settled, after
 * their callers have handled the answer; `null` when none is in flight.
 */
export function pendingSavesSettled(sourceFile: string): Promise<void> | null {
  const saves = pending.get(sourceFile)
  if (!saves || saves.size === 0) return null
  return Promise.allSettled([...saves]).then(() => undefined)
}
