/**
 * What the underwriter app works out of a quote's values (specs/workbench): whether two sets
 * of values are the same quote, the price a saved revision shows on the sheets, the
 * quote_details columns the Quotes page lists and how each value shows in them, how long
 * ago a moment was, and the values kept in the browser until they are saved or discarded.
 */
import type { QuoteSave, QuoteSource } from "../api/app"
import type { FormSpec, SchemaColumn, WorkbenchAppQuote, WorkbenchAppQuoteEvent } from "../api/types"
import type { SamplePrice } from "../stores/useWorkbenchPricingStore"
import { canonicalJson } from "../utils/canonicalJson"
import { formatValue } from "../utils/formatValue"
import { formattedNumber, valuesBasis } from "../utils/sheetValues"
import { sheetVisibility } from "../utils/sheetVisibility"
import { QUOTE_DETAILS_ID, readableName, type SampleRow } from "../utils/workbenchForm"

export type Values = Record<string, SampleRow[]>

/** `values` without what is not a value: blank cells, rows left empty at the end, tables with none. */
export function normalisedValues(values: Readonly<Record<string, readonly SampleRow[]>>): Values {
  const kept: Values = {}
  for (const [table, rows] of Object.entries(values)) {
    const cleaned = rows.map((row) => Object.fromEntries(Object.entries(row).filter(([, value]) => value !== "")))
    while (cleaned.length > 0 && Object.keys(cleaned[cleaned.length - 1]).length === 0) cleaned.pop()
    if (cleaned.length > 0) kept[table] = cleaned
  }
  return kept
}

/** What identifies `values` as a quote: the same for the same values, however blanks were left. */
export const valuesKey = (values: Readonly<Record<string, readonly SampleRow[]>>): string => canonicalJson(normalisedValues(values))

/**
 * `values` on `form`'s sheets: the cells of its input tables' columns alone, by id, as
 * values typed into other sheets, a draft kept before a release, are put back.
 */
export function onSheets(form: FormSpec, values: Values): Values {
  const kept: Values = {}
  for (const table of form.schema.tables) {
    const rows = values[table.id]
    if (table.role !== "input" || rows === undefined) continue
    const columns = new Set(table.columns.map((column) => column.id))
    kept[table.id] = rows.map((row) => Object.fromEntries(Object.entries(row).filter(([id]) => columns.has(id))))
  }
  return kept
}

/** The basis the sheets read `values` on `form` for: a price for another is out of date. */
export const basisOf = (form: FormSpec, values: Values): string =>
  valuesBasis(form.schema, sheetVisibility(form, values).hidden, values)

/** The price a saved revision shows on `form`: its tables, for its values as they are. */
export function savedPrice(
  form: FormSpec,
  values: Values,
  tables: Record<string, Record<string, unknown>[]> | null | undefined,
  request: Record<string, unknown>,
): SamplePrice | null {
  if (tables === null || tables === undefined) return null
  return { basis: basisOf(form, values), tables, sample: request }
}

// ── The Quotes page's columns ────────────────────────────────────────────

export interface ListColumn {
  /** The quote_details column's name, which the quote's details are keyed by. */
  key: string
  label: string
  /** The schema column, which says how a value shows; null when the sheets have none. */
  column: SchemaColumn | null
}

/** The quote_details columns the Quotes page lists after the quote's reference and title. */
const LISTED = ["insured", "broker", "inception_date"] as const

/** The listed columns, as the release's sheets label and format them. */
export function listColumns(form: FormSpec | null): ListColumn[] {
  const table = form?.schema.tables.find((candidate) => candidate.id === QUOTE_DETAILS_ID)
  return LISTED.map((key) => {
    const column = table?.columns.find((candidate) => candidate.name === key) ?? null
    return { key, label: column?.label || readableName(key), column }
  })
}

/** A value as the list shows it: a date as a day, a number as its column formats it, a dash when blank. */
export function listValue(column: ListColumn, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—"
  if (typeof value === "boolean") return value ? "Yes" : "No"
  if (column.column?.type === "date" && typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
  }
  if (column.column !== null) {
    const formatted = formattedNumber(column.column, value)
    if (formatted !== null) return formatted
  }
  return typeof value === "string" ? value : formatValue(value)
}

/** The stage as the app names it. */
export const STAGE_LABEL: Record<WorkbenchAppQuote["stage"], string> = { draft: "Draft", priced: "Priced", final: "Final" }

// ── Time ─────────────────────────────────────────────────────────────────

/** How long ago `iso` was, as a list says it: just now, minutes, hours, yesterday, then the date. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso)
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000)
  if (seconds < 45) return "just now"
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  if (days === 1) return "yesterday"
  if (days < 7) return `${days}d ago`
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short", year: then.getFullYear() === now.getFullYear() ? undefined : "numeric" })
}

/** A moment in full, for a title on hover. */
export const fullTime = (iso: string): string => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })

// ── Values kept in the browser ───────────────────────────────────────────

/** A write a save sends: a new quote, made from a source or not, or a quote's next revision over `version`. */
export type QuoteWrite =
  | { kind: "create"; body: QuoteSave; source: QuoteSource | null }
  | { kind: "save"; quoteId: string; version: number; body: QuoteSave }

/**
 * A write sent and not answered, or answered by a server failure, so it may have landed:
 * its operation key is bound to what it saves, `basis` (the values, what they are saved as,
 * and priced or not), and it is repeated exactly before anything else is saved.
 */
export interface PendingWrite {
  basis: string
  write: QuoteWrite
}

/** Values typed and not yet saved, kept in this browser as they were left. */
export interface Draft {
  values: Values
  /** The quote's version and newest revision they were typed over; null for a new quote. */
  version: number | null
  latest: number | null
  /** The sheets' revision they were typed into. */
  formRevision: string
  keptAt: string
  /** The write a save still held when they were kept. */
  pending: PendingWrite | null
}

const isPendingWrite = (value: unknown): value is PendingWrite => {
  if (typeof value !== "object" || value === null) return false
  const { basis, write } = value as Partial<PendingWrite>
  if (typeof basis !== "string" || typeof write !== "object" || write === null) return false
  const body = (write as { body?: Partial<QuoteSave> }).body
  if (typeof body !== "object" || body === null || typeof body.operation !== "string" || typeof body.values !== "object") return false
  return write.kind === "create" || (write.kind === "save" && typeof write.quoteId === "string" && typeof write.version === "number")
}

const DRAFT_PREFIX = "haute-app-draft"

/** Where a quote's draft is kept: one per app, person and quote, or per new quote. */
export const draftKey = (app: string, principal: string, quote: string): string => `${DRAFT_PREFIX}:${app}:${principal}:${quote}`

/** The draft kept under `key`, or null: none, or the browser keeps none. */
export function readDraft(key: string): Draft | null {
  try {
    const text = window.localStorage.getItem(key)
    if (text === null) return null
    const draft = JSON.parse(text) as Draft
    if (typeof draft !== "object" || draft === null || typeof draft.values !== "object") return null
    // A held write that is not one is none: the values are still offered back.
    return { ...draft, pending: isPendingWrite(draft.pending) ? draft.pending : null }
  } catch {
    return null
  }
}

/** Keep `draft` under `key`; false when the browser could not. */
export function writeDraft(key: string, draft: Draft): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(draft))
    return true
  } catch {
    return false
  }
}

export function clearDraft(key: string): void {
  try {
    window.localStorage.removeItem(key)
  } catch {
    // Nothing kept, nothing to clear.
  }
}

/** A new operation key, which makes a save repeated after its answer was lost write once. */
export function operationKey(): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `save-${random}`.slice(0, 64)
}

// ── History ──────────────────────────────────────────────────────────────

/** How a quote was made from another, in a sentence. */
export const RELATION_TEXT = { duplicate: "a duplicate", renewal: "a renewal", alternative: "an alternative" } as const

/** What an event says was done, in a sentence after who did it. */
export function eventText(event: WorkbenchAppQuoteEvent): string {
  const detail = event.detail as Record<string, unknown>
  const text = (key: string) => (typeof detail[key] === "string" ? (detail[key] as string) : "")
  switch (event.kind) {
    case "created": {
      const source = detail.source as { reference?: string; revision?: number; relation?: keyof typeof RELATION_TEXT } | undefined
      return source?.reference
        ? `made it as ${RELATION_TEXT[source.relation ?? "duplicate"]} of ${source.reference} revision ${source.revision ?? "?"}`
        : "made it"
    }
    case "saved":
      return `saved revision ${event.revision ?? "?"}${detail.priced ? ", priced" : ", unpriced"}`
    case "assigned":
      return `assigned it to ${text("to")}`
    case "noted":
      return text("note") ? "wrote the note" : "cleared the note"
    case "archived":
      return "archived it"
    case "restored":
      return "restored it"
    case "finalised":
      return `marked revision ${event.revision ?? "?"} final`
    case "reopened":
      return `reopened final revision ${typeof detail.final_revision === "number" ? detail.final_revision : "?"}: ${text("reason")}`
    case "derived":
      return `made ${text("reference")} from revision ${event.revision ?? "?"}, as ${RELATION_TEXT[(detail.relation as keyof typeof RELATION_TEXT) ?? "duplicate"]}`
  }
}
