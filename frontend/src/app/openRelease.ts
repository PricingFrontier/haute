import { priceAppQuote } from "../api/app"
import type { FormSpec } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { canonicalJson } from "../utils/canonicalJson"
import { columnKey } from "../utils/sheetVisibility"
import { QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED } from "../utils/workbenchForm"

/** The quote_details columns the app sets itself, which take no typing on its sheets. */
export const APP_SET_COLUMNS: ReadonlySet<string> = new Set([...QUOTE_DETAILS_MANAGED].map((id) => columnKey(QUOTE_DETAILS_ID, id)))

/**
 * Put the underwriter app's release (specs/workbench) in the stores Preview draws from:
 * the form read as at `revision`, Preview showing, the columns the app sets locked, and the
 * quote priced through the app's own route on that revision of release `release` rather
 * than on a pipeline in an editor.
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
  useWorkbenchPreviewStore.getState().setLocked(APP_SET_COLUMNS)
  useWorkbenchPricingStore.getState().setQuotePricer(async (_form, quote) => {
    const { tables, request } = await priceAppQuote(revision, release, quote)
    return { tables, sample: request }
  })
}
