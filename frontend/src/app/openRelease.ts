import { importAppRows, priceAppQuote } from "../api/app"
import type { FormSpec } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { answer, watch } from "../utils/answers"
import { newQuoteValues } from "../utils/sheetValues"
import { columnKey } from "../utils/sheetVisibility"
import { QUOTE_DETAILS, QUOTE_DETAILS_NAME, QUOTE_DETAILS_MANAGED, type SampleRow } from "../utils/workbenchForm"
import { appErrorMessage, releaseMoved, storeMisfits } from "./quoteValues"
import useAppStore from "./useAppStore"

/**
 * The quote_details columns the app sets itself, which take no typing on its sheets, and the
 * quote's name, given in the New quote window as the quote is made and never changed after.
 */
export const APP_SET_COLUMNS: ReadonlySet<string> = new Set(
  [...QUOTE_DETAILS_MANAGED, QUOTE_DETAILS.quote_name].map((id) => columnKey(QUOTE_DETAILS_NAME, id)),
)

/**
 * Put the underwriter app's release (specs/workbench) in the stores Preview draws from:
 * the form read as at `revision`, Preview showing the app's quote rather than a sample, the
 * columns the app sets locked, the quote priced through the app's own route on that
 * revision of release `release` rather than on a pipeline in an editor, its Imports run
 * through the app's route on that revision too, a pricing the quote store's tables refuse
 * said in the bar naming each table and column that does not fit, and a new quote, holding
 * its columns' defaults, until a quote opens.
 */
export function openRelease(form: FormSpec, revision: string, release: string): void {
  useWorkbenchFormStore.getState().install(form, revision)
  useWorkbenchViewStore.setState({ underwriterApp: true })
  useWorkbenchViewStore.getState().showMode("preview")
  useWorkbenchPreviewStore.getState().setLocked(APP_SET_COLUMNS)
  useWorkbenchPricingStore.getState().setQuotePricer((_form, quote) => {
    const priced = priceAppQuote(revision, release, quote)
    // The page is for another release than the one serving it: Reload, not a failed price,
    // while this is the app's pricer.
    void answer([watch(useWorkbenchPricingStore, (state) => state.quotePricer)], priced, {
      failed: (error) => {
        if (releaseMoved(error)) useAppStore.getState().noteReleaseChanged()
      },
    })
    return priced.then(
      ({ tables, request }) => ({ tables, sample: request }),
      (error: unknown) => {
        // Tables that do not fit are named in the bar's own words, on one line.
        if (storeMisfits(error) !== null) throw new Error(appErrorMessage(error, form, { oneLine: true }))
        throw error
      },
    )
  })
  useWorkbenchPreviewStore.getState().setImporter((componentId, values, file) => importAppRows(revision, release, componentId, values, file))
  useWorkbenchPreviewStore.getState().open(structuredClone(newQuoteValues(form.schema)) as Record<string, SampleRow[]>, null, false)
}
