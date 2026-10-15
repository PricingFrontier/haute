/**
 * The data the pipeline reads, as an epoch (specs/workbench): moved by every write of a
 * data file through Files and by every import of a Data Input's source that completes, so
 * a price made on the data before is stale (`pricingContext`). A file changed outside Haute
 * is not seen.
 */
import { create } from "zustand"

interface DataEpochState {
  epoch: number
  /** The data has changed: what was priced on it before is stale. */
  moved: () => void
}

const useDataEpochStore = create<DataEpochState>()((set) => ({
  epoch: 0,
  moved: () => set((state) => ({ epoch: state.epoch + 1 })),
}))

export default useDataEpochStore
