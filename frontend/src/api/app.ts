/**
 * The underwriter app's routes (specs/workbench): the release it serves with who the host
 * signed in (GET /api/app/release), the release's form with its revision
 * (GET /api/app/form), a quote priced through the release's pipeline
 * (POST /api/app/price), which a page drawn for another revision of the form or another
 * release is refused, and, with a quote store, the quotes it keeps (/api/app/quotes), each
 * named by its reference, URL-encoded, and its option.
 */

import type { SampleRow } from "../utils/workbenchForm"
import { expectGeneratedContract } from "../types/generatedContractValidation"
import { post, request } from "./client"
import type {
  WorkbenchAppFormResponse,
  WorkbenchAppPeopleResponse,
  WorkbenchAppPriceRequest,
  WorkbenchAppPriceResponse,
  WorkbenchAppQuoteActivityResponse,
  WorkbenchAppQuoteAmend,
  WorkbenchAppQuoteAssign,
  WorkbenchAppQuoteChange,
  WorkbenchAppQuoteChangeResponse,
  WorkbenchAppQuoteCreateRequest,
  WorkbenchAppQuoteNote,
  WorkbenchAppQuoteResponse,
  WorkbenchAppQuoteSaveRequest,
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
  stage: "draft" | "final" | null
  search: string
  assignee: string | null
  sort: "saved" | "reference" | "insured"
}

/** How a quote is made from another. */
export type QuoteRelation = WorkbenchAppQuoteSource["relation"]

/** The quote a new quote is made from, by its reference and option, and how. */
export type QuoteSource = WorkbenchAppQuoteSource

/** What a creation sends besides its source and reference: the values on the sheets drawn from `form_revision` of `release`, under its operation key. */
export type QuoteCreation = Omit<WorkbenchAppQuoteCreateRequest, "source" | "reference">

/** What a save sends besides the version it is saved over: the values on the sheets drawn from `form_revision` of `release`, and the prices shown for them or null. */
export type QuoteSave = Required<Omit<WorkbenchAppQuoteSaveRequest, "version">>

/** Each change a quote takes besides a save, by its route's name, and the body its route reads. */
interface QuoteChangeBodies {
  assignee: WorkbenchAppQuoteAssign
  archive: WorkbenchAppQuoteChange
  restore: WorkbenchAppQuoteChange
  final: WorkbenchAppQuoteChange
  amend: WorkbenchAppQuoteAmend
  note: WorkbenchAppQuoteNote
}

/** A change a quote takes besides a save, by its route's name. */
export type QuoteChange = keyof QuoteChangeBodies

/** What change `C` sends besides the version it is made on. */
export type QuoteChangeFields<C extends QuoteChange> = Omit<QuoteChangeBodies[C], "version">

/** The quote `reference`/`option`'s route. */
const quoteRoute = (reference: string, option: number): string => `/api/app/quotes/${encodeURIComponent(reference)}/${option}`

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

/**
 * A new quote, made unpriced, under `reference`, the reference the underwriter keyed, made
 * from `source` when given; an alternative names none, taking its source's.
 */
export async function createAppQuote(creation: QuoteCreation, source: QuoteSource | null, reference: string | null): Promise<WorkbenchAppQuoteResponse> {
  const body: WorkbenchAppQuoteCreateRequest = { ...creation, source, reference }
  return quoteResponse(await post<unknown>("/api/app/quotes", body, { timeout: PRICE_TIMEOUT_MS }))
}

/** The quote `reference`/`option`, with what it holds. */
export async function openAppQuote(reference: string, option: number): Promise<WorkbenchAppQuoteResponse> {
  return quoteResponse(await request<unknown>(quoteRoute(reference, option)))
}

/** The quote saved over `version` of it: unpriced while `save.shown` is null, else priced again by the server. */
export async function saveAppQuote(reference: string, option: number, save: QuoteSave, version: number): Promise<WorkbenchAppQuoteResponse> {
  const body: WorkbenchAppQuoteSaveRequest = { ...save, version }
  return quoteResponse(await post<unknown>(`${quoteRoute(reference, option)}/save`, body, { timeout: PRICE_TIMEOUT_MS }))
}

/**
 * A change to the quote at `version`, with what it changes, answered with the quote as it
 * left it. A note moves no version: it is written over whichever the quote is at.
 */
export async function changeAppQuote<C extends QuoteChange>(
  reference: string,
  option: number,
  change: C,
  version: number,
  fields: QuoteChangeFields<C>,
): Promise<WorkbenchAppQuoteChangeResponse> {
  // A note names no version; every other change's fields are its body's but the version.
  const body = (change === "note" ? fields : { ...fields, version }) as QuoteChangeBodies[C]
  const data = await post<unknown>(`${quoteRoute(reference, option)}/${change}`, body)
  return expectGeneratedContract("WorkbenchAppQuoteChangeResponse", (await workbenchValidators()).validateWorkbenchAppQuoteChangeResponse, data)
}

/** What was done to the quote, and the quotes it came from and that were made from it. */
export async function fetchAppQuoteActivity(reference: string, option: number): Promise<WorkbenchAppQuoteActivityResponse> {
  const data = await request<unknown>(`${quoteRoute(reference, option)}/activity`)
  return expectGeneratedContract("WorkbenchAppQuoteActivityResponse", (await workbenchValidators()).validateWorkbenchAppQuoteActivityResponse, data)
}

/** The people quotes are with, or were made, saved or changed by. */
export async function fetchAppPeople(): Promise<WorkbenchAppPeopleResponse> {
  const data = await request<unknown>("/api/app/people")
  return expectGeneratedContract("WorkbenchAppPeopleResponse", (await workbenchValidators()).validateWorkbenchAppPeopleResponse, data)
}
