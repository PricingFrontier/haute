import { afterEach, describe, expect, it, vi } from "vitest"
import { useState } from "react"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import RatebookRatesTab, { type RatesFactorSelection } from "../RatebookRatesTab"
import type { FactorTables } from "../ratebookFactorTables"

afterEach(cleanup)

/** Three factors whose rate spreads rank vehicle_age, then region, then flat. */
const TABLES: FactorTables = {
  flat: [
    { __factor_group__: "A", optimal_scenario_value: 1, quote_count: 50 },
    { __factor_group__: "B", optimal_scenario_value: 1.01, quote_count: 50 },
  ],
  region: [
    { __factor_group__: "North", optimal_scenario_value: 1.1, quote_count: 300 },
    { __factor_group__: "South", optimal_scenario_value: 0.95, quote_count: 100 },
  ],
  vehicle_age: [
    { __factor_group__: "10-11", optimal_scenario_value: 0.7, quote_count: 20 },
    { __factor_group__: "1-3", optimal_scenario_value: 1.25, quote_count: 60 },
    { __factor_group__: "missing", optimal_scenario_value: 0.8, quote_count: 10 },
    { __factor_group__: "4-5", optimal_scenario_value: 1.4, quote_count: 30 },
  ],
}

const BANDING = { vehicle_age: ["1-3", "4-5", "10-11", "missing"] }

function browserFactors(): string[] {
  return Array.from(document.querySelectorAll(".validation-feature-list button")).map(
    (button) => button.getAttribute("aria-label") ?? "",
  )
}

function levelRows(): HTMLElement[] {
  return screen.getAllByTestId("relativity-row")
}

function valuesTable(): HTMLTableElement {
  return screen.getByRole("table", { name: /rate values/i, hidden: true }) as HTMLTableElement
}

function tableRows(): string[][] {
  return Array.from(valuesTable().querySelectorAll("tbody tr")).map((row) =>
    Array.from(row.children).map((cell) => cell.textContent ?? ""),
  )
}

describe("RatebookRatesTab", () => {
  it("ranks factors by rate spread, names the ranking, and opens the widest-moving factor", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    expect(browserFactors()).toEqual(["vehicle_age", "region", "flat"])
    expect(screen.getByText("Rate spread")).toBeInTheDocument()
    expect(screen.queryByText(/importance/i)).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "vehicle_age" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "vehicle_age", pressed: true })).toBeInTheDocument()
  })

  it("searches factors", () => {
    render(<RatebookRatesTab factorTables={TABLES} />)

    fireEvent.change(screen.getByRole("textbox", { name: "Search factors" }), { target: { value: "reg" } })

    expect(browserFactors()).toEqual(["region"])
    fireEvent.change(screen.getByRole("textbox", { name: "Search factors" }), { target: { value: "zzz" } })
    expect(screen.getByText("No factors found")).toBeInTheDocument()
  })

  it("draws the selected factor's levels in banding order, in the bars and the values table", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    expect(levelRows().map((row) => row.getAttribute("data-key"))).toEqual(["1-3", "4-5", "10-11", "missing"])
    expect(tableRows().map((row) => row[0])).toEqual(["1-3", "4-5", "10-11", "missing"])
  })

  it("keeps the solve's level order when no banding order is configured", () => {
    render(<RatebookRatesTab factorTables={TABLES} />)

    expect(levelRows().map((row) => row.getAttribute("data-key"))).toEqual(["10-11", "1-3", "missing", "4-5"])
  })

  it("switches factor from the browser", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    fireEvent.click(screen.getByRole("button", { name: "region" }))

    expect(screen.getByRole("heading", { name: "region" })).toBeInTheDocument()
    expect(levelRows().map((row) => row.getAttribute("data-key"))).toEqual(["North", "South"])
  })

  it("tabulates Level | Rate | vs neutral 1.0 (%) | Quotes | Share with shares summing to 100", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    const headers = Array.from(valuesTable().querySelectorAll("thead th")).map((th) => th.textContent)
    expect(headers).toEqual(["Level", "Rate", "vs neutral 1.0 (%)", "Quotes", "Share"])
    expect(tableRows()).toEqual([
      ["1-3", "1.2500", "+25.0%", "60", "50.0%"],
      ["4-5", "1.4000", "+40.0%", "30", "25.0%"],
      ["10-11", "0.7000", "-30.0%", "20", "16.7%"],
      ["missing", "0.8000", "-20.0%", "10", "8.3%"],
    ])
    const shareTotal = tableRows().reduce((total, row) => total + Number(row[4].replace("%", "")), 0)
    expect(Math.round(shareTotal * 10)).toBe(1000)
  })

  it("summarises the factor under its heading", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    expect(screen.getByText("4 levels · 120 quotes · rates 0.7000 to 1.4000")).toBeInTheDocument()
  })

  it("aligns a quote-count strip with the levels, scaled to the largest level", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    expect(screen.getByText("Quotes 0–60")).toBeInTheDocument()
    const quoteBars = levelRows().map((row) => within(row).getByTestId("quote-strip-bar"))
    expect(quoteBars.map((bar) => bar.getAttribute("title"))).toEqual([
      "1-3: 60 quotes",
      "4-5: 30 quotes",
      "10-11: 20 quotes",
      "missing: 10 quotes",
    ])
    expect(quoteBars.map((bar) => bar.style.width)).toEqual(["100%", "50%", "33.33333333333333%", "16.666666666666664%"])
  })

  it("fills the detail line from the keyboard and emphasises the focused level's quotes", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    const detail = screen.getByRole("status")
    expect(detail).toHaveTextContent("Hover or focus a level to inspect its rate.")

    const level = screen.getByRole("button", { name: /^4-5\./ })
    expect(level).toHaveAttribute("tabindex", "0")
    fireEvent.focus(level)

    expect(detail).toHaveTextContent("4-5")
    expect(detail).toHaveTextContent("Rate: 1.4000")
    expect(detail).toHaveTextContent("vs neutral 1.0: +40.0%")
    expect(detail).toHaveTextContent("Quotes: 30")
    expect(detail).toHaveTextContent("Share: 25.0%")
    expect(level).toHaveAttribute("aria-pressed", "true")
    expect(within(level).getByTestId("quote-strip-bar").style.opacity).toBe("0.65")
  })

  it("names each level fully for assistive technology", () => {
    render(<RatebookRatesTab factorTables={TABLES} factorLevelOrder={BANDING} />)

    expect(
      screen.getByRole("button", {
        name: "10-11. Rate 0.7000, -30.0% vs neutral 1.0. 20 quotes, 16.7% of the factor's quotes.",
      }),
    ).toBeInTheDocument()
  })

  it("thins level labels for a high-cardinality factor but keeps every level focusable and tabulated", () => {
    const age = Array.from({ length: 83 }, (_, i) => ({
      __factor_group__: String(17 + i),
      optimal_scenario_value: 0.8 + i / 200,
      quote_count: 10 + i,
    }))
    render(<RatebookRatesTab factorTables={{ driver_age: age }} />)

    const labels = screen
      .getAllByTestId("relativity-label")
      .map((label) => label.textContent)
      .filter((text) => text !== "")
    expect(labels.length).toBeLessThan(83)
    expect(labels[0]).toBe("17")
    expect(labels[labels.length - 1]).toBe("99")
    expect(levelRows()).toHaveLength(83)
    expect(tableRows()).toHaveLength(83)
  })

  it("uses a caller-owned selection and search, so the choice outlives the tab", () => {
    const onSelect = vi.fn()
    const onSearch = vi.fn()
    const selection: RatesFactorSelection = { selected: "region", onSelect, search: "", onSearch }
    render(<RatebookRatesTab factorTables={TABLES} selection={selection} />)

    expect(screen.getByRole("heading", { name: "region" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "flat" }))
    expect(onSelect).toHaveBeenCalledWith("flat")
    fireEvent.change(screen.getByRole("textbox", { name: "Search factors" }), { target: { value: "v" } })
    expect(onSearch).toHaveBeenCalledWith("v")
  })

  it("restores a lifted selection after the tab unmounts and mounts again", () => {
    function Host() {
      const [shown, setShown] = useState(true)
      const [selected, setSelected] = useState<string | null>(null)
      const [search, setSearch] = useState("")
      return (
        <>
          <button type="button" onClick={() => setShown((current) => !current)}>
            toggle
          </button>
          {shown && (
            <RatebookRatesTab
              factorTables={TABLES}
              selection={{ selected, onSelect: setSelected, search, onSearch: setSearch }}
            />
          )}
        </>
      )
    }
    render(<Host />)

    fireEvent.click(screen.getByRole("button", { name: "region" }))
    fireEvent.click(screen.getByRole("button", { name: "toggle" }))
    fireEvent.click(screen.getByRole("button", { name: "toggle" }))

    expect(screen.getByRole("heading", { name: "region" })).toBeInTheDocument()
  })

  it("opens the top-ranked factor when the lifted selection names a factor this result lacks", () => {
    const selection: RatesFactorSelection = { selected: "gone", onSelect: () => {}, search: "", onSearch: () => {} }
    render(<RatebookRatesTab factorTables={TABLES} selection={selection} />)

    expect(screen.getByRole("heading", { name: "vehicle_age" })).toBeInTheDocument()
  })

  it("throws on a non-positive rate rather than drawing it", () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    expect(() =>
      render(
        <RatebookRatesTab
          factorTables={{ region: [{ __factor_group__: "North", optimal_scenario_value: -1, quote_count: 5 }] }}
        />,
      ),
    ).toThrow("Factor region level North has a non-positive rate -1")
    vi.mocked(console.error).mockRestore()
  })
})
