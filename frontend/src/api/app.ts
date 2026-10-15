/**
 * The underwriter app's routes (specs/workbench): the release it serves with who the host
 * signed in (GET /api/app/release), the release's form with its revision
 * (GET /api/app/form), a quote priced through the release's pipeline
 * (POST /api/app/price), which a page drawn for another revision of the form or another
 * release is refused, and, with a quote store, the quotes it keeps (/api/app/quotes).
 */

import type { SampleRow } from "../utils/workbenchForm"
import { expectGeneratedContract } from "../types/generatedContractValidation"
import { post, request } from "./client"
import type {
  WorkbenchAppCarryResponse,
  WorkbenchAppFormResponse,
  WorkbenchAppPeopleResponse,
  WorkbenchAppPriceRequest,
  WorkbenchAppPriceResponse,
  WorkbenchAppQuoteAssign,
  WorkbenchAppQuoteChange,
  WorkbenchAppQuoteChangeResponse,
  WorkbenchAppQuoteCreateRequest,
  WorkbenchAppQuoteFinal,
  WorkbenchAppQuoteHistoryResponse,
  WorkbenchAppQuoteNote,
  WorkbenchAppQuoteReopen,
  WorkbenchAppQuoteResponse,
  WorkbenchAppQuoteReviseRequest,
  WorkbenchAppQuoteSource,
  WorkbenchAppQuotesResponse,
  WorkbenchAppReleaseResponse,
} from "./types"

const workbenchValidators = () => import("../generated/api-contracts.workbench-app.validators.mjs")

/** The app answers a pricing within its own time limit; the browser waits a little longer. */
const PRICE_TIMEOUT_MS = 90_000

export async function fetchAppRelease(): Promise<WorkbenchAppReleaseResponse> {
  const data = await request<unknown>("/api/app/release")
  return expectGeneratedContract(
    "WorkbenchAppReleaseResponse",
    (await workbenchValidators()).validateWorkbenchAppReleaseResponse,
    data,
  )
}

export async function fetchAppForm(): Promise<WorkbenchAppFormResponse> {
  const data = await request<unknown>("/api/app/form")
  return expectGeneratedContract(
    "WorkbenchAppFormResponse",
    (await workbenchValidators()).validateWorkbenchAppFormResponse,
    data,
  )
}

/** The quote typed into the sheets drawn from form revision `revision` of release `release`, priced. */
export async function priceAppQuote(
  revision: string,
  release: string,
  values: Record<string, SampleRow[]>,
): Promise<WorkbenchAppPriceResponse> {
  const body: WorkbenchAppPriceRequest = { form_revision: revision, release, values }
  const data = await post<unknown>("/api/app/price", body, { timeout: PRICE_TIMEOUT_MS })
  return expectGeneratedContract(
    "WorkbenchAppPriceResponse",
    (await workbenchValidators()).validateWorkbenchAppPriceResponse,
    data,
  )
}

/** A list of the Quotes page as the address holds it. */
export interface QuotesQuery {
  view: "mine" | "team" | "archived"
  stage: "draft" | "priced" | "final" | null
  search: string
  assignee: string | null
  sort: "saved" | "reference" | "insured"
}

/** How a quote is made from another's revision. */
export type QuoteRelation = WorkbenchAppQuoteSource["relation"]

/** What a save sends: the values on the sheets drawn from `revision` of `release`, and the prices shown for them or null. */
export type QuoteSave = Required<Omit<WorkbenchAppQuoteCreateRequest, "source">>

/** The quote and revision a new quote is made from, and how. */
export type QuoteSource = WorkbenchAppQuoteSource

/** Each change a quote takes besides a save, by its route's name, and the body its route reads. */
interface QuoteChangeBodies {
  assignee: WorkbenchAppQuoteAssign
  archive: WorkbenchAppQuoteChange
  restore: WorkbenchAppQuoteChange
  final: WorkbenchAppQuoteFinal
  reopen: WorkbenchAppQuoteReopen
  note: WorkbenchAppQuoteNote
}

/** A change a quote takes besides a save, by its route's name. */
export type QuoteChange = keyof QuoteChangeBodies

/** What change `C` sends besides the version it is made on. */
export type QuoteChangeFields<C extends QuoteChange> = Omit<QuoteChangeBodies[C], "version">

/** A page of `query`'s list, after `cursor` when given. */
export async function fetchAppQuotes(query: QuotesQuery, cursor: string | null = null): Promise<WorkbenchAppQuotesResponse> {
  const params = new URLSearchParams({ view: query.view, sort: query.sort })
  if (query.stage !== null) params.set("stage", query.stage)
  if (query.search.trim() !== "") params.set("search", query.search.trim())
  if (query.assignee !== null) params.set("assignee", query.assignee)
  if (cursor !== null) params.set("cursor", cursor)
  const data = await request<unknown>(`/api/app/quotes?${params.toString()}`)
  return expectGeneratedContract("WorkbenchAppQuotesResponse", (await workbenchValidators()).validateWorkbenchAppQuotesResponse, data)
}

async function quoteResponse(data: unknown): Promise<WorkbenchAppQuoteResponse> {
  return expectGeneratedContract("WorkbenchAppQuoteResponse", (await workbenchValidators()).validateWorkbenchAppQuoteResponse, data)
}

/** A new quote, made from `source` when given. */
export async function createAppQuote(save: QuoteSave, source: QuoteSource | null): Promise<WorkbenchAppQuoteResponse> {
  const body: WorkbenchAppQuoteCreateRequest = { ...save, source }
  return quoteResponse(await post<unknown>("/api/app/quotes", body, { timeout: PRICE_TIMEOUT_MS }))
}

/** A quote with its newest revision, or revision `revision`. */
export async function openAppQuote(id: string, revision: number | null = null): Promise<WorkbenchAppQuoteResponse> {
  const path = revision === null ? `/api/app/quotes/${encodeURIComponent(id)}` : `/api/app/quotes/${encodeURIComponent(id)}/revisions/${revision}`
  return quoteResponse(await request<unknown>(path))
}

/** The quote's next revision, saved over `version` of it. */
export async function saveAppQuote(id: string, save: QuoteSave, version: number): Promise<WorkbenchAppQuoteResponse> {
  const body: WorkbenchAppQuoteReviseRequest = { ...save, version }
  return quoteResponse(await post<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/revisions`, body, { timeout: PRICE_TIMEOUT_MS }))
}

/**
 * A change to the quote at `version`, with what it changes, answered with the revision it
 * wrote. A note is no revision: it is written over whichever the quote is at.
 */
export async function changeAppQuote<C extends QuoteChange>(
  id: string,
  change: C,
  version: number,
  fields: QuoteChangeFields<C>,
): Promise<WorkbenchAppQuoteChangeResponse> {
  // A note names no version; every other change's fields are its body's but the version.
  const body = (change === "note" ? fields : { ...fields, version }) as QuoteChangeBodies[C]
  const data = await post<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/${change}`, body)
  return expectGeneratedContract("WorkbenchAppQuoteChangeResponse", (await workbenchValidators()).validateWorkbenchAppQuoteChangeResponse, data)
}

/** The quote's revisions, events and related quotes. */
export async function fetchAppQuoteHistory(id: string): Promise<WorkbenchAppQuoteHistoryResponse> {
  const data = await request<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/history`)
  return expectGeneratedContract("WorkbenchAppQuoteHistoryResponse", (await workbenchValidators()).validateWorkbenchAppQuoteHistoryResponse, data)
}

/**
 * Revision `revision` of the quote, its values carried onto the release's sheets: without
 * the details the app set for it, for a quote made from it, or with them (`details`) for the
 * same quote moved onto newer sheets.
 */
export async function carryAppQuote(id: string, revision: number, details = false): Promise<WorkbenchAppCarryResponse> {
  const asked = details ? "?details=true" : ""
  const data = await request<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/revisions/${revision}/carry${asked}`)
  return expectGeneratedContract("WorkbenchAppCarryResponse", (await workbenchValidators()).validateWorkbenchAppCarryResponse, data)
}

/** The people quotes are with, or were made or saved by. */
export async function fetchAppPeople(): Promise<WorkbenchAppPeopleResponse> {
  const data = await request<unknown>("/api/app/people")
  return expectGeneratedContract("WorkbenchAppPeopleResponse", (await workbenchValidators()).validateWorkbenchAppPeopleResponse, data)
}
