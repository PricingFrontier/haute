/**
 * The cells of quote_details the app sets, on the sheets (specs/workbench): locked in
 * Preview as in the app, validation_passed worked out from the checks whatever the cell
 * holds, and Preview showing the identity its sample is priced under.
 */
import { afterEach, describe, expect, it } from "vitest"
import { act, cleanup, renderHook } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type SamplePrice } from "../../stores/useWorkbenchPricingStore"
import fixture from "../../utils/__tests__/fixtures/quoteChecks.json"
import { APP_SET_COLUMNS, columnKey } from "../../utils/sheetVisibility"
import { useQuoteValues, useSampleValues } from "../useSheetValues"

const form = fixture.form as unknown as FormSpec
const TYPED = { region: "south", agreed: true }
const price = (premiumOk: boolean) => ({ basis: "", sample: {}, tables: { policy: [{ premium: 250, premium_ok: premiumOk }] } }) as unknown as SamplePrice
/** The values typed, with the cell saying `said`, the opposite of what the checks come to. */
const values = (age: string, said: boolean) => ({ policy: [{ ...TYPED, age }], quote_details: [{ quote_name: "Fleet", validation_passed: said }] })
const KEY = columnKey("quote_details", "validation_passed")

const initial = { form: useWorkbenchFormStore.getState(), preview: useWorkbenchPreviewStore.getState(), pricing: useWorkbenchPricingStore.getState() }

afterEach(() => {
  cleanup()
  useWorkbenchFormStore.setState(initial.form, true)
  useWorkbenchPreviewStore.setState(initial.preview, true)
  useWorkbenchPricingStore.setState(initial.pricing, true)
})

describe("validation_passed on the sheets", () => {
  it.each([
    ["every check passing", "40", true, true],
    ["a range failing", "16", true, false],
    ["a validation failing", "40", false, false],
  ])("is worked out in Preview with %s, whatever the sample's cell holds, and takes no typing", (_what, age, premiumOk, passed) => {
    useWorkbenchFormStore.setState({ form: { ...form, sample: values(age, !passed) }, status: "ready" })
    useWorkbenchPricingStore.setState({ price: price(premiumOk) })

    const { result } = renderHook(() => useSampleValues())

    expect(result.current.rows.quote_details[0]).toEqual({ quote_name: "Fleet", validation_passed: passed, quote_reference: "PREVIEW", quote_option: "1" })
    expect(result.current.locked.has(KEY)).toBe(true)
  })

  it("locks in Preview every column the app locks, typing into one changing nothing", () => {
    const sample = { ...values("40", true), quote_details: [{ quote_name: "Fleet", quote_reference: "typed before", stage: "final" }] }
    useWorkbenchFormStore.setState({ form: { ...form, sample }, status: "ready" })

    const { result } = renderHook(() => useSampleValues())

    expect(result.current.locked).toBe(APP_SET_COLUMNS)
    for (const name of ["quote_reference", "quote_option", "quote_name", "stage", "assignee", "archived", "created_date", "created_by", "modified_date", "modified_by", "haute_version", "validation_passed"]) {
      expect(APP_SET_COLUMNS.has(columnKey("quote_details", name))).toBe(true)
      act(() => result.current.setCell("quote_details", 0, name, "typed"))
    }
    for (const name of ["insured", "broker", "inception_date", "expiry_date", "is_renewal"]) expect(APP_SET_COLUMNS.has(columnKey("quote_details", name))).toBe(false)
    expect(useWorkbenchFormStore.getState().form?.sample).toEqual(sample)
    // The identity shown is the one the sample is priced under, whatever it held.
    expect(result.current.rows.quote_details[0]).toMatchObject({ quote_reference: "PREVIEW", quote_option: "1" })
  })

  it("keeps in Preview every cell the app sets when rows are set whole, as an Import sets them", () => {
    const sample = { ...values("40", true), quote_details: [{ quote_name: "Fleet", stage: "draft", insured: "Before" }] }
    useWorkbenchFormStore.setState({ form: { ...form, sample }, status: "ready" })
    const { result } = renderHook(() => useSampleValues())

    act(() => result.current.setRows({ quote_details: [{ quote_name: "Imported", quote_reference: "Q-9", validation_passed: true, insured: "After" }], policy: [{ ...TYPED, age: "41" }] }))

    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({
      policy: [{ ...TYPED, age: "41" }],
      quote_details: [{ quote_name: "Fleet", stage: "draft", insured: "After" }],
    })
  })

  it("is unticked in Preview until the sample is priced, a validation the pipeline fills being unfilled", () => {
    useWorkbenchFormStore.setState({ form: { ...form, sample: values("40", true) }, status: "ready" })

    const { result } = renderHook(() => useSampleValues())

    expect(result.current.rows.quote_details[0].validation_passed).toBe(false)
  })

  it("is worked out in the app from the quote and its price, whatever the quote's cell holds", () => {
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPreviewStore.setState({ quote: values("40", false), price: price(true) })

    const { result, rerender } = renderHook(() => useQuoteValues())
    expect(result.current.rows.quote_details[0].validation_passed).toBe(true)

    useWorkbenchPreviewStore.setState({ quote: values("16", true) })
    rerender()
    expect(result.current.rows.quote_details[0].validation_passed).toBe(false)
  })
})
