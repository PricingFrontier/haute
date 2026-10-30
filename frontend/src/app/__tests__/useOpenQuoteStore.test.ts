/**
 * The quote open in the underwriter app (specs/workbench): opened as the store keeps it, read
 * only while it may not change; saved priced when the sheets show a price for the values as
 * they are and unpriced otherwise, over the version they are edited over; a save over a quote
 * moved on, a page of another release, a lost answer, a repeated save superseded since it
 * landed and values the store refuses each read into what they offer; a change's details
 * taken onto the sheets; an alternative made as it is asked for, once though its answer is
 * lost; and values kept in the browser. A quote under a new reference is the New quote
 * window's (NewQuoteWindow.test.tsx).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/app", () => ({
  changeAppQuote: vi.fn(),
  createAppQuote: vi.fn(),
  fetchAppQuoteActivity: vi.fn(),
  openAppQuote: vi.fn(),
  saveAppQuote: vi.fn(),
  fetchAppPeople: vi.fn(),
}))

import { changeAppQuote, createAppQuote, fetchAppQuoteActivity, openAppQuote, saveAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { FormSpec, WorkbenchAppQuoteActivityResponse, WorkbenchAppQuoteChangeResponse, WorkbenchAppQuoteResponse } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type QuotePricer } from "../../stores/useWorkbenchPricingStore"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import { basisOf } from "../../utils/sheetVisibility"
import { draftKey, readDraft, valuesKey, writeDraft, type PendingWrite } from "../quoteValues"
import useAppStore from "../useAppStore"
import useOpenQuoteStore, { openDraftKey, readOnlyReason } from "../useOpenQuoteStore"
import { answer, appQuote, changed, release, TABLES, VALUES } from "./fixtures"

const store = () => useOpenQuoteStore.getState()
const preview = () => useWorkbenchPreviewStore.getState()
/** Where this browser keeps what is typed into Q-000001/1. */
const KEY = draftKey("motor", "ana@example.com", "Q-000001/1")
/** An alternative asked for of Q-000001/1. */
const ALTERNATIVE = { reference: "Q-000001", option: 1, relation: "alternative" } as const
/** An alternative made, unpriced: the next option under its source's reference. */
const madeAlternative = () => answer({ reference: "Q-000001", option: 2, relation: "alternative", priced_release: null })
const typed = (state: string) => ({ policy: [{ "policy.state": state }] })
const stale = (who = "jo") =>
  new ApiError("HTTP 409", 409, "moved", { detail: {} }, { message: `Q-000001/1 has changed since you opened it: ${who} saved it at 10:05 UTC on 12 Oct 2026.`, error_code: "stale_quote", quote: appQuote({ version: 2 }) })
const SUPERSEDED = "Your save of Q-000001/1 landed at 10:00 UTC; jo changed it at 10:05 UTC since. Open the newest to go on."
const superseded = () =>
  new ApiError("HTTP 409", 409, "superseded", { detail: {} }, {
    message: SUPERSEDED,
    error_code: "write_superseded",
    landed_at: "2026-10-12T10:00:00+00:00",
    changed_by: "jo",
    changed_at: "2026-10-12T10:05:00+00:00",
  })
const activity = (actions: WorkbenchAppQuoteActivityResponse["entries"][number]["action"][]): WorkbenchAppQuoteActivityResponse => ({
  entries: actions.map((action) => ({ action, actor: "ana@example.com", at: "2026-10-12T09:00:00+00:00", version: 1, assignee: null, note: null, reason: null, priced_release: null })),
  source: null,
  derived: [],
})

describe("useOpenQuoteStore", () => {
  let form: FormSpec
  beforeEach(() => {
    form = sheetForm()
    useAppStore.setState({ release, form, revision: "rev-9", people: [], releaseChanged: false })
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false })
    useOpenQuoteStore.setState({ status: "idle", quote: null, base: null, pending: null, conflict: null, refusal: null, error: null, historyOpen: false })
  })

  afterEach(async () => {
    // A pricing a test left waiting or running is settled with no pricer, pricing nothing.
    useWorkbenchPricingStore.setState({ quotePricer: null })
    await useWorkbenchPreviewStore.getState().settle()
    vi.clearAllMocks()
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it("opens a quote on the sheets with its price, by its reference and option, and it may change", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())

    await store().openQuote("Q-000001", 1)

    expect(openAppQuote).toHaveBeenCalledWith("Q-000001", 1)
    expect(store()).toMatchObject({ status: "ready", base: 1, baseline: valuesKey(VALUES), savedTables: TABLES })
    expect(preview().quote).toEqual(VALUES)
    expect(preview().price).toMatchObject({ tables: TABLES, basis: basisOf(form, VALUES) })
    expect(preview().readOnly).toBe(false)
    expect(readOnlyReason(store())).toBeNull()
  })

  it.each([
    ["final", answer({ stage: "final", final_by: "jo@example.com", final_at: "2026-10-12T10:00:00+00:00" })],
    ["archived", answer({ archived: true, archived_at: "2026-10-12T10:00:00+00:00" })],
  ] as const)("holds a %s quote read only", async (reason, opened) => {
    vi.mocked(openAppQuote).mockResolvedValue(opened)

    await store().openQuote("Q-000001", 1)

    expect(readOnlyReason(store())).toBe(reason)
    expect(preview().readOnly).toBe(true)
    expect(await store().save()).toBeNull()
    expect(saveAppQuote).not.toHaveBeenCalled()
  })

  it("says why a quote could not be opened, and names the rows of it the sheets have no place for", async () => {
    vi.mocked(openAppQuote).mockRejectedValueOnce(new ApiError("HTTP 404", 404, "The app keeps no quote Q-000009/1."))
    await store().openQuote("Q-000009", 1)
    expect(store()).toMatchObject({ status: "error", loadError: "The app keeps no quote Q-000009/1." })

    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({}, { dropped: ["equipment row 2 (Crane)"] }))
    await store().openQuote("Q-000001", 1)
    expect(store().dropped).toEqual(["equipment row 2 (Crane)"])
  })

  it("saves a quote priced when the sheets show a price for the values as they are", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2 }))
    await store().openQuote("Q-000001", 1)

    await store().save()

    const [reference, option, body, version] = vi.mocked(saveAppQuote).mock.calls[0]
    expect([reference, option, version]).toEqual(["Q-000001", 1, 1])
    expect(body).toMatchObject({ form_revision: "rev-9", release: "rel-1", values: VALUES, shown: TABLES })
    expect(store()).toMatchObject({ saving: false, pending: null, base: 2 })
    expect(store().quote?.version).toBe(2)
  })

  it("saves a quote unpriced when its price is for other values, and says when the server's price moved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2, priced_release: null }, { moved: true }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    await store().save()

    expect(vi.mocked(saveAppQuote).mock.calls[0][2].shown).toBeNull()
    expect(store()).toMatchObject({ moved: true, savedTables: null })
  })

  it("leaves values typed during a save on the sheets, unsaved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    let release_: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (release_ = resolve)))
    await store().openQuote("Q-000001", 1)

    const saving = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    preview().setCell("policy", 0, "policy.state", "CA")
    release_(answer({ version: 2 }))
    await saving

    expect(preview().quote.policy[0]["policy.state"]).toBe("CA")
    expect(store().baseline).not.toBe(valuesKey(preview().quote))
    // What was typed during the save is kept in this browser over the version it saved.
    expect(readDraft(KEY)).toMatchObject({ values: typed("CA"), version: 2 })
  })

  it("saves once the quote's pricing settles, keeping the price it settles on", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2 }))
    const tables = { premiums: [{ item: "Crane", premium: 9 }] }
    useWorkbenchPricingStore.setState({ quotePricer: vi.fn<QuotePricer>(() => Promise.resolve({ tables, sample: {} })) })
    await store().openQuote("Q-000001", 1)

    // Typed and saved at once: the save waits for the pricing the typing asked for.
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    expect(saveAppQuote).toHaveBeenCalledWith("Q-000001", 1, expect.objectContaining({ values: typed("CA"), shown: tables }), 1)
  })

  it("opens a quote that may change, priced on another release, to be priced again on this one", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ priced_release: "rel-0" }))
    const tables = { premiums: [{ item: "Crane", premium: 130 }] }
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve({ tables, sample: {} }))
    useWorkbenchPricingStore.setState({ quotePricer })

    await store().openQuote("Q-000001", 1)
    expect(preview().price).toBeNull()
    await preview().settle()

    expect(quotePricer).toHaveBeenCalledWith(form, VALUES)
    expect(preview().price).toMatchObject({ tables, basis: basisOf(form, VALUES) })
  })

  it("reads a save over a quote moved on into an offer, keeping the values", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    expect(await store().save()).toBeNull()

    expect(store().conflict).toEqual({ message: "Q-000001/1 has changed since you opened it: jo saved it at 10:05 UTC on 12 Oct 2026.", quote: expect.objectContaining({ version: 2 }) })
    expect(preview().quote.policy[0]["policy.state"]).toBe("CA")
  })

  it("reads a page drawn for another release as one to reload", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "This page was drawn for another release of the app: reload it to save on this one."))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    await store().save()

    expect(useAppStore.getState().releaseChanged).toBe(true)
  })

  it("names each value a save was refused for as not of its column's type, and lets the write go, nothing of it written", async () => {
    const refused = new ApiError("HTTP 422", 422, "refused", {}, {
      message: "These values are not of their columns' types, so the quote was not saved: policy, Year; equipment row 2, Value.",
      error_code: "quote_values_invalid",
      cells: [
        { table: "policy", row: null, column: "Year" },
        { table: "equipment", row: 2, column: "Value" },
      ],
    })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValueOnce(refused).mockResolvedValue(answer({ version: 2 }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.year", "abc")

    expect(await store().save()).toBeNull()

    expect(store()).toMatchObject({ refusal: { of: "save", places: ["policy, Year", "equipment row 2, Value"] }, error: null, pending: null })
    // Saved again, it is a write of its own, and the refusal goes.
    await store().save()
    const [first, second] = vi.mocked(saveAppQuote).mock.calls.map((call) => call[2].operation)
    expect(second).not.toBe(first)
    expect(store().refusal).toBeNull()
  })

  it("keeps a save's operation key after a lost answer or a server failure, and lets it go on a refusal", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError("HTTP 504", 504, "Gateway timeout"))
      .mockRejectedValueOnce(new ApiError("HTTP 422", 422, "No"))
      .mockResolvedValue(answer({ version: 2 }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    await store().save()
    expect(store().error).toContain("Failed to fetch")
    await store().save()
    await store().save()
    await store().save()

    const keys = vi.mocked(saveAppQuote).mock.calls.map((call) => call[2].operation)
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).toBe(keys[0])
    expect(keys[3]).not.toBe(keys[2])
  })

  it("repeats a save whose answer was lost before saving what was typed since, each written once", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(answer({ version: 2 }, { values: typed("CA") }))
      .mockResolvedValueOnce(answer({ version: 3 }, { values: typed("TX") }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    preview().setCell("policy", 0, "policy.state", "TX")

    await store().save()

    const calls = vi.mocked(saveAppQuote).mock.calls
    // The lost save sent again exactly, then the values typed since over the version it wrote.
    expect(calls[1]).toEqual(calls[0])
    expect([calls[2][2].values, calls[2][3]]).toEqual([typed("TX"), 2])
    expect(calls[2][2].operation).not.toBe(calls[0][2].operation)
    expect(store()).toMatchObject({ pending: null, base: 3 })
    expect(preview().quote).toEqual(typed("TX"))
  })

  it("lets a lost save go when sent again and refused, and saves what is on the sheets", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError("HTTP 422", 422, "No"))
      .mockResolvedValueOnce(answer({ version: 2 }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    preview().setCell("policy", 0, "policy.state", "TX")

    await store().save()

    const calls = vi.mocked(saveAppQuote).mock.calls
    expect(calls).toHaveLength(3)
    // It never landed, so the values are saved over the version they were edited over.
    expect([calls[2][2].values, calls[2][3]]).toEqual([typed("TX"), 1])
    expect(calls[2][2].operation).not.toBe(calls[0][2].operation)
    expect(store()).toMatchObject({ pending: null, error: null })
  })

  it("lets a lost save go as done when sent again it had landed and the quote moved on since, offering the newest", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockRejectedValueOnce(superseded()).mockResolvedValue(answer({ version: 4 }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    expect(store().pending).not.toBeNull()

    // Nothing typed since: the save sent again is the one held, exactly.
    expect(await store().save()).toBeNull()

    const calls = vi.mocked(saveAppQuote).mock.calls
    expect(calls[1]).toEqual(calls[0])
    expect(store()).toMatchObject({ pending: null, conflict: { message: SUPERSEDED, quote: null } })
    expect(readDraft(KEY)?.pending).toBeNull()
    // Done, it is never sent again: the next save is a write of its own.
    await store().save()
    const keys = vi.mocked(saveAppQuote).mock.calls.map((call) => call[2].operation)
    expect(keys).toHaveLength(3)
    expect(keys[2]).not.toBe(keys[0])
  })

  it("saves nothing typed since over the version a superseded save moved the quote on from, keeping it on the sheets", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockRejectedValueOnce(superseded())
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    preview().setCell("policy", 0, "policy.state", "TX")

    expect(await store().save()).toBeNull()

    const calls = vi.mocked(saveAppQuote).mock.calls
    expect(calls).toHaveLength(2)
    expect(calls[1]).toEqual(calls[0])
    expect(store()).toMatchObject({ pending: null, conflict: { message: SUPERSEDED } })
    expect(preview().quote).toEqual(typed("TX"))
    expect(readDraft(KEY)).toMatchObject({ values: typed("TX"), pending: null })
  })

  it("lets a save held from an earlier visit go as done when it landed under a version since moved past, and saves what is typed over the version opened", async () => {
    const held: PendingWrite = {
      basis: "held",
      write: { kind: "save", reference: "Q-000001", option: 1, version: 1, body: { operation: "save-held-0001", form_revision: "rev-9", release: "rel-1", values: typed("CA"), shown: null } },
    }
    writeDraft(KEY, { values: typed("CA"), version: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: held })
    vi.mocked(openAppQuote).mockResolvedValue(answer({ version: 3 }))
    vi.mocked(saveAppQuote).mockRejectedValueOnce(superseded()).mockResolvedValueOnce(answer({ version: 4 }, { values: typed("TX") }))
    await store().openQuote("Q-000001", 1)
    expect(store().recovered?.pending).toEqual(held)
    preview().setCell("policy", 0, "policy.state", "TX")

    await store().save()

    const calls = vi.mocked(saveAppQuote).mock.calls
    expect(calls[0]).toEqual(["Q-000001", 1, held.write.body, 1])
    expect([calls[1][2].values, calls[1][3]]).toEqual([typed("TX"), 3])
    expect(calls[1][2].operation).not.toBe("save-held-0001")
    expect(store()).toMatchObject({ pending: null, conflict: null, base: 4 })
  })

  it("saves nothing while nothing is typed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ priced_release: null }, { values: {} }))
    await store().openQuote("Q-000001", 1)

    expect(await store().save()).toBeNull()

    expect(saveAppQuote).not.toHaveBeenCalled()
  })

  it("keeps a save held after a lost answer through a note, and lets it go once a change moves the version on", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch"))
    vi.mocked(changeAppQuote).mockResolvedValueOnce(changed({ note: "Call the broker" })).mockResolvedValueOnce(changed({ version: 2, archived: true }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    // A note moves no version: the save it named version 1 over may still have landed.
    await store().change("note", { note: "Call the broker" })
    expect(store().pending).not.toBeNull()
    await store().change("archive", {})

    // It named version 1, which the change moved on from: it never landed, and never will.
    expect(store().pending).toBeNull()
  })

  it("asks again with the same key after a reload before the answer, and with a new key once the alternative is made", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(createAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(madeAlternative())

    expect(await store().openNew(ALTERNATIVE)).toBeNull()
    expect(store()).toMatchObject({ status: "error", loadError: "Failed to fetch" })
    // The page is reloaded before an answer came: asked with the key held, so it is made once.
    store().close()
    expect((await store().openNew(ALTERNATIVE))?.label).toBe("Q-000001/2")
    // Asked for again once made: another option, under a key of its own.
    await store().openNew(ALTERNATIVE)

    const keys = vi.mocked(createAppQuote).mock.calls.map(([body]) => body.operation)
    expect(keys).toHaveLength(3)
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[1])
  })

  it("lets a refused alternative's key go, so the next asked for is asked anew", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(createAppQuote).mockRejectedValueOnce(new ApiError("HTTP 422", 422, "No")).mockResolvedValue(madeAlternative())

    await store().openNew(ALTERNATIVE)
    expect(store().loadError).toBe("No")
    await store().openNew(ALTERNATIVE)

    const [first, second] = vi.mocked(createAppQuote).mock.calls.map(([body]) => body.operation)
    expect(second).not.toBe(first)
  })

  it("holds an alternative's key apart from one of another quote", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(createAppQuote).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(madeAlternative())

    await store().openNew({ ...ALTERNATIVE, option: 2 })
    await store().openNew(ALTERNATIVE)

    const [other, alternative] = vi.mocked(createAppQuote).mock.calls
    expect(alternative[1]).toEqual({ reference: "Q-000001", option: 1, relation: "alternative" })
    expect(alternative[0].operation).not.toBe(other[0].operation)
    expect(store().quote?.label).toBe("Q-000001/2")
  })

  it("makes no change while a save is being made, and no save while a change is", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    let saving_: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (saving_ = resolve)))
    let changing_: (value: WorkbenchAppQuoteChangeResponse) => void = () => {}
    vi.mocked(changeAppQuote).mockReturnValue(new Promise((resolve) => (changing_ = resolve)))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    const saving = store().save()
    expect(await store().change("archive", {})).toBeNull()
    expect(changeAppQuote).not.toHaveBeenCalled()
    saving_(answer({ version: 2 }, { values: typed("CA") }))
    await saving

    const changing = store().change("archive", {})
    preview().setCell("policy", 0, "policy.state", "TX")
    expect(await store().save()).toBeNull()
    expect(saveAppQuote).toHaveBeenCalledTimes(1)
    changing_(changed({ version: 3, archived: true }))
    await changing
  })

  it("keeps what is typed before and during a change that holds the quote read only", async () => {
    let archiving: (value: WorkbenchAppQuoteChangeResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote).mockReturnValue(new Promise((resolve) => (archiving = resolve)))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    const change = store().change("archive", {})
    preview().setCell("policy", 0, "policy.year", "2028")
    archiving(changed({ version: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }))
    await change

    expect(preview().readOnly).toBe(true)
    expect(readDraft(KEY)?.values).toEqual({ policy: [{ "policy.state": "CA", "policy.year": "2028" }] })
  })

  it("offers back what was kept of a quote archived with typing once it is restored, and keeps it", async () => {
    const archived = { version: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer(archived))
    vi.mocked(changeAppQuote).mockResolvedValueOnce(changed(archived)).mockResolvedValueOnce(changed({ version: 3 }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().change("archive", {})
    store().close()

    await store().openQuote("Q-000001", 1)
    expect(store().recovered).toBeNull()
    await store().change("restore", {})
    expect(store().recovered?.values).toEqual(typed("CA"))
    store().close()

    expect(readDraft(KEY)?.values).toEqual(typed("CA"))
  })

  it("keeps nothing and saves nothing while a quote is being opened, the quote before off the sheets", async () => {
    const fresh = draftKey("motor", "ana@example.com", "new")
    writeDraft(fresh, { values: typed("UNSAVED-NEW"), version: null, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    let reading: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockReturnValueOnce(new Promise((resolve) => (reading = resolve)))
    await store().openQuote("Q-000001", 1)

    const opening = store().openQuote("Q-000002", 1)
    expect(preview()).toMatchObject({ quote: {}, readOnly: true })
    expect(store().keepDraft()).toBe(true)
    expect(await store().save()).toBeNull()
    reading(answer({ reference: "Q-000002" }))
    await opening

    expect(readDraft(fresh)?.values).toEqual(typed("UNSAVED-NEW"))
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("drops a save's answer once the quote is left, keeping what was typed", async () => {
    let saving_: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (saving_ = resolve)))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    const saving = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    preview().setCell("policy", 0, "policy.year", "2027")

    store().close()
    saving_(answer({ version: 2 }))

    expect(await saving).toBeNull()
    expect(store()).toMatchObject({ status: "idle", quote: null })
    expect(readDraft(KEY)?.values).toEqual({ policy: [{ "policy.state": "CA", "policy.year": "2027" }] })
  })

  it("makes an alternative from its source's values, without the details the app set for the source, under its reference, and opens it", async () => {
    const sheets = { ...form, schema: { tables: [detailsTable(), ...form.schema.tables] } }
    useAppStore.setState({ form: sheets })
    const details = { qd_reference: "Q-000001", qd_option: "1", qd_stage: "final", qd_assignee: "jo@example.com", qd_insured: "Acme", qd_is_renewal: true }
    vi.mocked(openAppQuote).mockResolvedValue(answer({ stage: "final" }, { values: { ...VALUES, quote_details: [details] }, dropped: ["equipment row 2 (Crane)"] }))
    vi.mocked(createAppQuote).mockResolvedValue(madeAlternative())

    const quote = await store().openNew(ALTERNATIVE)

    expect(quote?.label).toBe("Q-000001/2")
    expect(openAppQuote).toHaveBeenCalledWith("Q-000001", 1)
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect(source).toEqual({ reference: "Q-000001", option: 1, relation: "alternative" })
    // It names no reference of its own: it takes its source's, and the next option under it.
    expect([body.values, reference]).toEqual([{ ...VALUES, quote_details: [{ qd_insured: "Acme", qd_is_renewal: true }] }, null])
    expect(body).not.toHaveProperty("shown")
    // What of its source has no place on the sheets is named.
    expect(store()).toMatchObject({ status: "ready", base: 1, dropped: ["equipment row 2 (Crane)"], quote: expect.objectContaining({ option: 2 }) })
    expect(preview().readOnly).toBe(false)
  })

  it("says why a quote to make one from could not be read", async () => {
    vi.mocked(openAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "No such quote"))

    await store().openNew({ ...ALTERNATIVE, reference: "Q-000009" })

    expect(store()).toMatchObject({ status: "error", loadError: "No such quote" })
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("offers back values this browser kept, restores them or discards them", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    writeDraft(KEY, { values: typed("CA"), version: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    await store().openQuote("Q-000001", 1)
    expect(openDraftKey(store())).toBe(KEY)
    expect(store().recovered?.values).toEqual(typed("CA"))

    store().restoreDraft()
    expect(preview().quote).toEqual(typed("CA"))

    await store().openQuote("Q-000001", 1)
    store().discardDraft()
    expect(readDraft(KEY)).toBeNull()
    expect(store().recovered).toBeNull()
  })

  it("keeps each quote's draft apart by its reference, case included, and option", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ reference: "abc-1" })).mockResolvedValueOnce(answer({ reference: "ABC-1", option: 2 }))
    await store().openQuote("abc-1", 1)
    expect(openDraftKey(store())).toBe(draftKey("motor", "ana@example.com", "abc-1/1"))
    await store().openQuote("ABC-1", 2)
    expect(openDraftKey(store())).toBe(draftKey("motor", "ana@example.com", "ABC-1/2"))
  })

  it("offers back no draft on a quote that may not change, and puts back only what fits the sheets", async () => {
    const kept = { policy: [{ "policy.state": "CA", "policy.gone": "x" }], removed: [{ c: "1" }], quote_details: [{ qd_stage: "final", qd_insured: "Acme" }] }
    writeDraft(KEY, { values: kept, version: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ stage: "final" }))
    await store().openQuote("Q-000001", 1)
    expect(store().recovered).toBeNull()
    // Offered while it could change, then held read only, it is not put back.
    useOpenQuoteStore.setState({ recovered: { values: kept, version: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null } })
    store().restoreDraft()
    expect(preview().quote).toEqual(VALUES)
    expect(preview().readOnly).toBe(true)

    // The details the app sets as the quote holds them now, not as they were kept.
    const details = { qd_reference: "Q-000001", qd_stage: "draft" }
    const sheets = { ...form, schema: { tables: [detailsTable(), ...form.schema.tables] } }
    useAppStore.setState({ form: sheets })
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({}, { values: { ...VALUES, quote_details: [details] } }))
    await store().openQuote("Q-000001", 1)
    store().restoreDraft()
    expect(preview().quote).toEqual({ policy: [{ "policy.state": "CA" }], quote_details: [{ qd_insured: "Acme", ...details }] })
  })

  it("names what a restored draft holds that has no place on the sheets, as the sheets it was typed on named it", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const values = { policy: [{ "policy.state": "CA", "policy.gone": "x" }], removed: [{ c: "1" }] }
    const places = { "policy:policy.state": "policy.state", "policy:policy.gone": "policy.cover", "removed:c": "extras.note" }
    writeDraft(KEY, { values, version: 1, places, keptAt: "2026-10-12T10:00:00Z", pending: null })
    await store().openQuote("Q-000001", 1)

    store().restoreDraft()

    expect(preview().quote).toEqual(typed("CA"))
    expect(store().dropped).toEqual(["policy.cover", "extras.note"])
  })

  it("keeps where each value typed sits on the sheets with the draft", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")

    expect(store().keepDraft()).toBe(true)

    expect(Object.keys(readDraft(KEY)?.places ?? {})).toContain("policy:policy.state")
  })

  it("offers back no draft already on the sheet, as after an archive and a restore with the typing still there", async () => {
    const archived = { version: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer())
    vi.mocked(changeAppQuote).mockResolvedValueOnce(changed(archived)).mockResolvedValueOnce(changed({ version: 3 }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    store().keepDraft()

    await store().change("archive", {})
    await store().change("restore", {})

    expect(preview().quote.policy?.[0]["policy.state"]).toBe("CA")
    expect(store().recovered).toBeNull()
  })

  it("does not offer back a draft the same as what is saved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    writeDraft(KEY, { values: VALUES, version: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })

    await store().openQuote("Q-000001", 1)

    expect(store().recovered).toBeNull()
  })

  it("changes the quote over the version it moved to, holding it read only as the change leaves it", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ stage: "final", final_by: "ana@example.com", final_at: "2026-10-12T10:00:00+00:00", version: 2 }))
      .mockResolvedValueOnce(changed({ version: 3 }))
    await store().openQuote("Q-000001", 1)

    await store().change("final", {})
    expect(preview().readOnly).toBe(true)
    expect(store().base).toBe(2)
    await store().change("amend", { reason: "Broker" })

    expect(preview().readOnly).toBe(false)
    expect(vi.mocked(changeAppQuote).mock.calls).toEqual([
      ["Q-000001", 1, "final", 1, {}],
      ["Q-000001", 1, "amend", 2, { reason: "Broker" }],
    ])
    // A draft again, its values edited over the version the amendment moved it to.
    expect([store().quote?.stage, store().base, store().baseline]).toEqual(["draft", 3, valuesKey(VALUES)])
  })

  it("names each required field Mark final was refused for, the quote left as it was", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote).mockRejectedValue(
      new ApiError("HTTP 409", 409, "not ready", {}, {
        message: "Q-000001/1 needs a value in policy.Drivers, equipment row 3.Value before it is marked final.",
        error_code: "quote_not_ready",
        missing: ["policy.Drivers", "equipment row 3.Value"],
      }),
    )
    await store().openQuote("Q-000001", 1)

    expect(await store().change("final", {})).toBeNull()

    expect(store()).toMatchObject({ refusal: { of: "final", places: ["policy.Drivers", "equipment row 3.Value"] }, error: null, base: 1 })
    expect(store().quote?.stage).toBe("draft")
    store().dismiss("refusal")
    expect(store().refusal).toBeNull()
  })

  it("takes the details a change set onto the sheets from the quote it answers, and edits over its version, nothing new to save", async () => {
    const sheets = { ...form, schema: { tables: [detailsTable(), ...form.schema.tables] } }
    useAppStore.setState({ form: sheets })
    useWorkbenchFormStore.setState({ form: sheets })
    const saved = { qd_reference: "Q-000001", qd_option: "1", qd_stage: "draft", qd_assignee: "ana@example.com", qd_insured: "Acme" }
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { ...VALUES, quote_details: [saved] } }))
    vi.mocked(changeAppQuote).mockResolvedValue(
      changed({ version: 2, assignee: "jo@example.com", details: { quote_reference: "Q-000001", quote_option: 1, insured: "Acme", stage: "draft", assignee: "jo@example.com", archived: false } }),
    )
    await store().openQuote("Q-000001", 1)

    await store().change("assignee", { assignee: "jo@example.com" })

    expect(preview().quote).toEqual({ ...VALUES, quote_details: [{ ...saved, qd_assignee: "jo@example.com" }] })
    expect(store()).toMatchObject({ base: 2, baseline: valuesKey(preview().quote) })
  })

  it("changes only the quote a change was asked about, leaving another opened by the answer alone", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ reference: "Q-000002" }))
    await store().openQuote("Q-000001", 1)
    const asked = store().quote
    // While "Mark Q-000001/1 final?" was open, Back opened another quote at the same version.
    await store().openQuote("Q-000002", 1)

    expect(await store().change("final", {}, asked ?? undefined)).toBeNull()

    expect(changeAppQuote).not.toHaveBeenCalled()
    expect(store().error).toContain("Q-000001/1")
  })

  it("keeps a note from moving the version a save names, so a save over a colleague's is refused", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    // Jo saved meanwhile: the note's answer is the quote as it is now.
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ version: 2, note: "Call the broker" }))
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    await store().openQuote("Q-000001", 1)

    await store().change("note", { note: "Call the broker" })
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()

    expect(store().quote?.note).toBe("Call the broker")
    expect(vi.mocked(saveAppQuote).mock.calls[0][3]).toBe(1)
    expect(store().conflict?.message).toContain("jo saved it")
  })

  it("drops a save's or a change's answer once another quote was opened, keeping that one", async () => {
    let saving: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ reference: "Q-000002" }, { values: typed("TX") }))
    vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => (saving = resolve)))
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ version: 2, assignee: "jo@example.com" }))
    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    writeDraft(KEY, { values: preview().quote, version: 1, places: {}, keptAt: "2026-10-12T10:00:00Z", pending: null })
    const saved = store().save()
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    const assigning = store().change("assignee", { assignee: "jo@example.com" })
    await store().openQuote("Q-000002", 1)

    saving(answer({ version: 2 }))
    expect(await saved).toBeNull()
    await assigning

    expect(store()).toMatchObject({ quote: expect.objectContaining({ reference: "Q-000002" }), base: 1, saving: false })
    expect(preview().quote).toEqual(typed("TX"))
    // The first quote's save landed: what this browser kept of it, as saved, is dropped.
    expect(readDraft(KEY)).toBeNull()
  })

  it("reads a change over a quote moved on into an offer", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote).mockRejectedValue(stale())
    await store().openQuote("Q-000001", 1)

    expect(await store().change("archive", {})).toBeNull()
    expect(store().conflict?.quote?.version).toBe(2)
    store().dismiss("conflict")
    expect(store().conflict).toBeNull()
  })

  it("reads the activity while the history is open, and says when it could not", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(fetchAppQuoteActivity).mockResolvedValueOnce(activity(["created"])).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "down"))
    await store().openQuote("Q-000001", 1)

    store().setHistoryOpen(true)
    await vi.waitFor(() => expect(store().activity).not.toBeNull())
    expect(fetchAppQuoteActivity).toHaveBeenCalledWith("Q-000001", 1)
    await store().loadActivity()
    expect(store().error).toContain("history could not be read")
  })

  it("shows the activity asked for last, whichever answers first", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const answers: Array<(value: WorkbenchAppQuoteActivityResponse) => void> = []
    vi.mocked(fetchAppQuoteActivity).mockImplementation(() => new Promise((resolve) => { answers.push(resolve) }))
    await store().openQuote("Q-000001", 1)

    const before = store().loadActivity()
    const after = store().loadActivity()
    answers[1](activity(["created", "saved"]))
    await after
    answers[0](activity(["created"]))
    await before

    expect(store().activity?.entries.map((entry) => entry.action)).toEqual(["created", "saved"])
  })

  it("says nothing of an earlier activity read that fails after a later one answered", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const settles: Array<{ resolve: (value: WorkbenchAppQuoteActivityResponse) => void; reject: (error: Error) => void }> = []
    vi.mocked(fetchAppQuoteActivity).mockImplementation(() => new Promise((resolve, reject) => { settles.push({ resolve, reject }) }))
    await store().openQuote("Q-000001", 1)

    const before = store().loadActivity()
    const after = store().loadActivity()
    settles[1].resolve(activity([]))
    await after
    settles[0].reject(new Error("down"))
    await before

    expect(store().activity).not.toBeNull()
    expect(store().error).toBeNull()
  })

  it("keeps the newest open when an earlier one answers last", async () => {
    let early: (value: WorkbenchAppQuoteResponse) => void = () => {}
    let earlyFail: (reason: unknown) => void = () => {}
    vi.mocked(openAppQuote)
      .mockReturnValueOnce(new Promise((resolve) => (early = resolve)))
      .mockReturnValueOnce(new Promise((_resolve, reject) => (earlyFail = reject)))
      .mockResolvedValueOnce(answer({ reference: "Q-000003" }))
    const first = store().openQuote("Q-000001", 1)
    const second = store().openQuote("Q-000002", 1)
    await store().openQuote("Q-000003", 1)
    early(answer())
    earlyFail(new ApiError("HTTP 404", 404, "gone"))
    await Promise.allSettled([first, second])

    expect(store()).toMatchObject({ status: "ready", quote: expect.objectContaining({ reference: "Q-000003" }) })
  })

  it("makes no quote once another is opened before the one it is made from answers", async () => {
    let source: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockReturnValueOnce(new Promise((resolve) => (source = resolve))).mockResolvedValueOnce(answer({ reference: "Q-000003" }))
    const making = store().openNew(ALTERNATIVE)
    await store().openQuote("Q-000003", 1)
    source(answer({}, { values: {}, dropped: ["equipment row 1 (Crane)"] }))

    expect(await making).toBeNull()
    expect(store().quote?.reference).toBe("Q-000003")
    expect(store().dropped).toEqual([])
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("does nothing with no quote open, and nothing with no release read", async () => {
    expect(await store().change("archive", {})).toBeNull()
    store().restoreDraft()
    await store().loadActivity()
    useAppStore.setState({ release: null, form: null })
    expect(openDraftKey(store())).toBeNull()
    await store().openNew(ALTERNATIVE)
    expect(changeAppQuote).not.toHaveBeenCalled()
    expect(openAppQuote).not.toHaveBeenCalled()
    expect(createAppQuote).not.toHaveBeenCalled()
    expect(fetchAppQuoteActivity).not.toHaveBeenCalled()
  })

  it("reads the activity again as an open quote is opened, saved and changed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2 }))
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ version: 2, note: "x" }))
    vi.mocked(fetchAppQuoteActivity).mockResolvedValue(activity([]))
    useOpenQuoteStore.setState({ historyOpen: true })

    await store().openQuote("Q-000001", 1)
    preview().setCell("policy", 0, "policy.state", "CA")
    await store().save()
    await store().change("note", { note: "x" })

    await vi.waitFor(() => expect(fetchAppQuoteActivity).toHaveBeenCalledTimes(3))
  })

  it("dismisses each notice", () => {
    useOpenQuoteStore.setState({ error: "e", moved: true, dropped: ["a.b"], conflict: { message: "m", quote: null }, refusal: { of: "save", places: ["a, b"] } })
    store().dismiss("error")
    store().dismiss("moved")
    store().dismiss("dropped")
    store().dismiss("conflict")
    store().dismiss("refusal")
    expect(store()).toMatchObject({ error: null, moved: false, dropped: [], conflict: null, refusal: null })
  })

  it("holds every quote read only for one who may only read quotes, and changes nothing", async () => {
    useAppStore.setState({ release: { ...release, can_edit: false, edit_role: "Quotes.Edit" } })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await store().openQuote("Q-000001", 1)

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
