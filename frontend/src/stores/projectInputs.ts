/**
 * The one way the browser is told that a file a run reads has changed outside the
 * document: a utility module saved, created or deleted, a data or params file written or
 * removed through Files, a Data Input's source imported again (specs/caching, "Stale
 * results"). Every answer the browser keeps goes old together: the data epoch moves (a
 * Workbench price, the key of every derived request, `useDerivedRequestKey`) and the
 * structural version advances (every node preview). A writer calls this, never one clock
 * of its own, so no reader is left on a list that misses it.
 */
import useDataEpochStore from "./useDataEpochStore"
import useGraphStore from "./useGraphStore"

export function noteProjectInputsChanged(): void {
  useDataEpochStore.getState().moved()
  useGraphStore.getState().noteStructureOutsideGraph()
}
