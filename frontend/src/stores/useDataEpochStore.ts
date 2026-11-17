/**
 * The files the pipeline reads outside the document, as an epoch (specs/workbench, specs/
 * caching): moved only through `noteProjectInputsChanged`, by every write of a data file
 * through Files, every utility file saved, created or deleted, and every import of a Data
 * Input's source that completes, so a price, a preview or an estimate made before is stale
 * (`pricingContext`, `useDerivedRequestKey`). A file changed outside Haute is not seen.
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
