/**
 * The quote open in the underwriter app (specs/workbench): opened at its newest revision or
 * another, read only while it may not change; saved priced when the sheets show a price for
 * the values as they are and unpriced otherwise; a save over a quote moved on, a page of
 * another release and a lost answer each read into what they offer; a new quote made from
 * another's revision; values moved onto the current sheets; and values kept in the browser.
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
import { basisOf, draftKey, readDraft, valuesKey, writeDraft } from "../quoteValues"
import useAppStore from "../useAppStore"
import useOpenQuoteStore, { openDraftKey, readOnlyReason } from "../useOpenQuoteStore"
import { answer, appQuote, changed, release, TABLES, VALUES } from "./fixtures"


const store = () => useOpenQuoteStore.getState()
const preview = () => useWorkbenchPreviewStore.getState()
const stale = (who = "jo") =>
  new ApiError("HTTP 409", 409, "moved", { detail: {} }, { message: `Q-000001 has changed since you opened it: ${who} saved it.`, error_code: "stale_quote_revision", quote: appQuote({ version: 3, latest: 2 }) })

describe("useOpenQuoteStore", () => {
  let form: FormSpec
  beforeEach(() => {
    form = sheetForm()
    useAppStore.setState({ release, form, revision: "rev-9", people: [], question: null, releaseChanged: false })
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false })
    useOpenQuoteStore.setState({ status: "idle", quote: null, revision: null, base: null, past: false, olderSheets: null, source: null, pending: null, conflict: null, error: null, historyOpen: false })
  })

  afterEach(async () => {
    // A pricing a test left waiting or running is settled with no pricer, pricing nothing.
    useWorkbenchPricingStore.setState({ quotePricer: null })
    await useWorkbenchPreviewStore.getState().settle()
    vi.clearAllMocks()
    window.localStorage.clear()
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
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, version: 2 }, { number: 2 }))
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
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, version: 2, stage: "draft" }, { number: 2, tables: null }, { moved: true }))
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
    release_(answer({ latest: 2, version: 2 }, { number: 2 }))
    await saving

    expect(preview().quote.policy[0]["policy.state"]).toBe("CA")
    expect(store().baseline).not.toBe(JSON.stringify(preview().quote))
    // What was typed during the save is kept in this browser over the revision it saved.
    expect(readDraft(draftKey("motor", "ana@example.com", "q1"))).toMatchObject({ values: { policy: [{ "policy.state": "CA" }] }, version: 2 })
  })

  it("saves once the quote's pricing settles, keeping the price it settles on", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, version: 2 }, { number: 2 }))
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

    expect(store().conflict).toEqual({ message: "Q-000001 has changed since you opened it: jo saved it.", latest: expect.objectContaining({ version: 3 }) })
    expect(preview().quote.policy[0]["policy.state"]).toBe("CA")
  })

  it("saves the values as a new quote made from the quote as it is now", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    vi.mocked(createAppQuote).mockResolvedValue(answer({ id: "q2", reference: "Q-000002", relation: "duplicate" }))
    await store().openQuote("q1", null)
    await store().save()

    const made = await store().saveAsNew()

    expect(made?.reference).toBe("Q-000002")
    expect(vi.mocked(createAppQuote).mock.calls[0][1]).toEqual({ quote_id: "q1", revision: 2, relation: "duplicate" })
    expect(store().conflict).toBeNull()
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
      .mockResolvedValue(answer({ latest: 2, version: 2 }))
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

  it("writes a new quote once though its answer is lost and it is edited before Save is pressed again", async () => {
    const first = { policy: [{ "policy.state": "CA" }] }
    const edited = { policy: [{ "policy.state": "TX" }] }
    vi.mocked(createAppQuote)
      .mockRejectedValueOnce(new ApiError("HTTP 502", 502, "Bad gateway"))
      .mockResolvedValue(answer({ id: "q7", reference: "Q-000007" }, { values: first, tables: null }))
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ id: "q7", reference: "Q-000007", latest: 2, version: 2 }, { number: 2, values: edited, tables: null }))
    await store().openNew(null)
    preview().setCell("policy", 0, "policy.state", "CA")
    expect(await store().save()).toBeNull()

    preview().setCell("policy", 0, "policy.state", "TX")
    const saved = await store().save()

    // The create the lost answer left uncertain is repeated exactly, so it writes once...
    const creates = vi.mocked(createAppQuote).mock.calls.map(([body]) => body)
    expect(creates).toHaveLength(2)
    expect(creates[1]).toEqual(creates[0])
    expect(creates[1].values).toEqual(first)
    // ...and the edit is saved over the quote it made, under a key of its own.
    expect(saveAppQuote).toHaveBeenCalledTimes(1)
    const [id, body, version] = vi.mocked(saveAppQuote).mock.calls[0]
    expect([id, version, body.values]).toEqual(["q7", 1, edited])
    expect(body.operation).not.toBe(creates[0].operation)
    expect(saved?.latest).toBe(2)
    expect(store()).toMatchObject({ base: 2, pending: null, saving: false })
    expect(preview().quote).toEqual(edited)
  })

  it("repeats a create whose answer was lost once its draft is restored, then saves what was typed after", async () => {
    const first = { policy: [{ "policy.state": "CA" }] }
    const edited = { policy: [{ "policy.state": "TX" }] }
    vi.mocked(createAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(answer({ id: "q7", reference: "Q-000007" }, { values: first, tables: null }))
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ id: "q7", reference: "Q-000007", latest: 2, version: 2 }, { number: 2, values: edited, tables: null }))
    await store().openNew(null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    // The page is reloaded: the draft holds the values and the create still to be answered.
    store().close()
    await store().openNew(null)
    expect(store().recovered?.values).toEqual(first)
    store().restoreDraft()
    preview().setCell("policy", 0, "policy.state", "TX")
    await store().save()

    const creates = vi.mocked(createAppQuote).mock.calls.map(([body]) => body)
    expect(creates).toHaveLength(2)
    expect(creates[1]).toEqual(creates[0])
    expect(vi.mocked(saveAppQuote).mock.calls[0].slice(0, 1)).toEqual(["q7"])
    expect(vi.mocked(saveAppQuote).mock.calls[0][1].values).toEqual(edited)
    expect(store().quote?.latest).toBe(2)
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
    saving_(answer({ latest: 2, version: 2 }, { number: 2, values: { policy: [{ "policy.state": "CA" }] } }))
    await saving

    const changing = store().change("archive", {})
    preview().setCell("policy", 0, "policy.state", "TX")
    expect(await store().save()).toBeNull()
    expect(saveAppQuote).toHaveBeenCalledTimes(1)
    changing_(changed({ latest: 3, version: 3, archived: true }, { number: 3 }))
    await changing
  })

  it("keeps a save as new's operation key for a retry after a lost answer", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    vi.mocked(createAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(answer({ id: "q2", reference: "Q-000002" }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    expect(await store().saveAsNew()).toBeNull()
    expect((await store().saveAsNew())?.reference).toBe("Q-000002")

    const keys = vi.mocked(createAppQuote).mock.calls.map((call) => call[0].operation)
    expect(keys).toHaveLength(2)
    expect(keys[1]).toBe(keys[0])
  })

  it("puts the details the app set on the sheets after a first save, though typed over since", async () => {
    let creating: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(createAppQuote).mockReturnValue(new Promise((resolve) => (creating = resolve)))
    await store().openNew(null)
    preview().setCell("policy", 0, "policy.state", "CA")

    const saving = store().save()
    await vi.waitFor(() => expect(createAppQuote).toHaveBeenCalled())
    preview().setCell("policy", 0, "policy.year", "2027")
    const details = { qd_reference: "Q-000001", qd_stage: "draft", qd_assignee: "ana@example.com", qd_archived: false }
    creating(answer({ stage: "draft" }, { tables: null, values: { policy: [{ "policy.state": "CA" }], quote_details: [details] } }))
    await saving

    expect(preview().quote).toEqual({ policy: [{ "policy.state": "CA", "policy.year": "2027" }], quote_details: [details] })
    expect(store().quote?.reference).toBe("Q-000001")
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
    let creating: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(createAppQuote).mockReturnValue(new Promise((resolve) => (creating = resolve)))
    await store().openNew(null)
    preview().setCell("policy", 0, "policy.state", "CA")
    const saving = store().save()
    await vi.waitFor(() => expect(createAppQuote).toHaveBeenCalled())
    preview().setCell("policy", 0, "policy.year", "2027")

    store().close()
    creating(answer())

    expect(await saving).toBeNull()
    expect(store()).toMatchObject({ status: "idle", quote: null })
    expect(readDraft(draftKey("motor", "ana@example.com", "new"))?.values).toEqual({ policy: [{ "policy.state": "CA", "policy.year": "2027" }] })
  })

  it("saves nothing while nothing is typed", async () => {
    await store().openNew(null)

    expect(await store().save()).toBeNull()
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("opens a new quote holding its columns' defaults, which are not typing: nothing saved, and no draft kept", async () => {
    form.schema.tables[0].columns[0].default_value = "NY"
    const fresh = draftKey("motor", "ana@example.com", "new")
    await store().openNew(null)

    expect(preview().quote).toEqual({ policy: [{ "policy.state": "NY" }] })
    expect(await store().save()).toBeNull()
    expect(createAppQuote).not.toHaveBeenCalled()
    store().close()
    expect(readDraft(fresh)).toBeNull()

    // Typed, what was typed is kept with the defaults; a quote made from another keeps its values.
    await store().openNew(null)
    preview().setCell("policy", 0, "policy.year", "2027")
    store().close()
    expect(readDraft(fresh)?.values).toEqual({ policy: [{ "policy.state": "NY", "policy.year": "2027" }] })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(carryAppQuote).mockResolvedValue({ values: { policy: [{ "policy.year": "2026" }] }, dropped: [] })
    await store().openNew({ quoteId: "q1", revision: 1, relation: "duplicate" })
    expect(preview().quote).toEqual({ policy: [{ "policy.year": "2026" }] })
  })

  it("creates a quote made from another's revision with its source", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(carryAppQuote).mockResolvedValue({ values: VALUES, dropped: ["policy.gone"] })
    vi.mocked(createAppQuote).mockResolvedValue(answer({ id: "q2", reference: "Q-000002" }))

    await store().openNew({ quoteId: "q1", revision: 1, relation: "renewal" })

    expect(store().source).toEqual({ quoteId: "q1", revision: 1, relation: "renewal", reference: "Q-000001" })
    expect(store().dropped).toEqual(["policy.gone"])
    // A renewal opens with is_renewal ticked.
    expect(preview().quote).toEqual({ ...VALUES, quote_details: [{ qd_is_renewal: true }] })
    await store().save()
    expect(vi.mocked(createAppQuote).mock.calls[0][1]).toEqual({ quote_id: "q1", revision: 1, relation: "renewal" })
    expect(store().source).toBeNull()
  })

  it("opens a renewal with is_renewal ticked over its source's details, and a duplicate or an alternative with them as they were", async () => {
    const details = { qd_insured: "Acme", qd_is_renewal: false }
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(carryAppQuote).mockResolvedValue({ values: { ...VALUES, quote_details: [details] }, dropped: [] })

    await store().openNew({ quoteId: "q1", revision: 1, relation: "renewal" })
    expect(preview().quote).toEqual({ ...VALUES, quote_details: [{ qd_insured: "Acme", qd_is_renewal: true }] })
    // Ticked as a value typed, so the underwriter can untick it.
    preview().setCell("quote_details", 0, "qd_is_renewal", false)
    expect(preview().quote.quote_details).toEqual([details])

    for (const relation of ["duplicate", "alternative"] as const) {
      await store().openNew({ quoteId: "q1", revision: 1, relation })
      expect(preview().quote).toEqual({ ...VALUES, quote_details: [details] })
    }
  })

  it("opens a quote made from another clean, its values carried without the source's details: nothing kept, nothing to ask", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(carryAppQuote).mockResolvedValue({ values: VALUES, dropped: [] })

    for (const relation of ["duplicate", "renewal", "alternative"] as const) {
      await store().openNew({ quoteId: "q1", revision: 1, relation })
      expect(valuesKey(preview().quote)).toBe(store().baseline)
      expect(store().keepDraft()).toBe(true)
      expect(readDraft(openDraftKey(store()) ?? "")).toBeNull()
    }
    // Carried without the details the app set for the source, which are not the new quote's.
    expect(vi.mocked(carryAppQuote).mock.calls).toEqual([["q1", 1], ["q1", 1], ["q1", 1]])
  })

  it("says why a quote to make one from could not be read", async () => {
    vi.mocked(openAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "No such quote"))
    vi.mocked(carryAppQuote).mockResolvedValue({ values: {}, dropped: [] })

    await store().openNew({ quoteId: "q9", revision: 1, relation: "duplicate" })

    expect(store()).toMatchObject({ status: "error", loadError: "No such quote" })
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
      .mockResolvedValueOnce(changed({ stage: "final", final_revision: 2, latest: 2, version: 2 }))
      .mockResolvedValueOnce(changed({ latest: 3, version: 3 }))
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
    vi.mocked(changeAppQuote).mockResolvedValue({ quote: appQuote({ latest: 2, version: 2, note: "Call the broker" }), revision: null })
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
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ latest: 2, version: 2, assignee: "jo@example.com" }))
    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    writeDraft(draftKey("motor", "ana@example.com", "q1"), { values: preview().quote, version: 1, latest: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    const saved = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    const assigning = store().change("assignee", { assignee: "jo@example.com" })
    await store().openQuote("q2", null)

    saving(answer({ latest: 2, version: 2 }, { number: 2 }))
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
    expect(store().conflict?.latest?.version).toBe(3)
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

  it("keeps a new quote open when the quote it was made from answers after another open", async () => {
    let carried: (value: { values: Record<string, never>; dropped: string[] }) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ id: "q3" }))
    vi.mocked(carryAppQuote).mockReturnValueOnce(new Promise((resolve) => (carried = resolve)))
    const made = store().openNew({ quoteId: "q1", revision: 1, relation: "duplicate" })
    await store().openQuote("q3", null)
    carried({ values: {}, dropped: ["x.y"] })
    await made

    expect(store().quote?.id).toBe("q3")
    expect(store().dropped).toEqual([])
  })

  it("does nothing with no quote open, and nothing with no release read", async () => {
    expect(await store().change("archive", {})).toBeNull()
    await store().moveToCurrentSheets()
    store().restoreDraft()
    await store().loadHistory()
    expect(await store().saveAsNew()).toBeNull()
    useAppStore.setState({ release: null, form: null })
    expect(openDraftKey(store())).toBeNull()
    await store().openNew(null)
    expect(changeAppQuote).not.toHaveBeenCalled()
    expect(carryAppQuote).not.toHaveBeenCalled()
  })

  it("reads the history again as an open quote is opened, saved and changed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, version: 2 }, { number: 2 }))
    vi.mocked(changeAppQuote).mockResolvedValue({ quote: appQuote({ version: 3 }), revision: null })
    vi.mocked(fetchAppQuoteHistory).mockResolvedValue({ revisions: [], events: [], source: null, derived: [] })
    useOpenQuoteStore.setState({ historyOpen: true })

    await store().openQuote("q1", null)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    await store().change("note", { note: "x" })

    await vi.waitFor(() => expect(fetchAppQuoteHistory).toHaveBeenCalledTimes(3))
  })

  it("says why values could not be saved as a new quote, or moved onto the current sheets", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, {}, { form: { ...sheetForm(), name: "older" } }))
    vi.mocked(carryAppQuote).mockRejectedValue(new ApiError("HTTP 500", 500, "carry failed"))
    vi.mocked(createAppQuote).mockRejectedValue(new ApiError("HTTP 422", 422, "create failed"))
    await store().openQuote("q1", null)

    await store().moveToCurrentSheets()
    expect(store().error).toBe("carry failed")
    expect(await store().saveAsNew()).toBeNull()
    expect(store().error).toBe("create failed")
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
    expect(await store().saveAsNew()).toBeNull()
    await store().openNew(null)
    expect(preview().readOnly).toBe(true)
    expect(changeAppQuote).not.toHaveBeenCalled()
  })
})
