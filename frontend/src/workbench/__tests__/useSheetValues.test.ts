/**
 * quote_details' validation_passed on the sheets (specs/workbench): worked out from the
 * checks in Preview and the app, whatever the cell holds, and taking no typing.
 */
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, renderHook } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type SamplePrice } from "../../stores/useWorkbenchPricingStore"
import fixture from "../../utils/__tests__/fixtures/quoteChecks.json"
import { columnKey } from "../../utils/sheetVisibility"
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

    expect(result.current.rows.quote_details[0]).toEqual({ quote_name: "Fleet", validation_passed: passed })
    expect(result.current.locked.has(KEY)).toBe(true)
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
