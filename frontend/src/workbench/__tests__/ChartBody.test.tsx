/**
 * A Chart on a sheet (specs/workbench, Charts), the chart runtime mocked: drawn once and
 * given each new option merged, "Nothing to chart yet" with nothing to plot, its values
 * for a screen reader, the sample while building and the quote in Preview, dimmed while it
 * plots a price for other values, resized, drawn anew on a change of theme, a failure
 * shown as an alert, and disposed on unmount.
 */
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { FieldRef, FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { previewBasis } from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { shownFields, updateSchemaColumn, withSampleCell, type ChartOptions } from "../../utils/workbenchForm"
import { SheetValuesContext, useNewQuoteValues, useQuoteValues, useSampleValues } from "../useSheetValues"
import WidgetBody from "../WidgetBody"
import { addTo, chartWidget, currentForm, loadForm, sheetForm, widgetsOf } from "./fixtures"

const setOption = vi.fn<(option: Record<string, unknown>, settings?: { replaceMerge?: string[] }) => void>()
const resize = vi.fn()
const dispose = vi.fn()
const getDataURL = vi.fn(() => "data:image/svg+xml,<svg/>")
const createComboChart = vi.fn<(element: HTMLElement) => {
  setOption: typeof setOption
  resize: typeof resize
  dispose: typeof dispose
  getDataURL: typeof getDataURL
}>(() => ({ setOption, resize, dispose, getDataURL }))

vi.mock("../../panels/explore/chartRuntime", () => ({
  createComboChart: (element: HTMLElement) => createComboChart(element),
}))

let resizeCallback: ResizeObserverCallback | null = null

class MockResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    resizeCallback = callback
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

/** What a new option replaces whole rather than merges. */
const REPLACED = ["series", "xAxis", "yAxis", "grid", "legend"]
const VALUE: FieldRef = { table: "equipment", column: "equipment.value" }
const PREMIUM: FieldRef = { table: "premiums", column: "premiums.premium" }

/** `sheetForm` with a Chart, w_chart, plotting `fields` in its first sheet's section. */
function chartForm(fields: FieldRef[], options: Partial<ChartOptions> & { title?: string } = {}): FormSpec {
  const form = sheetForm()
  addTo(form, chartWidget("w_chart", fields, options))
  return form
}

const chart = () => {
  const found = widgetsOf(currentForm()).find((candidate) => candidate.id === "w_chart")
  if (found === undefined) throw new Error("no w_chart")
  return found
}

/** Whose values the sheet holds: a new quote's while building, the sample in Preview, or the underwriter app's quote. */
type Values = "new" | "sample" | "quote"

/** The sheet's values around a component. */
function OnSheet({ values, children }: { values: Values; children: ReactNode }) {
  const newQuote = useNewQuoteValues()
  const sample = useSampleValues()
  const quote = useQuoteValues()
  return <SheetValuesContext value={values === "new" ? newQuote : values === "quote" ? quote : sample}>{children}</SheetValuesContext>
}

function renderChart(values: Values = "sample") {
  return render(
    <OnSheet values={values}>
      <WidgetBody widget={chart()} fields={shownFields(currentForm().schema, chart())} />
    </OnSheet>,
  )
}

/** Type `value` into the sample's cell, as a Table on the same sheet would. */
const typeSample = (tableId: string, row: number, columnId: string, value: string) =>
  act(() => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, tableId, row, columnId, value)))

/** Each series' values in the last option set on the chart. */
const plotted = () => (setOption.mock.lastCall?.[0] as { series: { data: unknown[] }[] }).series.map((series) => series.data)

/** The screen reader's table, by row: its category, then each value. */
const readOut = (name: string) =>
  within(screen.getByRole("table", { name }))
    .getAllByRole("row")
    .map((row) => Array.from(row.children).map((cell) => cell.textContent))

describe("ChartBody", () => {
  beforeEach(() => {
    setOption.mockReset()
    resize.mockReset()
    dispose.mockReset()
    createComboChart.mockReset()
    resizeCallback = null
    vi.stubGlobal("ResizeObserver", MockResizeObserver)
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    loadForm()
    useWorkbenchPricingStore.setState({ price: null, error: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false })
    useWorkbenchViewStore.setState({ section: "sheets" })
  })

  afterEach(() => {
    cleanup()
    delete document.documentElement.dataset.theme
    vi.unstubAllGlobals()
  })

  it("draws a Chart once in its host and merges each new option into it, replacing what a change of type drops", async () => {
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.item": "Crane", "equipment.value": "100" }] }
    loadForm(form)
    renderChart()

    await waitFor(() => expect(setOption).toHaveBeenCalled())
    expect(createComboChart).toHaveBeenCalledTimes(1)
    expect(createComboChart).toHaveBeenCalledWith(screen.getByTestId("chart-host-w_chart"))
    expect(setOption).toHaveBeenLastCalledWith(expect.objectContaining({ series: [expect.objectContaining({ data: [100] })] }), { replaceMerge: REPLACED })

    // Each value typed moves the series in the same chart.
    typeSample("equipment", 0, "equipment.value", "250")
    await waitFor(() => expect(plotted()).toEqual([[250]]))
    typeSample("equipment", 1, "equipment.value", "400")
    await waitFor(() => expect(plotted()).toEqual([[250, 400]]))
    expect(setOption).toHaveBeenLastCalledWith(expect.any(Object), { replaceMerge: REPLACED })
    expect(createComboChart).toHaveBeenCalledTimes(1)
    expect(dispose).not.toHaveBeenCalled()
  })

  it('says "Nothing to chart yet" while nothing is typed or priced, making no chart', async () => {
    loadForm(chartForm([VALUE, PREMIUM]))
    renderChart()

    expect(screen.getByText("Nothing to chart yet")).toBeInTheDocument()
    expect(screen.queryByTestId("chart-host-w_chart")).not.toBeInTheDocument()
    await act(async () => {})
    expect(createComboChart).not.toHaveBeenCalled()
  })

  it("points at the schema while a Chart plots no fields, and in Preview, which has no panel, shows nothing", async () => {
    loadForm(chartForm([]))
    renderChart()

    expect(screen.getByText("Choose its fields from the schema in the panel on the right")).toBeInTheDocument()
    expect(screen.queryByRole("figure")).not.toBeInTheDocument()
    await act(async () => {})
    expect(createComboChart).not.toHaveBeenCalled()

    cleanup()
    useWorkbenchViewStore.setState({ section: "preview" })
    renderChart()
    expect(screen.queryByText(/panel on the right/)).not.toBeInTheDocument()
    expect(screen.queryByRole("figure")).not.toBeInTheDocument()
  })

  it("says why a pie cannot be drawn, a value below 0 or every value 0, making no chart until it can be", async () => {
    const form = chartForm([VALUE], { chart_type: "pie" })
    form.sample = { equipment: [{ "equipment.item": "Crane", "equipment.value": "100" }, { "equipment.item": "Drill", "equipment.value": "-20" }] }
    loadForm(form)
    renderChart()

    expect(screen.getByText(/^A pie can't show a value below 0: 2 is /)).toBeInTheDocument()
    expect(screen.queryByTestId("chart-host-w_chart")).not.toBeInTheDocument()
    await act(async () => {})
    expect(createComboChart).not.toHaveBeenCalled()

    typeSample("equipment", 1, "equipment.value", "20")
    await waitFor(() => expect(plotted()).toEqual([[expect.objectContaining({ value: 100 }), expect.objectContaining({ value: 20 })]]))

    typeSample("equipment", 0, "equipment.value", "0")
    typeSample("equipment", 1, "equipment.value", "0")
    expect(screen.getByText("Every value is 0, so there is no pie to draw")).toBeInTheDocument()
    expect(screen.queryByTestId("chart-host-w_chart")).not.toBeInTheDocument()
    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it("gives a screen reader its values as a table: each category and each value in its column's format", async () => {
    const form = updateSchemaColumn(
      chartForm([VALUE], { title: "Values insured", category: { table: "equipment", column: "equipment.item" } }),
      "equipment",
      "equipment.value",
      { number_format: "currency_gbp" },
    )
    form.sample = {
      equipment: [
        { "equipment.item": "Crane", "equipment.value": "1000" },
        { "equipment.item": "Paver", "equipment.value": "2500.5" },
      ],
    }
    loadForm(form)
    renderChart()
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())

    expect(readOut("Values insured values")).toEqual([
      ["Category", "Value"],
      ["Crane", "£1,000.00"],
      ["Paver", "£2,500.50"],
    ])
    expect(screen.getByRole("figure", { name: "Values insured: 2 categories and 1 series." })).toBeInTheDocument()
  })

  it("plots nothing on a new quote while building, the sample in Preview, and the underwriter app's quote in the app", async () => {
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.value": "100" }] }
    loadForm(form)
    useWorkbenchPreviewStore.setState({ quote: { equipment: [{ "equipment.value": "300" }] } })

    renderChart("new")
    expect(screen.getByText("Nothing to chart yet")).toBeInTheDocument()
    await act(async () => {})
    expect(createComboChart).not.toHaveBeenCalled()
    cleanup()

    useWorkbenchViewStore.setState({ section: "preview" })
    renderChart("sample")
    await waitFor(() => expect(plotted()).toEqual([[100]]))
    expect(readOut("Chart values")).toEqual([
      ["Category", "Value"],
      ["1", "100"],
    ])
    cleanup()

    renderChart("quote")
    await waitFor(() => expect(plotted()).toEqual([[300]]))
    expect(readOut("Chart values")[1]).toEqual(["1", "300"])
  })

  it("is dimmed while it plots a price for other values, until the next pricing", async () => {
    const form = chartForm([PREMIUM], { title: "Premiums" })
    loadForm(form)
    useWorkbenchPricingStore.setState({ price: { basis: previewBasis(form), sample: {}, tables: { premiums: [{ item: "Crane", premium: 10 }] } } })
    renderChart()
    await waitFor(() => expect(plotted()).toEqual([[10]]))
    const figure = screen.getByRole("figure")
    expect(figure).not.toHaveAttribute("data-stale")
    expect(figure).toHaveStyle({ opacity: "1" })

    // The sample has moved on since it was priced.
    typeSample("policy", 0, "policy.state", "CA")
    expect(figure).toHaveAttribute("data-stale", "true")
    expect(figure).toHaveStyle({ opacity: "0.5" })

    // The runtime loaded once, each new option is set as the price arrives, not after another import.
    act(() => useWorkbenchPricingStore.setState({ price: { basis: previewBasis(currentForm()), sample: {}, tables: { premiums: [{ item: "Crane", premium: 12 }] } } }))
    expect(figure).not.toHaveAttribute("data-stale")
    expect(plotted()).toEqual([[12]])
  })

  it("resizes the chart as its host does", async () => {
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.value": "100" }] }
    loadForm(form)
    renderChart()
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())

    act(() => resizeCallback?.([], {} as ResizeObserver))
    expect(resize).toHaveBeenCalledTimes(1)
  })

  it("draws a new chart when the page's theme changes, disposing the old one", async () => {
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.value": "100" }] }
    loadForm(form)
    renderChart()
    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(1))

    act(() => {
      document.documentElement.dataset.theme = "dark"
    })
    await waitFor(() => expect(createComboChart).toHaveBeenCalledTimes(2))
    expect(dispose).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(setOption).toHaveBeenCalledTimes(2))
    expect(plotted()).toEqual([[100]])
  })

  it("shows a runtime that fails to load as an alert in the Chart's place", async () => {
    createComboChart.mockImplementationOnce(() => {
      throw new Error("The chart runtime could not be loaded")
    })
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.value": "100" }] }
    loadForm(form)
    renderChart()

    expect(await screen.findByRole("alert")).toHaveTextContent("The chart runtime could not be loaded")
  })

  it("shows options the chart refuses as an alert, until options set cleanly", async () => {
    setOption.mockImplementationOnce(() => {
      throw new Error("Unknown series type")
    })
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.value": "100" }] }
    loadForm(form)
    renderChart()

    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown series type")
    typeSample("equipment", 0, "equipment.value", "200")
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument())
    expect(plotted()).toEqual([[200]])
  })

  it("disposes the chart when it is unmounted", async () => {
    const form = chartForm([VALUE])
    form.sample = { equipment: [{ "equipment.value": "100" }] }
    loadForm(form)
    const view = renderChart()
    await waitFor(() => expect(createComboChart).toHaveBeenCalled())

    view.unmount()
    expect(dispose).toHaveBeenCalledTimes(1)
  })
})
