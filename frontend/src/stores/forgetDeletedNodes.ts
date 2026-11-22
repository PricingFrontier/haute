import { retireInputImport } from "./useInputImportStore"
import useNodeResultsStore from "./useNodeResultsStore"
import useOutputWriteStore from "./useOutputWriteStore"
import { isModelKeyOf } from "../modelling/interactiveModels"

/**
 * Forget what is kept under the keys of nodes deleted from the canvas
 * (`resultKeysOf`, taken while they were still there), once they have left the
 * graph: their results and caches, a Data Output's last write, and a running
 * import, which leaves its node at once and is stopped in the background. A
 * write already sent still finishes, since stopping it part-way could leave a
 * half-written file. A remembered training job is kept, so Undo of the deletion
 * brings the trained model back.
 */
export function forgetDeletedNodes(keys: readonly string[]): void {
  const { clearNode, trainResults, trainJobs } = useNodeResultsStore.getState()
  const { forget } = useOutputWriteStore.getState()
  // An interactive build's models keep their runs under keys of their own.
  const modelKeys = [...Object.keys(trainResults), ...Object.keys(trainJobs)]
  for (const key of keys) {
    clearNode(key)
    forget(key)
    retireInputImport(key)
    for (const modelKey of new Set(modelKeys.filter((candidate) => isModelKeyOf(candidate, key)))) {
      clearNode(modelKey)
    }
  }
}
