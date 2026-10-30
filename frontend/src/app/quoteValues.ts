/**
 * What the underwriter app works out of a quote's values (specs/workbench): whether two sets
 * of values are the same quote, the details the app sets on a quote's sheets, the price a
 * saved quote shows on them, what the quote store's refusals name, the quote_details columns
 * the Quotes page lists and how each value shows in them, a moment in full, what was done to a
 * quote, and the values kept in the browser until they are saved or discarded.
 */
import type { QuoteSave, QuoteSource } from "../api/app"
import { apiErrorCode, apiErrorRawDetail, apiErrorStatus } from "../api/errors"
import type { FormSpec, SchemaColumn, WorkbenchAppQuote, WorkbenchAppQuoteActivityEntry, WorkbenchAppRelatedQuote } from "../api/types"
import type { SamplePrice } from "../stores/useWorkbenchPricingStore"
import { canonicalJson } from "../utils/canonicalJson"
import { formatValue } from "../utils/formatValue"
import { formattedNumber } from "../utils/sheetValues"
import { basisOf, columnKey } from "../utils/sheetVisibility"
import { QUOTE_DETAILS, QUOTE_DETAILS_COLUMNS, QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, readableName, type QuoteDetailName, type SampleRow } from "../utils/workbenchForm"

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

// ── A quote's identity and the details the app sets ─────────────────────

/** Whether `a` and `b` are one quote: the same reference, case included, and option. */
export const sameQuote = (a: Pick<WorkbenchAppQuote, "reference" | "option">, b: Pick<WorkbenchAppQuote, "reference" | "option">): boolean =>
  a.reference === b.reference && a.option === b.option

/** `values` with the quote_details cells the app sets taken from `saved`, the rest as typed. */
export function withAppDetails(values: Values, saved: Values): Values {
  const pick = (row: SampleRow | undefined, set: boolean): SampleRow =>
    Object.fromEntries(Object.entries(row ?? {}).filter(([id]) => QUOTE_DETAILS_MANAGED.has(id) === set))
  const row = { ...pick(values[QUOTE_DETAILS_ID]?.[0], false), ...pick(saved[QUOTE_DETAILS_ID]?.[0], true) }
  const others = Object.fromEntries(Object.entries(values).filter(([table]) => table !== QUOTE_DETAILS_ID))
  return Object.keys(row).length === 0 ? others : { ...others, [QUOTE_DETAILS_ID]: [row] }
}

/** `values` without the quote_details cells the app sets: a quote's, which a quote made from it does not take. */
export const withoutAppDetails = (values: Values): Values => withAppDetails(values, {})

/**
 * The quote_details cells the app sets, as the quote's `details` (its row by column name)
 * hold them, for `form`'s sheets, as a save's values hold them: a whole number as its text, a
 * date as its ISO text, a tick for true, and no cell for false or a blank. None on sheets
 * without quote_details.
 */
export function appDetails(form: FormSpec, details: WorkbenchAppQuote["details"]): Values {
  if (!form.schema.tables.some((table) => table.id === QUOTE_DETAILS_ID)) return {}
  const row: SampleRow = {}
  for (const [id, name] of QUOTE_DETAILS_COLUMNS) {
    const value = details[name]
    if (!QUOTE_DETAILS_MANAGED.has(id) || value === undefined || value === null || value === false || value === "") continue
    row[id] = value === true ? true : String(value)
  }
  return { [QUOTE_DETAILS_ID]: [row] }
}

/** The price a saved quote shows on `form`: its tables, for its values as they are. */
export function savedPrice(
  form: FormSpec,
  values: Values,
  tables: Record<string, Record<string, unknown>[]> | null | undefined,
  request: Record<string, unknown>,
): SamplePrice | null {
  if (tables === null || tables === undefined) return null
  return { basis: basisOf(form, values), tables, sample: request }
}

// ── The quote store's refusals ───────────────────────────────────────────

/** A page drawn for another release than the one serving it: 409 without a code of the store's. */
export const releaseMoved = (error: unknown): boolean => apiErrorStatus(error) === 409 && apiErrorCode(error) === null

/**
 * A write repeated under its operation key that landed, the quote changed since by someone
 * else: it is done, and never sent again.
 */
export const writeSuperseded = (error: unknown): boolean => apiErrorCode(error) === "write_superseded"

/** A misfit cell as the store names it: `table, column`, or `table row N, column` in a many-row table. */
function misfitPlace(cell: unknown): string[] {
  if (typeof cell !== "object" || cell === null) return []
  const { table, row, column } = cell as { table?: unknown; row?: unknown; column?: unknown }
  if (typeof table !== "string" || typeof column !== "string") return []
  return [typeof row === "number" ? `${table} row ${row}, ${column}` : `${table}, ${column}`]
}

/** A save or a Mark final refused for what it names: each place, by the labels the sheets give it. */
export interface Refusal {
  of: "save" | "final"
  places: string[]
}

/**
 * The places a refusal names as stopping it: a save's values not of their columns' types
 * (`quote_values_invalid`), or the required fields without a value that kept a quote from
 * being marked final (`quote_not_ready`); null for any other, and for one naming none.
 */
export function refusalOf(error: unknown): Refusal | null {
  const detail = apiErrorRawDetail(error)
  const code = apiErrorCode(error)
  const listed = (field: string): unknown[] => {
    const value = detail?.[field]
    return Array.isArray(value) ? value : []
  }
  if (code === "quote_values_invalid") {
    const places = listed("cells").flatMap(misfitPlace)
    return places.length === 0 ? null : { of: "save", places }
  }
  if (code === "quote_not_ready") {
    const places = listed("missing").filter((field): field is string => typeof field === "string")
    return places.length === 0 ? null : { of: "final", places }
  }
  return null
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
export const STAGE_LABEL: Record<WorkbenchAppQuote["stage"], string> = { draft: "Draft", final: "Final" }

// ── Time ─────────────────────────────────────────────────────────────────

/** A moment in full, for a title on hover. */
export const fullTime = (iso: string): string => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })

// ── Values kept in the browser ───────────────────────────────────────────

/** A write a save sends: the quote `reference`/`option` saved over `version`. */
export type QuoteWrite = { kind: "save"; reference: string; option: number; version: number; body: QuoteSave }

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
  /** The quote's version they were typed over; null for a new quote. */
  version: number | null
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
  return write.kind === "save" && typeof write.reference === "string" && typeof write.option === "number" && typeof write.version === "number"
}

const DRAFT_PREFIX = "haute-app-draft"

/** Where a quote's draft is kept: one per app, person and quote (its label, `ABC-1/2`), or per new quote. */
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
 * one per app, person and what was asked, a new quote or one made from a quote.
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
 * is trimmed and, in Unicode's composed form, 1 to 40 characters, holding no `/`, which the
 * app puts before the option, and no control character, and more than `.` or `..`, which an
 * address reads as folders. It is kept as keyed: `ABC-1` and `abc-1` are two references.
 */
export function referenceProblem(reference: string): string | null {
  const text = reference.trim().normalize("NFC")
  if (text === "") return "Required"
  if ([...text].length > REFERENCE_MOST) return `At most ${REFERENCE_MOST} characters`
  if (text.includes("/")) return "No / in a reference"
  if (text === "." || text === "..") return "Not . or .."
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

/** A renewal's values: those of its source, with `is_renewal` ticked in its details. */
export function asRenewal(values: Values): Values {
  const [details = {}] = values[QUOTE_DETAILS_ID] ?? []
  return { ...values, [QUOTE_DETAILS_ID]: [{ ...details, [QUOTE_DETAILS.is_renewal]: true }] }
}

// ── Activity ─────────────────────────────────────────────────────────────

/** How a quote was made from another, in a sentence. */
export const RELATION_TEXT = { duplicate: "a duplicate", renewal: "a renewal", alternative: "an alternative" } as const

/**
 * What an activity entry says was done, in a sentence after who did it: its making as what
 * it was made from, `source`, when it was.
 */
export function activityText(entry: WorkbenchAppQuoteActivityEntry, source: WorkbenchAppRelatedQuote | null = null): string {
  switch (entry.action) {
    case "created":
      return source === null ? "made it" : `made it as ${RELATION_TEXT[source.relation]} of ${source.label}`
    case "saved":
      return entry.priced_release === null ? "saved it, unpriced" : "saved it, priced"
    case "assigned":
      return entry.assignee === null ? "assigned it" : `assigned it to ${entry.assignee}`
    case "noted":
      return entry.note ? "wrote the note" : "cleared the note"
    case "archived":
      return "archived it"
    case "restored":
      return "restored it"
    case "finalised":
      return "marked it final"
    case "amended":
      return entry.reason ? `amended it: ${entry.reason}` : "amended it"
  }
}
