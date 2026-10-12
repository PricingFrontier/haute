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
  WorkbenchAppPriceResponse,
  WorkbenchAppQuoteChangeResponse,
  WorkbenchAppQuoteHistoryResponse,
  WorkbenchAppQuoteResponse,
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
  const data = await post<unknown>("/api/app/price", { form_revision: revision, release, values }, { timeout: PRICE_TIMEOUT_MS })
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
  sort: "saved" | "reference" | "title"
}

/** How a quote is made from another's revision. */
export type QuoteRelation = "duplicate" | "renewal" | "alternative"

/** What a save sends: the values on the sheets drawn from `revision` of `release`, and the prices shown for them or null. */
export interface QuoteSave {
  operation: string
  form_revision: string
  release: string
  values: Record<string, SampleRow[]>
  shown: Record<string, Record<string, unknown>[]> | null
}

/** A change a quote takes besides a save, by its route's name. */
export type QuoteChange = "name" | "assignee" | "note" | "archive" | "restore" | "final" | "reopen"

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

/** A new quote, named `name`, made from `source` when given. */
export async function createAppQuote(
  save: QuoteSave,
  name: string,
  source: { quote_id: string; revision: number; relation: QuoteRelation } | null,
): Promise<WorkbenchAppQuoteResponse> {
  return quoteResponse(await post<unknown>("/api/app/quotes", { ...save, name, source }, { timeout: PRICE_TIMEOUT_MS }))
}

/** A quote with its newest revision, or revision `revision`. */
export async function openAppQuote(id: string, revision: number | null = null): Promise<WorkbenchAppQuoteResponse> {
  const path = revision === null ? `/api/app/quotes/${encodeURIComponent(id)}` : `/api/app/quotes/${encodeURIComponent(id)}/revisions/${revision}`
  return quoteResponse(await request<unknown>(path))
}

/** The quote's next revision, saved over `version` of it. */
export async function saveAppQuote(id: string, save: QuoteSave, version: number): Promise<WorkbenchAppQuoteResponse> {
  return quoteResponse(await post<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/revisions`, { ...save, version }, { timeout: PRICE_TIMEOUT_MS }))
}

/** A change to the quote at `version`, with what it changes. */
export async function changeAppQuote(
  id: string,
  change: QuoteChange,
  version: number,
  fields: Record<string, string | number> = {},
): Promise<WorkbenchAppQuoteChangeResponse> {
  const data = await post<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/${change}`, { version, ...fields })
  return expectGeneratedContract("WorkbenchAppQuoteChangeResponse", (await workbenchValidators()).validateWorkbenchAppQuoteChangeResponse, data)
}

/** The quote's revisions, events and related quotes. */
export async function fetchAppQuoteHistory(id: string): Promise<WorkbenchAppQuoteHistoryResponse> {
  const data = await request<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/history`)
  return expectGeneratedContract("WorkbenchAppQuoteHistoryResponse", (await workbenchValidators()).validateWorkbenchAppQuoteHistoryResponse, data)
}

/** Revision `revision` of the quote, its values carried onto the release's sheets. */
export async function carryAppQuote(id: string, revision: number): Promise<WorkbenchAppCarryResponse> {
  const data = await request<unknown>(`/api/app/quotes/${encodeURIComponent(id)}/revisions/${revision}/carry`)
  return expectGeneratedContract("WorkbenchAppCarryResponse", (await workbenchValidators()).validateWorkbenchAppCarryResponse, data)
}

/** The people quotes are with, or were made or saved by. */
export async function fetchAppPeople(): Promise<WorkbenchAppPeopleResponse> {
  const data = await request<unknown>("/api/app/people")
  return expectGeneratedContract("WorkbenchAppPeopleResponse", (await workbenchValidators()).validateWorkbenchAppPeopleResponse, data)
}
