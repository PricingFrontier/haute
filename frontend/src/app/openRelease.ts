import { priceAppQuote } from "../api/app"
import type { FormSpec } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { newQuoteValues } from "../utils/sheetValues"
import { columnKey } from "../utils/sheetVisibility"
import { QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, type SampleRow } from "../utils/workbenchForm"
import { releaseMoved } from "./quoteValues"
import useAppStore from "./useAppStore"

/** The quote_details columns the app sets itself, which take no typing on its sheets. */
export const APP_SET_COLUMNS: ReadonlySet<string> = new Set([...QUOTE_DETAILS_MANAGED].map((id) => columnKey(QUOTE_DETAILS_ID, id)))

/**
 * Put the underwriter app's release (specs/workbench) in the stores Preview draws from:
 * the form read as at `revision`, Preview showing the app's quote rather than a sample, the
 * columns the app sets locked, the quote priced through the app's own route on that
 * revision of release `release` rather than on a pipeline in an editor, and a new quote,
 * holding its columns' defaults, the quote of an app without a quote store.
 */
export function openRelease(form: FormSpec, revision: string, release: string): void {
  useWorkbenchFormStore.getState().install(form, revision)
  useWorkbenchViewStore.setState({ underwriterApp: true })
  useWorkbenchViewStore.getState().showMode("preview")
  useWorkbenchPreviewStore.getState().setLocked(APP_SET_COLUMNS)
  useWorkbenchPricingStore.getState().setQuotePricer(async (_form, quote) => {
    try {
      const { tables, request } = await priceAppQuote(revision, release, quote)
      return { tables, sample: request }
    } catch (error: unknown) {
      // The page is for another release than the one serving it: Reload, not a failed price.
      if (releaseMoved(error)) useAppStore.getState().noteReleaseChanged()
      throw error
    }
  })
  useWorkbenchPreviewStore.getState().open(structuredClone(newQuoteValues(form.schema)) as Record<string, SampleRow[]>, null, false)
}
