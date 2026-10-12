import { priceAppQuote } from "../api/app"
import type { FormSpec } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { canonicalJson } from "../utils/canonicalJson"

/**
 * Put the underwriter app's release (specs/workbench) in the stores Preview draws from:
 * the form read as at `revision`, Preview showing, and the quote priced through the app's
 * own route on that revision of release `release` rather than on a pipeline in an editor.
 */
export function openRelease(form: FormSpec, revision: string, release: string): void {
  useWorkbenchFormStore.setState({
    form,
    revision,
    status: "ready",
    loadError: null,
    savedForm: canonicalJson(form),
    dirty: false,
    undoStack: [],
    redoStack: [],
  })
  useWorkbenchViewStore.getState().showSection("preview")
  useWorkbenchPricingStore.getState().setQuotePricer(async (_form, quote) => {
    const { tables, request } = await priceAppQuote(revision, release, quote)
    return { tables, sample: request }
  })
}
