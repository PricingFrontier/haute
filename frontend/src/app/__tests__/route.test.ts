/**
 * The underwriter app's routes (specs/workbench): where the address says the page is, the
 * Quotes page's list in the query, and the leave guard asked before the page moves, Back
 * included.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { navigate, newQuotePath, quotePath, quotesPath, quotesQueryOf, routeOf, setLeaveGuard, useAppRoute } from "../route"

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
  it("reads the Quotes page with its list, a new quote with what it is made from, and a quote by its reference and option", () => {
    expect(at("/")).toEqual({ page: "quotes", query: { view: "mine", stage: null, search: "", assignee: null, sort: "saved" } })
    expect(at("/?view=team&stage=final&q=acme&assignee=jo%40example.com&sort=insured")).toEqual({
      page: "quotes",
      query: { view: "team", stage: "final", search: "acme", assignee: "jo@example.com", sort: "insured" },
    })
    expect(at("/quotes/new")).toEqual({ page: "new", from: null })
    expect(at("/quotes/new?from=ABC+1&option=2&as=renewal")).toEqual({ page: "new", from: { reference: "ABC 1", option: 2, relation: "renewal" } })
    expect(at("/quotes/ABC-1/2/")).toEqual({ page: "quote", reference: "ABC-1", option: 2 })
    // The reference decoded, as the address encodes it.
    expect(at("/quotes/caf%C3%A9%20%231/1")).toEqual({ page: "quote", reference: "café #1", option: 1 })
    // A quote whose reference is "new" is one of its own.
    expect(at("/quotes/new/1")).toEqual({ page: "quote", reference: "new", option: 1 })
    expect(at("/elsewhere")).toEqual({ page: "missing" })
  })

  it("reads an address naming no quote as missing, and a list or an origin it does not know as the default", () => {
    for (const address of ["/quotes/abc", "/quotes/abc/0", "/quotes/abc/x", "/quotes/abc/revisions/3", "/quotes/%E0%A4%A/1"]) {
      expect(at(address)).toEqual({ page: "missing" })
    }
    expect(at("/?view=nobody&stage=priced&sort=random")).toMatchObject({ page: "quotes", query: { view: "mine", stage: null, sort: "saved" } })
    expect(at("/quotes/new?from=abc&option=0&as=renewal")).toEqual({ page: "new", from: null })
    expect(at("/quotes/new?from=abc&option=1&as=copy")).toEqual({ page: "new", from: null })
  })

  it("writes each address back as it reads it, the defaults left out", () => {
    const query = quotesQueryOf("?view=archived&q=fleet")
    expect(quotesPath(query)).toBe("/?view=archived&q=fleet")
    expect(quotesPath(quotesQueryOf(""))).toBe("/")
    const from = { reference: "abc 1", option: 2, relation: "alternative" } as const
    expect(newQuotePath(from)).toBe("/quotes/new?from=abc+1&option=2&as=alternative")
    expect(at(newQuotePath(from))).toEqual({ page: "new", from })
    expect(quotePath("a b/c", 4)).toBe("/quotes/a%20b%2Fc/4")
    expect(at(quotePath("café #1", 3))).toEqual({ page: "quote", reference: "café #1", option: 3 })
  })
})

describe("navigate", () => {
  afterEach(async () => {
    setLeaveGuard(null)
    await navigate("/", { force: true })
  })

  it("moves the page and tells whoever reads the route, and says when it was there already", async () => {
    let reads = 0
    const { result } = renderHook(() => {
      reads += 1
      return useAppRoute()
    })
    await act(async () => {
      expect(await navigate("/quotes/abc/1")).toBe("moved")
    })
    expect(window.location.pathname).toBe("/quotes/abc/1")
    expect(result.current).toEqual({ page: "quote", reference: "abc", option: 1 })

    const before = reads
    const entries = window.history.length
    await act(async () => {
      expect(await navigate("/quotes/abc/1")).toBe("here")
    })
    // Nothing that follows the address hears of it.
    expect(reads).toBe(before)
    expect(window.history.length).toBe(entries)
  })

  it("asks the leave guard first, and stays when it says no, unless forced", async () => {
    await navigate("/quotes/abc/1")
    const guard = vi.fn(async () => false)
    setLeaveGuard(guard)

    expect(await navigate("/")).toBe("kept")
    expect(window.location.pathname).toBe("/quotes/abc/1")
    expect(await navigate("/", { force: true })).toBe("moved")
    expect(window.location.pathname).toBe("/")
    expect(guard).toHaveBeenCalledTimes(1)
  })

  it("puts Back's page back while the guard is asked, and goes once it says yes, the history as it was", async () => {
    renderHook(() => useAppRoute())
    await navigate("/")
    await navigate("/quotes/abc/1")
    const entries = window.history.length
    let answer: (yes: boolean) => void = () => {}
    const asked = vi.fn(() => new Promise<boolean>((resolve) => (answer = resolve)))
    setLeaveGuard(asked)
    const made = traversals()

    // Back, then the app's own way back to where the page was while it asks.
    window.history.back()
    await act(async () => vi.waitFor(() => expect(made()).toBe(2)))
    expect(asked).toHaveBeenCalledTimes(1)
    expect(window.location.pathname).toBe("/quotes/abc/1")

    answer(true)
    await act(async () => vi.waitFor(() => expect(made()).toBe(3)))
    expect(window.location.pathname).toBe("/")
    expect(window.history.length).toBe(entries)

    // Forward leads where it did, the guard gone once the page left.
    setLeaveGuard(null)
    window.history.forward()
    await act(async () => vi.waitFor(() => expect(made()).toBe(4)))
    expect(window.location.pathname).toBe("/quotes/abc/1")
    expect(asked).toHaveBeenCalledTimes(1)
  })
})
