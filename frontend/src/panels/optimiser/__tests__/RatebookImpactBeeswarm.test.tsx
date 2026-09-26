import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import RatebookImpactBeeswarm from "../RatebookImpactBeeswarm"
import type { FactorTables } from "../ratebookFactorTables"

afterEach(cleanup)

const AGE_AND_REGION: FactorTables = {
  age_band: [
    { __factor_group__: "17-24", optimal_scenario_value: 0.75, quote_count: 10 },
    { __factor_group__: "25-39", optimal_scenario_value: 1.40, quote_count: 10 },
    { __factor_group__: "40-49", optimal_scenario_value: 1.41, quote_count: 10 },
    { __factor_group__: "50-59", optimal_scenario_value: 1.42, quote_count: 10 },
    { __factor_group__: "60-69", optimal_scenario_value: 1.43, quote_count: 10 },
  ],
  region: [
    { __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 },
    { __factor_group__: "South", optimal_scenario_value: 0.98, quote_count: 10 },
  ],
}

/** `count` factors whose spread falls with their index (f0 moves most). */
function manyFactors(count: number): FactorTables {
  return Object.fromEntries(
    Array.from({ length: count }, (_, i) => [
      `f${i}`,
      [
        { __factor_group__: "lo", optimal_scenario_value: 1 - (count - i) / 100, quote_count: 5 },
        { __factor_group__: "hi", optimal_scenario_value: 1 + (count - i) / 100, quote_count: 5 },
      ],
    ]),
  )
}

function factorLabels(): string[] {
  return screen.getAllByTestId("ratebook-impact-factor").map((label) => label.textContent ?? "")
}

describe("RatebookImpactBeeswarm", () => {
  it("draws each factor's levels as dots labelled with their change against 1.0", () => {
    render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} />)

    expect(screen.getByText("Mechanical Price Effect")).toBeInTheDocument()
    expect(screen.getByTestId("ratebook-impact-beeswarm")).toBeInTheDocument()
    expect(factorLabels()).toEqual(["age_band", "region"])
    expect(screen.getByLabelText("age_band 17-24: -25.0%")).toBeInTheDocument()
    expect(screen.getByLabelText("age_band 25-39: +40.0%")).toBeInTheDocument()
    expect(screen.getByText("Log rate effect")).toBeInTheDocument()
    expect(screen.getByText("Factor value")).toBeInTheDocument()
    expect(screen.getByText("Low")).toBeInTheDocument()
    expect(screen.getByText("High")).toBeInTheDocument()

    const decreasingDot = screen.getByLabelText("age_band 17-24: -25.0%")
    const increasingDots = [
      screen.getByLabelText("age_band 25-39: +40.0%"),
      screen.getByLabelText("age_band 40-49: +41.0%"),
      screen.getByLabelText("age_band 50-59: +42.0%"),
      screen.getByLabelText("age_band 60-69: +43.0%"),
    ]
    expect(decreasingDot).toHaveAttribute("data-impact-direction", "decreasing")
    expect(increasingDots[0]).toHaveAttribute("data-impact-direction", "increasing")
    expect(decreasingDot).toHaveAttribute("data-factor-value-position", "0.00")
    expect(increasingDots[3]).toHaveAttribute("data-factor-value-position", "1.00")
    expect(decreasingDot).toHaveAttribute(
      "fill",
      "color-mix(in srgb, var(--chart-impact-value-low) 100%, var(--chart-impact-value-high) 0%)",
    )
    expect(increasingDots[3]).toHaveAttribute(
      "fill",
      "color-mix(in srgb, var(--chart-impact-value-low) 0%, var(--chart-impact-value-high) 100%)",
    )
    expect(new Set(increasingDots.map((dot) => dot.getAttribute("cy"))).size).toBeGreaterThan(1)
  })

  it("orders factors by quote-count weighted rate spread", () => {
    render(
      <RatebookImpactBeeswarm
        factorTables={{
          sparse_extreme: [
            { __factor_group__: "Rare", optimal_scenario_value: 2.50, quote_count: 1 },
            { __factor_group__: "Common", optimal_scenario_value: 1.00, quote_count: 999 },
          ],
          common_moderate: [
            { __factor_group__: "Low", optimal_scenario_value: 0.90, quote_count: 500 },
            { __factor_group__: "High", optimal_scenario_value: 1.10, quote_count: 500 },
          ],
        }}
      />,
    )

    expect(factorLabels()).toEqual(["common_moderate", "sparse_extreme"])
  })

  it("colours dash-separated numeric bands across unicode dash variants", () => {
    render(
      <RatebookImpactBeeswarm
        factorTables={{
          age_band: [
            { __factor_group__: "18–19", optimal_scenario_value: 0.95, quote_count: 10 },
            { __factor_group__: "20-29", optimal_scenario_value: 1.00, quote_count: 10 },
            { __factor_group__: "30 − 39", optimal_scenario_value: 1.05, quote_count: 10 },
            { __factor_group__: "40 - 49", optimal_scenario_value: 1.10, quote_count: 10 },
          ],
        }}
      />,
    )

    expect(screen.getByLabelText("age_band 18–19: -5.0%")).toHaveAttribute("data-factor-value-position", "0.00")
    expect(screen.getByLabelText("age_band 20-29: 0.0%")).not.toHaveAttribute("data-factor-value-position", "unknown")
    expect(screen.getByLabelText("age_band 30 − 39: +5.0%")).not.toHaveAttribute("data-factor-value-position", "unknown")
    expect(screen.getByLabelText("age_band 40 - 49: +10.0%")).toHaveAttribute("data-factor-value-position", "1.00")
  })

  it("colours dots by factor value rather than impact direction", () => {
    render(
      <RatebookImpactBeeswarm
        factorTables={{
          net_premium: [
            { __factor_group__: "<200", optimal_scenario_value: 1.25, quote_count: 10 },
            { __factor_group__: ">=400", optimal_scenario_value: 0.80, quote_count: 10 },
          ],
          region: [{ __factor_group__: "North", optimal_scenario_value: 1.05, quote_count: 10 }],
        }}
      />,
    )

    const lowValueIncreasingDot = screen.getByLabelText("net_premium <200: +25.0%")
    const highValueDecreasingDot = screen.getByLabelText("net_premium >=400: -20.0%")
    const unorderedCategoryDot = screen.getByLabelText("region North: +5.0%")

    expect(lowValueIncreasingDot).toHaveAttribute("data-impact-direction", "increasing")
    expect(lowValueIncreasingDot).toHaveAttribute("data-factor-value-position", "0.00")
    expect(lowValueIncreasingDot).toHaveAttribute(
      "fill",
      "color-mix(in srgb, var(--chart-impact-value-low) 100%, var(--chart-impact-value-high) 0%)",
    )
    expect(highValueDecreasingDot).toHaveAttribute("data-impact-direction", "decreasing")
    expect(highValueDecreasingDot).toHaveAttribute("data-factor-value-position", "1.00")
    expect(highValueDecreasingDot).toHaveAttribute(
      "fill",
      "color-mix(in srgb, var(--chart-impact-value-low) 0%, var(--chart-impact-value-high) 100%)",
    )
    expect(unorderedCategoryDot).toHaveAttribute("data-factor-value-position", "unknown")
    expect(unorderedCategoryDot).toHaveAttribute("fill", "var(--chart-impact-value-neutral)")
  })

  it("renders nothing when there are no factor tables", () => {
    const { container } = render(<RatebookImpactBeeswarm factorTables={{}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("throws on a non-positive rate", () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    expect(() =>
      render(
        <RatebookImpactBeeswarm
          factorTables={{ region: [{ __factor_group__: "North", optimal_scenario_value: 0, quote_count: 5 }] }}
        />,
      ),
    ).toThrow("Factor region level North has a non-positive rate 0")
    vi.mocked(console.error).mockRestore()
  })

  describe("responsive geometry", () => {
    it("draws at the pane's pixel width with no viewBox scaling and no minimum width", () => {
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} width={360} />)

      const svg = screen.getByTestId("ratebook-impact-beeswarm")
      expect(svg).toHaveAttribute("width", "360")
      expect(svg).not.toHaveAttribute("viewBox")
      const section = screen.getByRole("region", { name: "Mechanical Price Effect" })
      expect(section.className).not.toMatch(/min-w-\[/)
      expect(section.className).toMatch(/min-w-0/)
    })

    it("keeps every dot and the colour bar inside a narrow chart", () => {
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} width={300} />)

      for (const dot of document.querySelectorAll("circle")) {
        const cx = Number(dot.getAttribute("cx"))
        expect(cx).toBeGreaterThan(0)
        expect(cx).toBeLessThan(300)
      }
      const colourBar = document.querySelector("[data-testid=ratebook-impact-colour-bar]")!
      expect(Number(colourBar.getAttribute("x")) + Number(colourBar.getAttribute("width"))).toBeLessThanOrEqual(300)
    })

    it("widens the plot with the pane", () => {
      const { unmount } = render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} width={400} />)
      const narrowSpread = spreadOfDots()
      unmount()
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} width={900} />)
      expect(spreadOfDots()).toBeGreaterThan(narrowSpread)
    })
  })

  describe("truncation", () => {
    it("says how many factors it hides and offers All", () => {
      render(<RatebookImpactBeeswarm factorTables={manyFactors(11)} />)

      expect(screen.getByText("Showing top 8 of 11 factors")).toBeInTheDocument()
      expect(factorLabels()).toEqual(["f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7"])
      expect(screen.getByRole("button", { name: "Top 8" })).toHaveAttribute("aria-pressed", "true")

      fireEvent.click(screen.getByRole("button", { name: "All" }))

      expect(factorLabels()).toHaveLength(11)
      expect(screen.getByText("Showing all 11 factors")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true")

      fireEvent.click(screen.getByRole("button", { name: "Top 8" }))
      expect(factorLabels()).toHaveLength(8)
    })

    it("has no notice or toggle when every factor fits", () => {
      render(<RatebookImpactBeeswarm factorTables={manyFactors(8)} />)

      expect(factorLabels()).toHaveLength(8)
      expect(screen.queryByText(/Showing/)).not.toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "All" })).not.toBeInTheDocument()
      expect(screen.getByText("8 factors")).toBeInTheDocument()
    })
  })

  describe("categorical levels", () => {
    it("names the neutral colour in a legend note when a level has no value order", () => {
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} />)

      expect(screen.getByText("No value order (categorical level)")).toBeInTheDocument()
    })

    it("omits the note when every level has a numeric value", () => {
      render(<RatebookImpactBeeswarm factorTables={{ age_band: AGE_AND_REGION.age_band }} />)

      expect(screen.queryByText("No value order (categorical level)")).not.toBeInTheDocument()
    })

    it("says in words, on focus, that a categorical dot's colour encodes no value", () => {
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} />)

      const detail = screen.getByRole("status")
      expect(detail).toHaveTextContent("Hover or focus a dot to inspect its level.")

      const north = screen.getByLabelText("region North: +5.0%")
      expect(north).toHaveAttribute("tabindex", "0")
      fireEvent.focus(north)

      expect(detail).toHaveTextContent("region North")
      expect(detail).toHaveTextContent("Rate: 1.0500")
      expect(detail).toHaveTextContent("vs neutral 1.0: +5.0%")
      expect(detail).toHaveTextContent("Quotes: 10")
      expect(detail).toHaveTextContent("Colour: no value order (categorical level)")
    })

    it("places an ordered level on the value scale in the focus detail", () => {
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} />)

      fireEvent.focus(screen.getByLabelText("age_band 60-69: +43.0%"))

      expect(screen.getByRole("status")).toHaveTextContent("Colour: factor value, high end (1.00 of low 0 to high 1)")
    })
  })

  describe("values table", () => {
    it("lists every shown dot as Factor | Level | Rate | vs neutral 1.0 (%) | Quotes", () => {
      render(<RatebookImpactBeeswarm factorTables={AGE_AND_REGION} />)

      const table = screen.getByRole("table", { name: "Mechanical price effect values", hidden: true })
      expect(Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent)).toEqual([
        "Factor",
        "Level",
        "Rate",
        "vs neutral 1.0 (%)",
        "Quotes",
      ])
      const rows = Array.from(table.querySelectorAll("tbody tr")).map((row) =>
        Array.from(row.children).map((cell) => cell.textContent),
      )
      expect(rows).toHaveLength(7)
      expect(rows[0]).toEqual(["age_band", "17-24", "0.7500", "-25.0%", "10"])
      expect(rows[6]).toEqual(["region", "South", "0.9800", "-2.0%", "10"])
    })

    it("follows the Top 8 / All toggle", () => {
      render(<RatebookImpactBeeswarm factorTables={manyFactors(10)} />)

      const table = () => screen.getByRole("table", { name: "Mechanical price effect values", hidden: true })
      expect(table().querySelectorAll("tbody tr")).toHaveLength(16)
      fireEvent.click(screen.getByRole("button", { name: "All" }))
      expect(table().querySelectorAll("tbody tr")).toHaveLength(20)
    })
  })
})

function spreadOfDots(): number {
  const xs = Array.from(document.querySelectorAll("circle")).map((dot) => Number(dot.getAttribute("cx")))
  return Math.max(...xs) - Math.min(...xs)
}
