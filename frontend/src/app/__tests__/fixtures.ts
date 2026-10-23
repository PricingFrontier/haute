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
  return {
    id: "q1",
    reference: "Q-000001",
    option: 1,
    label,
    title: "Fleet",
    details: { quote_reference: "Q-000001", quote_option: 1, quote_name: "Fleet", insured: "Acme", stage: "priced", assignee: "ana@example.com", archived: false },
    stage: "priced",
    archived: false,
    priced: true,
    assignee: "ana@example.com",
    owner: "ana@example.com",
    created_at: "2026-10-12T09:00:00+00:00",
    latest: 1,
    release: "rel-1",
    form_revision: "rev-9",
    last_saved_by: "ana@example.com",
    last_saved_at: "2026-10-12T09:00:00+00:00",
    final_revision: null,
    final_by: null,
    final_at: null,
    archived_by: null,
    archived_at: null,
    note: "",
    note_by: null,
    note_at: null,
    source_quote_id: null,
    source_reference: null,
    source_option: null,
    source_label: null,
    source_revision: null,
    relation: null,
    ...overrides,
  }
}

export function answer(quote: Partial<WorkbenchAppQuote> = {}, revision: Partial<WorkbenchAppQuoteResponse["revision"]> = {}, extra: Partial<WorkbenchAppQuoteResponse> = {}): WorkbenchAppQuoteResponse {
  const made = appQuote(quote)
  return {
    quote: made,
    revision: {
      number: made.latest,
      values: VALUES,
      tables: TABLES,
      request: { policy: { state: "NY" } },
      release: "rel-1",
      form_revision: "rev-9",
      saved_by: "ana@example.com",
      saved_at: "2026-10-12T09:00:00+00:00",
      ...revision,
    },
    form: null,
    moved: false,
    ...extra,
  }
}

/**
 * A change's answer as the server gives it: the quote with `latest` the revision the change
 * wrote, and that revision.
 */
export function changed(quote: Partial<WorkbenchAppQuote>, revision: Partial<WorkbenchAppQuoteResponse["revision"]> = {}): WorkbenchAppQuoteChangeResponse {
  const made = answer(quote, revision)
  return { quote: made.quote, revision: made.revision }
}
