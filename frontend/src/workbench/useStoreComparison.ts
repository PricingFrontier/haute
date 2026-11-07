/**
 * The schema pane's Compare at work (specs/workbench): the form the view holds compared with
 * what was read of the chosen environment's database, as either changes or a pairing does,
 * while the pane shows. Session invalidation belongs to the store.
 */
import { useEffect } from "react"
import useStoreCompareStore from "../stores/useStoreCompareStore"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"

export default function useStoreComparison() {
  const form = useWorkbenchFormStore((s) => s.form)
  const environments = useStoreCompareStore((s) => s.environments)
  const renames = useStoreCompareStore((s) => s.renames)
  const compare = useStoreCompareStore((s) => s.compare)
  useEffect(() => {
    if (form !== null) compare(form)
  }, [form, environments, renames, compare])
}
