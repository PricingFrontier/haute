/**
 * The quote open in the underwriter app (specs/workbench): opened at its newest revision or
 * another, read only while it may not change; saved priced when the sheets show a price for
 * the values as they are and unpriced otherwise; a save over a quote moved on, a page of
 * another release and a lost answer each read into what they offer; an alternative made as
 * it is asked for, once though its answer is lost; values moved onto the current sheets; and
 * values kept in the browser. A quote under a new reference is the New quote window's
 * (NewQuoteWindow.test.tsx).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/app", () => ({
  carryAppQuote: vi.fn(),
  changeAppQuote: vi.fn(),
  createAppQuote: vi.fn(),
  fetchAppQuoteHistory: vi.fn(),
  openAppQuote: vi.fn(),
  saveAppQuote: vi.fn(),
  fetchAppPeople: vi.fn(),
}))

import { carryAppQuote, changeAppQuote, createAppQuote, fetchAppQuoteHistory, openAppQuote, saveAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { WorkbenchAppQuoteHistoryResponse, FormSpec, WorkbenchAppQuoteChangeResponse, WorkbenchAppQuoteResponse } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type QuotePricer } from "../../stores/useWorkbenchPricingStore"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import { basisOf, draftKey, readDraft, writeDraft } from "../quoteValues"
import useAppStore from "../useAppStore"
import useOpenQuoteStore, { openDraftKey, readOnlyReason } from "../useOpenQuoteStore"
import { answer, appQuote, changed, release, TABLES, VALUES } from "./fixtures"


const store = () => useOpenQuoteStore.getState()
const preview = () => useWorkbenchPreviewStore.getState()
/** An alternative asked for from the first revision of q1. */
const ALTERNATIVE = { quoteId: "q1", revision: 1, relation: "alternative" } as const
/** An alternative made: the next option under its source's reference. */
const madeAlternative = () => answer({ id: "q3", reference: "Q-000001", option: 2, label: "Q-000001/2", relation: "alternative" })
const stale = (who = "jo") =>
  new ApiError("HTTP 409", 409, "moved", { detail: {} }, { message: `Q-000001 has changed since you opened it: ${who} saved it.`, error_code: "stale_quote_revision", quote: appQuote({ latest: 2 }) })

describe("useOpenQuoteStore", () => {
  let form: FormSpec
  beforeEach(() => {
    form = sheetForm()
    useAppStore.setState({ release, form, revision: "rev-9", people: [], releaseChanged: false })
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false })
    useOpenQuoteStore.setState({ status: "idle", quote: null, revision: null, base: null, past: false, olderSheets: null, pending: null, conflict: null, error: null, historyOpen: false })
  })

  afterEach(async () => {
    // A pricing a test left waiting or running is settled with no pricer, pricing nothing.
    useWorkbenchPricingStore.setState({ quotePricer: null })
    await useWorkbenchPreviewStore.getState().settle()
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it("opens a quote's newest revision on the sheets with its price, and it may change", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())

    await store().openQuote("q1", null)

    expect(store().status).toBe("ready")
    expect(preview().quote).toEqual(VALUES)
    expect(preview().price).toMatchObject({ tables: TABLES, basis: basisOf(form, VALUES) })
    expect(preview().readOnly).toBe(false)
    expect(readOnlyReason(store())).toBeNull()
  })

  it.each([
    ["final", answer({ stage: "final", final_revision: 1 }), null],
    ["archived", answer({ archived: true, archived_at: "2026-10-12T10:00:00+00:00" }), null],
    ["past", answer({ latest: 2 }, { number: 1 }), 1],
    ["older-sheets", answer({}, {}, { form: { ...sheetForm(), name: "older" } }), null],
  ] as const)("holds a %s quote read only", async (reason, opened, revision) => {
    vi.mocked(openAppQuote).mockResolvedValue(opened)

    await store().openQuote("q1", revision)

    expect(readOnlyReason(store())).toBe(reason)
    expect(preview().readOnly).toBe(true)
    expect(await store().save()).toBeNull()
    expect(saveAppQuote).not.toHaveBeenCalled()
    if (reason === "older-sheets") expect(useWorkbenchFormStore.getState().form?.name).toBe("older")
  })

  it("says why a quote could not be opened", async () => {
    vi.mocked(openAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "The app keeps no quote 'q9'."))

    await store().openQuote("q9", null)

    expect(store()).toMatchObject({ status: "error", loadError: "The app keeps no quote 'q9'." })
  })

  it("saves a quote priced when the sheets show a price for the values as they are", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2 }, { number: 2 }))
    await store().openQuote("q1", null)

    await store().save()

    const [id, body, version] = vi.mocked(saveAppQuote).mock.calls[0]
    expect([id, version]).toEqual(["q1", 1])
    expect(body).toMatchObject({ form_revision: "rev-9", release: "rel-1", values: VALUES, shown: TABLES })
    expect(store()).toMatchObject({ saving: false, pending: null })
    expect(store().quote?.latest).toBe(2)
  })

  it("saves a quote unpriced when its price is for other values, and says when the server's price moved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, stage: "draft" }, { number: 2, tables: null }, { moved: true }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    await store().save()

    expect(vi.mocked(saveAppQuote).mock.calls[0][1].shown).toBeNull()
    expect(store().moved).toBe(true)
  })

  it("leaves values typed during a save on the sheets, unsaved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    let release_: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (release_ = resolve)))
    await store().openQuote("q1", null)

    const saving = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    preview().setCell("policy", 0, "policy.state", "CA")
    release_(answer({ latest: 2 }, { number: 2 }))
    await saving

    expect(preview().quote.policy[0]["policy.state"]).toBe("CA")
    expect(store().baseline).not.toBe(JSON.stringify(preview().quote))
    // What was typed during the save is kept in this browser over the revision it saved.
    expect(readDraft(draftKey("motor", "ana@example.com", "q1"))).toMatchObject({ values: { policy: [{ "policy.state": "CA" }] }, version: 2 })
  })

  it("saves once the quote's pricing settles, keeping the price it settles on", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2 }, { number: 2 }))
    const tables = { premiums: [{ item: "Crane", premium: 9 }] }
    useWorkbenchPricingStore.setState({ quotePricer: vi.fn<QuotePricer>(() => Promise.resolve({ tables, sample: {} })) })
    await store().openQuote("q1", null)

    // Typed and saved at once: the save waits for the pricing the typing asked for.
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    expect(saveAppQuote).toHaveBeenCalledWith("q1", expect.objectContaining({ values: { policy: [{ "policy.state": "CA" }] }, shown: tables }), 1)
  })

  it("opens a quote that may change, priced on another release, to be priced again on this one", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ release: "rel-0" }, { release: "rel-0" }))
    const tables = { premiums: [{ item: "Crane", premium: 130 }] }
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve({ tables, sample: {} }))
    useWorkbenchPricingStore.setState({ quotePricer })

    await store().openQuote("q1", null)
    expect(preview().price).toBeNull()
    await preview().settle()

    expect(quotePricer).toHaveBeenCalledWith(form, VALUES)
    expect(preview().price).toMatchObject({ tables, basis: basisOf(form, VALUES) })
  })

  it("reads a save over a quote moved on into an offer, keeping the values", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    expect(await store().save()).toBeNull()

    expect(store().conflict).toEqual({ message: "Q-000001 has changed since you opened it: jo saved it.", latest: expect.objectContaining({ latest: 2 }) })
    expect(preview().quote.policy[0]["policy.state"]).toBe("CA")
  })

  it("reads a page drawn for another release as one to reload", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "This page was drawn for another release of the app: reload it."))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    await store().save()

    expect(useAppStore.getState().releaseChanged).toBe(true)
  })

  it("keeps a save's operation key after a lost answer or a server failure, and lets it go on a refusal", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError("HTTP 504", 504, "Gateway timeout"))
      .mockRejectedValueOnce(new ApiError("HTTP 422", 422, "No"))
      .mockResolvedValue(answer({ latest: 2 }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    await store().save()
    expect(store().error).toContain("Failed to fetch")
    await store().save()
    await store().save()
    await store().save()

    const keys = vi.mocked(saveAppQuote).mock.calls.map((call) => call[1].operation)
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).toBe(keys[0])
    expect(keys[3]).not.toBe(keys[2])
  })

  it("repeats a save whose answer was lost before saving what was typed since, each written once", async () => {
    const typed = (state: string) => ({ policy: [{ "policy.state": state }] })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(answer({ latest: 2 }, { number: 2, values: typed("CA") }))
      .mockResolvedValueOnce(answer({ latest: 3 }, { number: 3, values: typed("TX") }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    preview().setCell("policy", 0, "policy.state", "TX")

    await store().save()

    const calls = vi.mocked(saveAppQuote).mock.calls
    // The lost save sent again exactly, then the values typed since over the revision it wrote.
    expect(calls[1]).toEqual(calls[0])
    expect([calls[2][1].values, calls[2][2]]).toEqual([typed("TX"), 2])
    expect(calls[2][1].operation).not.toBe(calls[0][1].operation)
    expect(store()).toMatchObject({ pending: null, base: 3 })
    expect(preview().quote).toEqual(typed("TX"))
  })

  it("lets a lost save go when sent again and refused, and saves what is on the sheets", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError("HTTP 422", 422, "No"))
      .mockResolvedValueOnce(answer({ latest: 2 }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    preview().setCell("policy", 0, "policy.state", "TX")

    await store().save()

    const calls = vi.mocked(saveAppQuote).mock.calls
    expect(calls).toHaveLength(3)
    // It never landed, so the values are saved over the version they were edited over.
    expect([calls[2][1].values, calls[2][2]]).toEqual([{ policy: [{ "policy.state": "TX" }] }, 1])
    expect(calls[2][1].operation).not.toBe(calls[0][1].operation)
    expect(store()).toMatchObject({ pending: null, error: null })
  })

  it("saves nothing while nothing is typed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ priced: false, stage: "draft" }, { values: {}, tables: null }))
    await store().openQuote("q1", null)

    expect(await store().save()).toBeNull()

    expect(saveAppQuote).not.toHaveBeenCalled()
  })

  it("leaves the sheets of a quote shown at an earlier revision as they are when a change writes one", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ latest: 2 }, { number: 1 }))
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ latest: 3, archived: true }, { number: 3, values: { policy: [{ "policy.state": "CA" }] } }))
    await store().openQuote("q1", 1)

    await store().change("archive", {})

    expect(store().quote?.latest).toBe(3)
    expect([store().base, store().revision?.number]).toEqual([null, 1])
    expect(preview().quote).toEqual(VALUES)
  })

  it("lets a save held after a lost answer go once a change writes the next revision", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch"))
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ latest: 2 }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    expect(store().pending).not.toBeNull()

    await store().change("archive", {})

    // It named version 1, which the change moved on from: it never landed, and never will.
    expect(store().pending).toBeNull()
  })

  it("asks again with the same key after a reload before the answer, and with a new key once the alternative is made", async () => {
    vi.mocked(carryAppQuote).mockResolvedValue({ values: VALUES, dropped: [] })
    vi.mocked(createAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(madeAlternative())

    expect(await store().openNew(ALTERNATIVE)).toBeNull()
    expect(store()).toMatchObject({ status: "error", loadError: "Failed to fetch" })
    // The page is reloaded before an answer came: asked with the key held, so it is made once.
    store().close()
    expect((await store().openNew(ALTERNATIVE))?.id).toBe("q3")
    // Asked for again once made: another option, under a key of its own.
    await store().openNew(ALTERNATIVE)

    const keys = vi.mocked(createAppQuote).mock.calls.map(([body]) => body.operation)
    expect(keys).toHaveLength(3)
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[1])
  })

  it("lets a refused alternative's key go, so the next asked for is asked anew", async () => {
    vi.mocked(carryAppQuote).mockResolvedValue({ values: VALUES, dropped: [] })
    vi.mocked(createAppQuote).mockRejectedValueOnce(new ApiError("HTTP 422", 422, "No")).mockResolvedValue(madeAlternative())

    await store().openNew(ALTERNATIVE)
    expect(store().loadError).toBe("No")
    await store().openNew(ALTERNATIVE)

    const [first, second] = vi.mocked(createAppQuote).mock.calls.map(([body]) => body.operation)
    expect(second).not.toBe(first)
  })

  it("holds an alternative's key apart from one of another revision", async () => {
    vi.mocked(carryAppQuote).mockResolvedValue({ values: VALUES, dropped: [] })
    vi.mocked(createAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(madeAlternative())

    await store().openNew({ ...ALTERNATIVE, revision: 2 })
    await store().openNew(ALTERNATIVE)

    const [other, alternative] = vi.mocked(createAppQuote).mock.calls
    expect(alternative[1]).toEqual({ quote_id: "q1", revision: 1, relation: "alternative" })
    expect(alternative[0].operation).not.toBe(other[0].operation)
    expect(store().quote?.label).toBe("Q-000001/2")
  })

  it("makes no change while a save is being made, and no save while a change is", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    let saving_: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (saving_ = resolve)))
    let changing_: (value: WorkbenchAppQuoteChangeResponse) => void = () => {}
    vi.mocked(changeAppQuote).mockReturnValue(new Promise((resolve) => (changing_ = resolve)))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    const saving = store().save()
    expect(await store().change("archive", {})).toBeNull()
    expect(changeAppQuote).not.toHaveBeenCalled()
    saving_(answer({ latest: 2 }, { number: 2, values: { policy: [{ "policy.state": "CA" }] } }))
    await saving

    const changing = store().change("archive", {})
    preview().setCell("policy", 0, "policy.state", "TX")
    expect(await store().save()).toBeNull()
    expect(saveAppQuote).toHaveBeenCalledTimes(1)
    changing_(changed({ latest: 3, archived: true }, { number: 3 }))
    await changing
  })

  it("keeps what is typed before and during a change that holds the quote read only", async () => {
    let archiving: (value: WorkbenchAppQuoteChangeResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote).mockReturnValue(new Promise((resolve) => (archiving = resolve)))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    const change = store().change("archive", {})
    preview().setCell("policy", 0, "policy.year", "2028")
    archiving(changed({ latest: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }))
    await change

    expect(preview().readOnly).toBe(true)
    expect(readDraft(draftKey("motor", "ana@example.com", "q1"))?.values).toEqual({ policy: [{ "policy.state": "CA", "policy.year": "2028" }] })
  })

  it("offers back what was kept of a quote archived with typing once it is restored, and keeps it", async () => {
    const key = draftKey("motor", "ana@example.com", "q1")
    const archived = { latest: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer(archived))
    vi.mocked(changeAppQuote).mockResolvedValueOnce(changed(archived)).mockResolvedValueOnce(changed({ latest: 3 }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().change("archive", {})
    store().close()

    await store().openQuote("q1", null)
    expect(store().recovered).toBeNull()
    await store().change("restore", {})
    expect(store().recovered?.values).toEqual({ policy: [{ "policy.state": "CA" }] })
    store().close()

    expect(readDraft(key)?.values).toEqual({ policy: [{ "policy.state": "CA" }] })
  })

  it("keeps nothing and saves nothing while a quote is being opened, the quote before off the sheets", async () => {
    const fresh = draftKey("motor", "ana@example.com", "new")
    writeDraft(fresh, { values: { policy: [{ "policy.state": "UNSAVED-NEW" }] }, version: null, latest: null, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    let reading: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockReturnValueOnce(new Promise((resolve) => (reading = resolve)))
    await store().openQuote("q1", null)

    const opening = store().openQuote("q2", null)
    expect(preview()).toMatchObject({ quote: {}, readOnly: true })
    expect(store().keepDraft()).toBe(true)
    expect(await store().save()).toBeNull()
    reading(answer({ id: "q2", reference: "Q-000002" }))
    await opening

    expect(readDraft(fresh)?.values).toEqual({ policy: [{ "policy.state": "UNSAVED-NEW" }] })
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("drops a save's answer once the quote is left, keeping what was typed", async () => {
    let saving_: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (saving_ = resolve)))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    const saving = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    preview().setCell("policy", 0, "policy.year", "2027")

    store().close()
    saving_(answer({ latest: 2 }, { number: 2 }))

    expect(await saving).toBeNull()
    expect(store()).toMatchObject({ status: "idle", quote: null })
    expect(readDraft(draftKey("motor", "ana@example.com", "q1"))?.values).toEqual({ policy: [{ "policy.state": "CA", "policy.year": "2027" }] })
  })

  it("makes an alternative from another's revision as carried, under its source's reference, and opens it", async () => {
    const details = { qd_insured: "Acme", qd_is_renewal: false }
    vi.mocked(carryAppQuote).mockResolvedValue({ values: { ...VALUES, quote_details: [details] }, dropped: ["policy.gone"] })
    vi.mocked(createAppQuote).mockResolvedValue(madeAlternative())

    const quote = await store().openNew(ALTERNATIVE)

    expect(quote?.label).toBe("Q-000001/2")
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect(source).toEqual({ quote_id: "q1", revision: 1, relation: "alternative" })
    // It names no reference of its own: it takes its source's, and the next option under it.
    expect([body.values, body.shown, reference]).toEqual([{ ...VALUES, quote_details: [details] }, null, null])
    // Carried without the details the app set for the source, which are not the new quote's.
    expect(vi.mocked(carryAppQuote).mock.calls).toEqual([["q1", 1]])
    expect(store()).toMatchObject({ status: "ready", base: 1, dropped: ["policy.gone"], quote: expect.objectContaining({ id: "q3" }) })
    expect(preview().readOnly).toBe(false)
  })

  it("says why a quote to make one from could not be read", async () => {
    vi.mocked(carryAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "No such quote"))

    await store().openNew({ ...ALTERNATIVE, quoteId: "q9" })

    expect(store()).toMatchObject({ status: "error", loadError: "No such quote" })
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("moves a quote on earlier sheets onto the current ones as unsaved values", async () => {
    const older = { ...sheetForm(), name: "older" }
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, {}, { form: older }))
    vi.mocked(carryAppQuote).mockResolvedValue({ values: { policy: [{ "policy.state": "CA" }] }, dropped: ["policy.old"] })
    await store().openQuote("q1", null)

    await store().moveToCurrentSheets()

    // The same quote: carried with the details the app set for it.
    expect(carryAppQuote).toHaveBeenCalledWith("q1", 1, true)
    expect(useWorkbenchFormStore.getState().form).toBe(form)
    expect(store()).toMatchObject({ olderSheets: null, dropped: ["policy.old"] })
    expect(preview()).toMatchObject({ readOnly: false, price: null, quote: { policy: [{ "policy.state": "CA" }] } })
  })

  it("offers back values this browser kept, restores them or discards them", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const key = draftKey("motor", "ana@example.com", "q1")
    writeDraft(key, { values: { policy: [{ "policy.state": "CA" }] }, version: 1, latest: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    await store().openQuote("q1", null)
    expect(openDraftKey(store())).toBe(key)
    expect(store().recovered?.values).toEqual({ policy: [{ "policy.state": "CA" }] })

    store().restoreDraft()
    expect(preview().quote).toEqual({ policy: [{ "policy.state": "CA" }] })

    await store().openQuote("q1", null)
    store().discardDraft()
    expect(readDraft(key)).toBeNull()
    expect(store().recovered).toBeNull()
  })

  it("offers back no draft on a quote that may not change, and puts back only what fits the sheets", async () => {
    const key = draftKey("motor", "ana@example.com", "q1")
    const kept = { policy: [{ "policy.state": "CA", "policy.gone": "x" }], removed: [{ c: "1" }], quote_details: [{ qd_stage: "draft", qd_insured: "Acme" }] }
    writeDraft(key, { values: kept, version: 1, latest: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ stage: "final", final_revision: 1 }))
    await store().openQuote("q1", null)
    expect(store().recovered).toBeNull()
    // Offered while it could change, then held read only, it is not put back.
    useOpenQuoteStore.setState({ recovered: { values: kept, version: 1, latest: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null } })
    store().restoreDraft()
    expect(preview().quote).toEqual(VALUES)
    expect(preview().readOnly).toBe(true)

    const details = { qd_reference: "Q-000001", qd_stage: "priced" }
    const sheets = { ...form, schema: { tables: [detailsTable(), ...form.schema.tables] } }
    useAppStore.setState({ form: sheets })
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({}, { values: { ...VALUES, quote_details: [details] } }))
    await store().openQuote("q1", null)
    store().restoreDraft()
    expect(preview().quote).toEqual({ policy: [{ "policy.state": "CA" }], quote_details: [{ qd_insured: "Acme", ...details }] })
  })

  it("names what a restored draft holds that has no place on the sheets, as the sheets it was typed on named it", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const key = draftKey("motor", "ana@example.com", "q1")
    const values = { policy: [{ "policy.state": "CA", "policy.gone": "x" }], removed: [{ c: "1" }] }
    const places = { "policy:policy.state": "policy.state", "policy:policy.gone": "policy.cover", "removed:c": "extras.note" }
    writeDraft(key, { values, version: 1, latest: 1, places, keptAt: "2026-10-12T10:00:00Z", pending: null })
    await store().openQuote("q1", null)

    store().restoreDraft()

    expect(preview().quote).toEqual({ policy: [{ "policy.state": "CA" }] })
    expect(store().dropped).toEqual(["policy.cover", "extras.note"])
  })

  it("keeps where each value typed sits on the sheets with the draft", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")

    expect(store().keepDraft()).toBe(true)

    const kept = readDraft(draftKey("motor", "ana@example.com", "q1"))
    expect(Object.keys(kept?.places ?? {})).toContain("policy:policy.state")
  })

  it("offers back no draft already on the sheet, as after an archive and a restore with the typing still there", async () => {
    const archived = { latest: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer())
    vi.mocked(changeAppQuote).mockResolvedValueOnce(changed(archived)).mockResolvedValueOnce(changed({ latest: 3 }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    store().keepDraft()

    await store().change("archive", {})
    await store().change("restore", {})

    expect(preview().quote.policy?.[0]["policy.state"]).toBe("CA")
    expect(store().recovered).toBeNull()
  })

  it("does not offer back a draft the same as what is saved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    writeDraft(draftKey("motor", "ana@example.com", "q1"), { values: VALUES, version: 1, latest: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })

    await store().openQuote("q1", null)

    expect(store().recovered).toBeNull()
  })

  it("changes the quote over the revision it wrote, holding it read only as the change leaves it", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ stage: "final", final_revision: 2, latest: 2 }))
      .mockResolvedValueOnce(changed({ latest: 3 }))
    await store().openQuote("q1", null)

    await store().change("final", { revision: 1 })
    expect(preview().readOnly).toBe(true)
    expect([store().base, store().revision?.number]).toEqual([2, 2])
    await store().change("reopen", { reason: "Broker" })
    expect(preview().readOnly).toBe(false)
    expect(vi.mocked(changeAppQuote).mock.calls[1]).toEqual(["q1", "reopen", 2, { reason: "Broker" }])
    // The revision the reopening wrote is the one the values are edited over now.
    expect([store().base, store().revision?.number, store().baseline]).toEqual([3, 3, JSON.stringify(VALUES)])
  })

  it("changes only the quote a change was asked about, leaving another opened by the answer alone", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ id: "q2", reference: "Q-000002" }))
    await store().openQuote("q1", null)
    const asked = store().quote
    // While "Mark Q-000001 final?" was open, Back opened another quote at the same revision.
    await store().openQuote("q2", null)

    expect(await store().change("final", { revision: 1 }, asked ?? undefined)).toBeNull()

    expect(changeAppQuote).not.toHaveBeenCalled()
    expect(store().error).toContain("Q-000001")
  })

  it("keeps a note from moving the version a save names, so a save over a colleague's is refused", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    // Jo saved revision 2 meanwhile: the note's answer is the quote as it is now.
    vi.mocked(changeAppQuote).mockResolvedValue({ quote: appQuote({ latest: 2, note: "Call the broker" }), revision: null })
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    await store().openQuote("q1", null)

    await store().change("note", { note: "Call the broker" })
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    expect(store().quote?.note).toBe("Call the broker")
    expect(vi.mocked(saveAppQuote).mock.calls[0][2]).toBe(1)
    expect(store().conflict?.message).toContain("jo saved it")
  })

  it("drops a save's or a change's answer once another quote was opened, keeping that one", async () => {
    let saving: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ id: "q2", reference: "Q-000002" }, { values: { policy: [{ "policy.state": "TX" }] } }))
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (saving = resolve)))
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ latest: 2, assignee: "jo@example.com" }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    writeDraft(draftKey("motor", "ana@example.com", "q1"), { values: preview().quote, version: 1, latest: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    const saved = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    const assigning = store().change("assignee", { assignee: "jo@example.com" })
    await store().openQuote("q2", null)

    saving(answer({ latest: 2 }, { number: 2 }))
    expect(await saved).toBeNull()
    await assigning

    expect(store()).toMatchObject({ quote: expect.objectContaining({ id: "q2" }), base: 1, saving: false })
    expect(preview().quote).toEqual({ policy: [{ "policy.state": "TX" }] })
    // The first quote's save landed: what this browser kept of it, as saved, is dropped.
    expect(readDraft(draftKey("motor", "ana@example.com", "q1"))).toBeNull()
  })

  it("reads a change over a quote moved on into an offer", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote).mockRejectedValue(stale())
    await store().openQuote("q1", null)

    expect(await store().change("archive", {})).toBeNull()
    expect(store().conflict?.latest?.latest).toBe(2)
    store().dismiss("conflict")
    expect(store().conflict).toBeNull()
  })

  it("reads the history while it is open, and says when it could not", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(fetchAppQuoteHistory).mockResolvedValueOnce({ revisions: [], events: [], source: null, derived: [] }).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "down"))
    await store().openQuote("q1", null)

    store().setHistoryOpen(true)
    await vi.waitFor(() => expect(store().history).not.toBeNull())
    await store().loadHistory()
    expect(store().error).toContain("history could not be read")
  })

  it("shows the history asked for last, whichever answers first", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const answers: Array<(value: WorkbenchAppQuoteHistoryResponse) => void> = []
    vi.mocked(fetchAppQuoteHistory).mockImplementation(() => new Promise((resolve) => { answers.push(resolve) }))
    await store().openQuote("q1", null)
    const history = (numbers: number[]): WorkbenchAppQuoteHistoryResponse => ({
      revisions: numbers.map((number) => ({ number, saved_by: "ana@example.com", saved_at: "2026-10-12T09:00:00+00:00", priced: true, release: "r1", form_revision: "rev-9", stage: "priced" })),
      events: [],
      source: null,
      derived: [],
    })

    const before = store().loadHistory()
    const after = store().loadHistory()
    answers[1](history([1, 2]))
    await after
    answers[0](history([1]))
    await before

    expect(store().history?.revisions.map((revision) => revision.number)).toEqual([1, 2])
  })

  it("says nothing of an earlier history read that fails after a later one answered", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const settles: Array<{ resolve: (value: WorkbenchAppQuoteHistoryResponse) => void; reject: (error: Error) => void }> = []
    vi.mocked(fetchAppQuoteHistory).mockImplementation(() => new Promise((resolve, reject) => { settles.push({ resolve, reject }) }))
    await store().openQuote("q1", null)

    const before = store().loadHistory()
    const after = store().loadHistory()
    settles[1].resolve({ revisions: [], events: [], source: null, derived: [] })
    await after
    settles[0].reject(new Error("down"))
    await before

    expect(store().history).not.toBeNull()
    expect(store().error).toBeNull()
  })

  it("keeps the newest open when an earlier one answers last", async () => {
    let early: (value: WorkbenchAppQuoteResponse) => void = () => {}
    let earlyFail: (reason: unknown) => void = () => {}
    vi.mocked(openAppQuote)
      .mockReturnValueOnce(new Promise((resolve) => (early = resolve)))
      .mockReturnValueOnce(new Promise((_resolve, reject) => (earlyFail = reject)))
      .mockResolvedValueOnce(answer({ id: "q3", reference: "Q-000003" }))
    const first = store().openQuote("q1", null)
    const second = store().openQuote("q2", null)
    await store().openQuote("q3", null)
    early(answer())
    earlyFail(new ApiError("HTTP 404", 404, "gone"))
    await Promise.allSettled([first, second])

    expect(store()).toMatchObject({ status: "ready", quote: expect.objectContaining({ id: "q3" }) })
  })

  it("makes no quote once another is opened before the one it is made from answers", async () => {
    let carried: (value: { values: Record<string, never>; dropped: string[] }) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ id: "q3" }))
    vi.mocked(carryAppQuote).mockReturnValueOnce(new Promise((resolve) => (carried = resolve)))
    const making = store().openNew(ALTERNATIVE)
    await store().openQuote("q3", null)
    carried({ values: {}, dropped: ["x.y"] })

    expect(await making).toBeNull()
    expect(store().quote?.id).toBe("q3")
    expect(store().dropped).toEqual([])
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("does nothing with no quote open, and nothing with no release read", async () => {
    expect(await store().change("archive", {})).toBeNull()
    await store().moveToCurrentSheets()
    store().restoreDraft()
    await store().loadHistory()
    useAppStore.setState({ release: null, form: null })
    expect(openDraftKey(store())).toBeNull()
    await store().openNew(ALTERNATIVE)
    expect(changeAppQuote).not.toHaveBeenCalled()
    expect(carryAppQuote).not.toHaveBeenCalled()
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("reads the history again as an open quote is opened, saved and changed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2 }, { number: 2 }))
    vi.mocked(changeAppQuote).mockResolvedValue({ quote: appQuote({ latest: 3 }), revision: null })
    vi.mocked(fetchAppQuoteHistory).mockResolvedValue({ revisions: [], events: [], source: null, derived: [] })
    useOpenQuoteStore.setState({ historyOpen: true })

    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    await store().change("note", { note: "x" })

    await vi.waitFor(() => expect(fetchAppQuoteHistory).toHaveBeenCalledTimes(3))
  })

  it("says why values could not be moved onto the current sheets", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, {}, { form: { ...sheetForm(), name: "older" } }))
    vi.mocked(carryAppQuote).mockRejectedValue(new ApiError("HTTP 500", 500, "carry failed"))
    await store().openQuote("q1", null)

    await store().moveToCurrentSheets()
    expect(store().error).toBe("carry failed")
  })

  it("dismisses each notice", () => {
    useOpenQuoteStore.setState({ error: "e", moved: true, dropped: ["a.b"], conflict: { message: "m", latest: null } })
    store().dismiss("error")
    store().dismiss("moved")
    store().dismiss("dropped")
    store().dismiss("conflict")
    expect(store()).toMatchObject({ error: null, moved: false, dropped: [], conflict: null })
  })

  it("holds every quote read only for one who may only read quotes, and changes nothing", async () => {
    useAppStore.setState({ release: { ...release, can_edit: false, edit_role: "Quotes.Edit" } })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await store().openQuote("q1", null)

    expect(readOnlyReason(store())).toBe("reader")
    expect(preview().readOnly).toBe(true)
    expect(await store().save()).toBeNull()
    expect(await store().change("archive", {})).toBeNull()
    await store().openNew(ALTERNATIVE)
    expect(preview().readOnly).toBe(true)
    expect(store().loadError).toBe("Making a quote needs the app's edit role.")
    expect(changeAppQuote).not.toHaveBeenCalled()
    expect(createAppQuote).not.toHaveBeenCalled()
  })
})
