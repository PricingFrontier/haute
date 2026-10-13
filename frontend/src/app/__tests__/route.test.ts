/**
 * The underwriter app's routes (specs/workbench): where the address says the page is, the
 * Quotes page's list in the query, and the leave guard asked before the page moves, Back
 * included.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { navigate, newQuotePath, quotesPath, quotesQueryOf, revisionPath, routeOf, setLeaveGuard, useAppRoute } from "../route"

const at = (path: string) => {
  const [pathname, search = ""] = path.split(/(?=\?)/)
  return routeOf({ pathname, search })
}

/** The history traversals made since, each a popstate, which the browser fires after the task that asks it. */
function traversals(): () => number {
  let made = 0
  window.addEventListener("popstate", () => (made += 1))
  return () => made
}

describe("routeOf", () => {
  it("reads the Quotes page with its list, a new quote with what it is made from, a quote and a revision", () => {
    expect(at("/")).toEqual({ page: "quotes", query: { view: "mine", stage: null, search: "", assignee: null, sort: "saved" } })
    expect(at("/?view=team&stage=final&q=acme&assignee=jo%40example.com&sort=insured")).toEqual({
      page: "quotes",
      query: { view: "team", stage: "final", search: "acme", assignee: "jo@example.com", sort: "insured" },
    })
    expect(at("/quotes/new")).toEqual({ page: "new", from: null })
    expect(at("/quotes/new?from=abc&revision=2&as=renewal")).toEqual({ page: "new", from: { quoteId: "abc", revision: 2, relation: "renewal" } })
    expect(at("/quotes/abc")).toEqual({ page: "quote", id: "abc", revision: null })
    expect(at("/quotes/abc/revisions/3/")).toEqual({ page: "quote", id: "abc", revision: 3 })
    expect(at("/elsewhere")).toEqual({ page: "missing" })
  })

  it("reads a list or an origin it does not know as the default", () => {
    expect(at("/?view=nobody&stage=gone&sort=random")).toMatchObject({ page: "quotes", query: { view: "mine", stage: null, sort: "saved" } })
    expect(at("/quotes/new?from=abc&revision=0&as=renewal")).toEqual({ page: "new", from: null })
    expect(at("/quotes/new?from=abc&revision=1&as=copy")).toEqual({ page: "new", from: null })
  })

  it("writes each address back as it reads it, the defaults left out", () => {
    const query = quotesQueryOf("?view=archived&q=fleet")
    expect(quotesPath(query)).toBe("/?view=archived&q=fleet")
    expect(quotesPath(quotesQueryOf(""))).toBe("/")
    expect(newQuotePath({ quoteId: "abc", revision: 2, relation: "alternative" })).toBe("/quotes/new?from=abc&revision=2&as=alternative")
    expect(revisionPath("a b", 4)).toBe("/quotes/a%20b/revisions/4")
  })
})

describe("navigate", () => {
  afterEach(async () => {
    setLeaveGuard(null)
    await navigate("/", { force: true })
  })

  it("moves the page and tells whoever reads the route", async () => {
    const { result } = renderHook(() => useAppRoute())
    await act(async () => {
      expect(await navigate("/quotes/abc")).toBe(true)
    })
    expect(window.location.pathname).toBe("/quotes/abc")
    expect(result.current).toEqual({ page: "quote", id: "abc", revision: null })
  })

  it("asks the leave guard first, and stays when it says no, unless forced", async () => {
    await navigate("/quotes/abc")
    const guard = vi.fn(async () => false)
    setLeaveGuard(guard)

    expect(await navigate("/")).toBe(false)
    expect(window.location.pathname).toBe("/quotes/abc")
    expect(await navigate("/", { force: true })).toBe(true)
    expect(window.location.pathname).toBe("/")
    expect(guard).toHaveBeenCalledTimes(1)
  })

  it("puts Back's page back while the guard is asked, and goes once it says yes, the history as it was", async () => {
    renderHook(() => useAppRoute())
    await navigate("/")
    await navigate("/quotes/abc")
    const entries = window.history.length
    let answer: (yes: boolean) => void = () => {}
    const asked = vi.fn(() => new Promise<boolean>((resolve) => (answer = resolve)))
    setLeaveGuard(asked)
    const made = traversals()

    // Back, then the app's own way back to where the page was while it asks.
    window.history.back()
    await act(async () => vi.waitFor(() => expect(made()).toBe(2)))
    expect(asked).toHaveBeenCalledTimes(1)
    expect(window.location.pathname).toBe("/quotes/abc")

    answer(true)
    await act(async () => vi.waitFor(() => expect(made()).toBe(3)))
    expect(window.location.pathname).toBe("/")
    expect(window.history.length).toBe(entries)

    // Forward leads where it did, the guard gone once the page left.
    setLeaveGuard(null)
    window.history.forward()
    await act(async () => vi.waitFor(() => expect(made()).toBe(4)))
    expect(window.location.pathname).toBe("/quotes/abc")
    expect(asked).toHaveBeenCalledTimes(1)
  })
})
