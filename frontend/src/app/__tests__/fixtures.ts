/** Quotes as the underwriter app's routes answer them, for its tests. */
import type { WorkbenchAppQuote, WorkbenchAppQuoteChangeResponse, WorkbenchAppQuoteResponse, WorkbenchAppReleaseResponse } from "../../api/types"

export const release: WorkbenchAppReleaseResponse = {
  id: "rel-1",
  form_revision: "rev-9",
  pipeline_name: "motor",
  haute_version: "1.2.0",
  created_at: "2026-10-12T09:00:00+00:00",
  host: null,
  principal: "ana@example.com",
  quotes: true,
  edit_role: null,
  can_edit: true,
}
export const VALUES = { policy: [{ "policy.state": "NY" }] }
export const TABLES = { premiums: [{ item: "Crane", premium: 120 }] }

export function appQuote(overrides: Partial<WorkbenchAppQuote> = {}): WorkbenchAppQuote {
  // Shown as its reference and option, unless the test names another label.
  const label = `${overrides.reference ?? "Q-000001"}/${overrides.option ?? 1}`
  // Priced on this release, unless the test names another or none.
  const pricedRelease = "priced_release" in overrides ? (overrides.priced_release ?? null) : "rel-1"
  return {
    reference: "Q-000001",
    option: 1,
    label,
    title: "Fleet",
    details: { quote_reference: "Q-000001", quote_option: 1, quote_name: "Fleet", insured: "Acme", stage: "draft", assignee: "ana@example.com", archived: false },
    stage: "draft",
    assignee: "ana@example.com",
    archived: false,
    version: 1,
    saved_at: "2026-10-12T09:00:00+00:00",
    saved_by: "ana@example.com",
    priced: pricedRelease !== null,
    priced_release: pricedRelease,
    final_by: null,
    final_at: null,
    archived_by: null,
    archived_at: null,
    note: "",
    note_by: null,
    note_at: null,
    source_reference: null,
    source_option: null,
    source_label: null,
    relation: null,
    ...overrides,
  }
}

/**
 * A quote's answer as the server gives it: the quote, its values, its prices (none while it
 * is unpriced), the request its values price as, and the rows with no place on the sheets.
 */
export function answer(quote: Partial<WorkbenchAppQuote> = {}, extra: Partial<Omit<WorkbenchAppQuoteResponse, "quote">> = {}): WorkbenchAppQuoteResponse {
  const made = appQuote(quote)
  return {
    quote: made,
    values: VALUES,
    tables: made.priced ? TABLES : null,
    request: { policy: { state: "NY" } },
    dropped: [],
    moved: false,
    ...extra,
  }
}

/** A change's answer as the server gives it: the quote alone, as the change left it. */
export function changed(quote: Partial<WorkbenchAppQuote> = {}): WorkbenchAppQuoteChangeResponse {
  return { quote: appQuote(quote) }
}
