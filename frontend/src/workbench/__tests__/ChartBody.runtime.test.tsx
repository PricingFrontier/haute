/**
 * A Chart whose runtime fails to load (specs/workbench, Charts), as a lazy chunk that cannot
 * be fetched does: the reason as an alert in its place, and the next draw importing the
 * runtime again rather than keeping the failure. Apart from ChartBody.test.tsx, since a
 * runtime once loaded is kept for every later draw in the module.
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import { shownFields, withSampleCell } from "../../utils/workbenchForm"
import { SheetValuesContext, useSampleValues } from "../useSheetValues"
import WidgetBody from "../WidgetBody"
import { addTo, chartWidget, currentForm, loadForm, sheetForm, widgetsOf } from "./fixtures"

const runtime = vi.hoisted(() => ({ imports: 0, setOption: (() => undefined) as (option: Record<string, unknown>) => void }))

// The first import fails; any after it loads.
vi.mock("../../panels/explore/chartRuntime", () => {
  runtime.imports += 1
  if (runtime.imports === 1) throw new Error("The chart runtime could not be loaded")
  return {
    createComboChart: () => ({ setOption: (option: Record<string, unknown>) => runtime.setOption(option), resize() {}, dispose() {}, getDataURL: () => "" }),
  }
})

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function OnSheet({ children }: { children: ReactNode }) {
  return <SheetValuesContext value={useSampleValues()}>{children}</SheetValuesContext>
}

describe("ChartBody's runtime", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver)
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    useWorkbenchPricingStore.setState({ price: null, error: null })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it("shows a runtime that fails to load as an alert, and imports it again on the next draw", async () => {
    const form = sheetForm()
    addTo(form, chartWidget("w_chart", [{ table: "equipment", column: "equipment.value" }]))
    form.sample = { equipment: [{ "equipment.item": "Crane", "equipment.value": "100" }] }
    loadForm(form)
    const plotted: unknown[] = []
    runtime.setOption = (option) => plotted.push((option as { series: { data: unknown[] }[] }).series.map((series) => series.data))
    const chart = widgetsOf(currentForm())[2]
    render(
      <OnSheet>
        <WidgetBody widget={chart} fields={shownFields(currentForm().schema, chart)} />
      </OnSheet>,
    )

    // The import's failure, in vitest's words for a mock that throws.
    expect(await screen.findByRole("alert")).not.toBeEmptyDOMElement()
    expect(plotted).toEqual([])

    act(() => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "equipment", 0, "equipment.value", "250")))
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument())
    expect(plotted.at(-1)).toEqual([[250]])
    expect(runtime.imports).toBe(2)
  })
})
