/**
 * What the underwriter app works out of a quote's values (specs/workbench): whether two sets
 * of values are the same quote, the price a saved revision shows on the sheets, the
 * quote_details columns the Quotes page lists and how each value shows in them, a moment in
 * full, and the values kept in the browser until they are saved or discarded.
 */
import type { QuoteSave, QuoteSource } from "../api/app"
import { apiErrorCode, apiErrorStatus } from "../api/errors"
import type { FormSpec, SchemaColumn, WorkbenchAppQuote, WorkbenchAppQuoteEvent } from "../api/types"
import type { SamplePrice } from "../stores/useWorkbenchPricingStore"
import { canonicalJson } from "../utils/canonicalJson"
import { formatValue } from "../utils/formatValue"
import { formattedNumber } from "../utils/sheetValues"
import { basisOf, columnKey } from "../utils/sheetVisibility"
import { QUOTE_DETAILS, QUOTE_DETAILS_ID, readableName, type QuoteDetailName, type SampleRow } from "../utils/workbenchForm"

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

/** A page drawn for another release than the one serving it: 409 without a code of the store's. */
export const releaseMoved = (error: unknown): boolean => apiErrorStatus(error) === 409 && apiErrorCode(error) === null

/** Where each value of `values` sits on `form`'s sheets, by `tableId:columnId`, named `table.column`. */
export function cellPlaces(form: FormSpec, values: Values): Record<string, string> {
  const places: Record<string, string> = {}
  for (const table of form.schema.tables) {
    for (const row of values[table.id] ?? []) {
      for (const columnId of Object.keys(row)) {
        const column = table.columns.find((candidate) => candidate.id === columnId)
        if (column !== undefined) places[columnKey(table.id, columnId)] = `${table.name}.${column.name}`
      }
    }
  }
  return places
}

/** Each place of a value in `values` that `kept` left out, named as `places` names it, once each. */
export function lostPlaces(values: Values, kept: Values, places: Readonly<Record<string, string>>): string[] {
  const lost = new Set<string>()
  for (const [tableId, rows] of Object.entries(normalisedValues(values))) {
    rows.forEach((row, index) => {
      for (const columnId of Object.keys(row)) {
        if (kept[tableId]?.[index]?.[columnId] === undefined) lost.add(places[columnKey(tableId, columnId)] ?? `${tableId}.${columnId}`)
      }
    })
  }
  return [...lost]
}

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
const LISTED = ["insured", "broker", "inception_date"] as const satisfies readonly QuoteDetailName[]

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

/** A moment in full, for a title on hover. */
export const fullTime = (iso: string): string => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })

// ── Values kept in the browser ───────────────────────────────────────────

/** A write a save sends: a quote's next revision over `version`. */
export type QuoteWrite = { kind: "save"; quoteId: string; version: number; body: QuoteSave }

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
  /**
   * Where each value sat on the sheets it was typed into, by `tableId:columnId`, named
   * `table.column`, so a restore onto other sheets names what has no place on them.
   */
  places: Record<string, string>
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
  return write.kind === "save" && typeof write.quoteId === "string" && typeof write.version === "number"
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
    if (typeof draft !== "object" || draft === null || typeof draft.values !== "object" || typeof draft.places !== "object") return null
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

// ── A quote asked for ────────────────────────────────────────────────────

const CREATING_PREFIX = "haute-app-creating"

/**
 * Where the operation key of a quote asked for is held in this tab until its answer comes:
 * one per app, person and what was asked, a new quote or one made from a quote's revision.
 */
export const creationKey = (app: string, principal: string, asked: string): string => `${CREATING_PREFIX}:${app}:${principal}:${asked}`

/**
 * The operation key held under `key`, or a new one held there: a reload before the answer
 * came asks again with the same key, so the quote is made once. A tab that keeps nothing
 * asks with a new key each time.
 */
export function heldOperation(key: string): string {
  try {
    const held = window.sessionStorage.getItem(key)
    if (held !== null) return held
    const operation = operationKey()
    window.sessionStorage.setItem(key, operation)
    return operation
  } catch {
    return operationKey()
  }
}

/** Let go of the key held under `key`: its quote was made, or refused, and the next is asked for anew. */
export function releaseOperation(key: string): void {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // Nothing held, nothing to let go.
  }
}

// ── A new quote keyed in the window ──────────────────────────────────────

/** The most characters a reference holds. */
export const REFERENCE_MOST = 40

/**
 * What stops `reference` being a quote's reference, as the server checks it, or null: it
 * is trimmed and then 1 to 40 characters, holding no `/`, which the app puts before the
 * option, and no control character.
 */
export function referenceProblem(reference: string): string | null {
  const text = reference.trim()
  if (text === "") return "Required"
  if ([...text].length > REFERENCE_MOST) return `At most ${REFERENCE_MOST} characters`
  if (text.includes("/")) return "No / in a reference"
  if (/[\p{Cc}\p{Cf}]/u.test(text)) return "No control characters"
  return null
}

/**
 * A creation sent from the window, whole, kept in the tab until its answer comes: what it
 * sends again, exactly, when tried again.
 */
export interface KeptCreation {
  operation: string
  form_revision: string
  release: string
  reference: string
  values: Values
  source: QuoteSource | null
  /** The window's title, which it is offered back under. */
  title: string
}

const KEPT_PREFIX = "haute-app-creation"

/** Where the creation sent from the window is kept in this tab: one per app and person. */
export const keptCreationKey = (app: string, principal: string): string => `${KEPT_PREFIX}:${app}:${principal}`

const isKeptCreation = (value: unknown): value is KeptCreation => {
  if (typeof value !== "object" || value === null) return false
  const kept = value as Partial<KeptCreation>
  return (
    typeof kept.operation === "string"
    && typeof kept.form_revision === "string"
    && typeof kept.release === "string"
    && typeof kept.reference === "string"
    && typeof kept.values === "object"
    && kept.values !== null
    && typeof kept.title === "string"
    && (kept.source === null || typeof kept.source === "object")
  )
}

/** The creation kept under `key`, or null: none, or the tab keeps none. */
export function readKeptCreation(key: string): KeptCreation | null {
  try {
    const text = window.sessionStorage.getItem(key)
    if (text === null) return null
    const kept: unknown = JSON.parse(text)
    return isKeptCreation(kept) ? kept : null
  } catch {
    return null
  }
}

/** Keep `creation` under `key` before it is sent; false when the tab could not. */
export function keepCreation(key: string, creation: KeptCreation): boolean {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(creation))
    return true
  } catch {
    return false
  }
}

/** Let go of the creation kept under `key`: answered, refused or discarded. */
export function forgetCreation(key: string): void {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // Nothing kept, nothing to let go.
  }
}

/** A renewal's values: those carried from its source, with `is_renewal` ticked in its details. */
export function asRenewal(values: Values): Values {
  const [details = {}] = values[QUOTE_DETAILS_ID] ?? []
  return { ...values, [QUOTE_DETAILS_ID]: [{ ...details, [QUOTE_DETAILS.is_renewal]: true }] }
}

/**
 * A quote's reference and option as the app shows them, `Q-000123/2`, as an event names
 * them; an event written before options names none, every quote then being option 1.
 */
const labelOf = (reference: string, option: unknown): string => `${reference}/${typeof option === "number" ? option : 1}`

// ── History ──────────────────────────────────────────────────────────────

/** How a quote was made from another, in a sentence. */
export const RELATION_TEXT = { duplicate: "a duplicate", renewal: "a renewal", alternative: "an alternative" } as const

/** What an event says was done, in a sentence after who did it. */
export function eventText(event: WorkbenchAppQuoteEvent): string {
  const detail = event.detail as Record<string, unknown>
  const text = (key: string) => (typeof detail[key] === "string" ? (detail[key] as string) : "")
  switch (event.kind) {
    case "created": {
      const source = detail.source as { reference?: string; option?: number; revision?: number; relation?: keyof typeof RELATION_TEXT } | undefined
      return source?.reference
        ? `made it as ${RELATION_TEXT[source.relation ?? "duplicate"]} of ${labelOf(source.reference, source.option)} revision ${source.revision ?? "?"}`
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
      return `made ${labelOf(text("reference"), detail.option)} from revision ${event.revision ?? "?"}, as ${RELATION_TEXT[(detail.relation as keyof typeof RELATION_TEXT) ?? "duplicate"]}`
  }
}
