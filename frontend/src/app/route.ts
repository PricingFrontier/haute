/**
 * The underwriter app's routes (specs/workbench): its one page reads where it is from the
 * address, so a quote has a link of its own, a reload stays on it and Back returns to the
 * list as it was. `/` is the Quotes page, its list in the query; `/quotes/new` a new quote,
 * made from another's revision when the query names one; `/quotes/{id}` a quote; and
 * `/quotes/{id}/revisions/{n}` one of its revisions. A leave guard, set while a quote has
 * unsaved values, is asked before the page moves, Back included.
 */
import { useSyncExternalStore, type MouseEvent } from "react"
import type { QuoteRelation, QuotesQuery } from "../api/app"

export type AppRoute =
  | { page: "quotes"; query: QuotesQuery }
  | { page: "new"; from: { quoteId: string; revision: number; relation: QuoteRelation } | null }
  | { page: "quote"; id: string; revision: number | null }
  | { page: "missing" }

const VIEWS = ["mine", "team", "archived"] as const
const STAGES = ["draft", "priced", "final"] as const
const SORTS = ["saved", "reference", "title"] as const
const RELATIONS = ["duplicate", "renewal", "alternative"] as const

const oneOf = <T extends string>(values: readonly T[], value: string | null): T | null =>
  value !== null && (values as readonly string[]).includes(value) ? (value as T) : null

/** The list the Quotes page shows for `search`, a location's query. */
export function quotesQueryOf(search: string): QuotesQuery {
  const params = new URLSearchParams(search)
  return {
    view: oneOf(VIEWS, params.get("view")) ?? "mine",
    stage: oneOf(STAGES, params.get("stage")),
    search: params.get("q") ?? "",
    assignee: params.get("assignee") || null,
    sort: oneOf(SORTS, params.get("sort")) ?? "saved",
  }
}

/** The Quotes page's address for `query`, the defaults left out. */
export function quotesPath(query: QuotesQuery): string {
  const params = new URLSearchParams()
  if (query.view !== "mine") params.set("view", query.view)
  if (query.stage !== null) params.set("stage", query.stage)
  if (query.search !== "") params.set("q", query.search)
  if (query.assignee !== null) params.set("assignee", query.assignee)
  if (query.sort !== "saved") params.set("sort", query.sort)
  const text = params.toString()
  return text === "" ? "/" : `/?${text}`
}

export const quotePath = (id: string): string => `/quotes/${encodeURIComponent(id)}`
export const revisionPath = (id: string, revision: number): string => `${quotePath(id)}/revisions/${revision}`
export const newQuotePath = (from?: { quoteId: string; revision: number; relation: QuoteRelation }): string =>
  from === undefined
    ? "/quotes/new"
    : `/quotes/new?${new URLSearchParams({ from: from.quoteId, revision: String(from.revision), as: from.relation }).toString()}`

/** Where `location` is. */
export function routeOf(location: { pathname: string; search: string }): AppRoute {
  const path = location.pathname.replace(/\/+$/, "") || "/"
  if (path === "/") return { page: "quotes", query: quotesQueryOf(location.search) }
  if (path === "/quotes/new") {
    const params = new URLSearchParams(location.search)
    const quoteId = params.get("from")
    const revision = Number(params.get("revision"))
    const relation = oneOf(RELATIONS, params.get("as"))
    const from = quoteId && Number.isInteger(revision) && revision >= 1 && relation !== null ? { quoteId, revision, relation } : null
    return { page: "new", from }
  }
  const quote = /^\/quotes\/([^/]+)(?:\/revisions\/(\d+))?$/.exec(path)
  if (quote !== null) {
    return { page: "quote", id: decodeURIComponent(quote[1]), revision: quote[2] === undefined ? null : Number(quote[2]) }
  }
  return { page: "missing" }
}

// ── Moving ─────────────────────────────────────────────────────────────────

/** Asked before the page moves: true lets it. */
type LeaveGuard = () => Promise<boolean>

let guard: LeaveGuard | null = null
const listeners = new Set<() => void>()
let current = `${window.location.pathname}${window.location.search}`

const notify = () => {
  current = `${window.location.pathname}${window.location.search}`
  for (const listener of listeners) listener()
}

/** Ask `leaving` before the page moves while it is set; null when nothing would be lost. */
export function setLeaveGuard(leaving: LeaveGuard | null): void {
  guard = leaving
}

/** Move to `path`, asking the leave guard first; false when it kept the page where it was. */
export async function navigate(path: string, { replace = false, force = false } = {}): Promise<boolean> {
  if (path === current) return true
  if (!force && guard !== null && !(await guard())) return false
  if (replace) window.history.replaceState(null, "", path)
  else window.history.pushState(null, "", path)
  notify()
  window.scrollTo?.(0, 0)
  return true
}

/** Back and Forward ask the leave guard too: the page goes back to where it was until the guard lets it move. */
function onPopState(): void {
  const target = `${window.location.pathname}${window.location.search}`
  if (guard === null) {
    notify()
    return
  }
  const leaving = guard
  window.history.pushState(null, "", current)
  void leaving().then((allowed) => {
    if (allowed) void navigate(target, { force: true })
  })
}

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) window.addEventListener("popstate", onPopState)
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) window.removeEventListener("popstate", onPopState)
  }
}

const snapshot = () => current

/** Where the page is, read again whenever it moves. */
export function useAppRoute(): AppRoute {
  const where = useSyncExternalStore(subscribe, snapshot)
  const [pathname, search = ""] = where.split(/(?=\?)/)
  return routeOf({ pathname, search })
}

/** Follow a link inside the app without reloading the page; a press with a modifier opens it as the browser would. */
export function followLink(event: MouseEvent, path: string): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  void navigate(path)
}
