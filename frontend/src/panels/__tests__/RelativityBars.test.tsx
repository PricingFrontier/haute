import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { RelativityBars, type RelativityBar } from "../RelativityBars"

afterEach(cleanup)

const BARS: RelativityBar[] = [
  { key: "a", label: "Low", value: 0.8 },
  { key: "b", label: "Mid", value: 1 },
  { key: "c", label: "High", value: 1.4 },
]

function barFill(row: HTMLElement): HTMLElement {
  return row.querySelector<HTMLElement>("[data-relativity-bar]")!
}

describe("RelativityBars", () => {
  it("draws one row per bar in the caller's order, diverging from 1.0 in the chart tokens", () => {
    render(<RelativityBars ariaLabel="Rates" bars={BARS} />)

    const rows = screen.getAllByTestId("relativity-row")
    expect(rows.map((row) => row.getAttribute("data-key"))).toEqual(["a", "b", "c"])
    expect(barFill(rows[0]).style.background).toBe("var(--chart-below)")
    expect(barFill(rows[2]).style.background).toBe("var(--chart-above)")
    // The largest deviation (0.4) fills half the track; 0.2 fills a quarter.
    expect(barFill(rows[2]).style.width).toBe("50%")
    expect(barFill(rows[0]).style.width).toBe("25%")
    expect(barFill(rows[0]).style.left).toBe("25%")
    expect(barFill(rows[2]).style.left).toBe("50%")
  })

  it("draws the baseline and whiskers with theme tokens, never raw colour literals", () => {
    const { container } = render(
      <RelativityBars
        ariaLabel="Relativities"
        bars={[{ key: "x", label: "x", value: 1.2, ciLower: 1.1, ciUpper: 1.3 }]}
      />,
    )

    expect(container.innerHTML).not.toMatch(/rgba?\(/)
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b/i)
    expect(container.querySelector("[data-relativity-baseline]")).not.toBeNull()
    expect(container.querySelector("[data-relativity-whisker]")).not.toBeNull()
  })

  it("scales so confidence whiskers stay inside the track", () => {
    render(
      <RelativityBars
        ariaLabel="Relativities"
        bars={[{ key: "x", label: "x", value: 1.1, ciLower: 0.6, ciUpper: 1.2 }]}
      />,
    )

    const whisker = document.querySelector<HTMLElement>("[data-relativity-whisker]")!
    const left = parseFloat(whisker.style.left)
    const width = parseFloat(whisker.style.width)
    expect(left).toBeGreaterThanOrEqual(0)
    expect(left + width).toBeLessThanOrEqual(100)
  })

  it("throws on a non-finite value instead of drawing a neutral bar", () => {
    vi.spyOn(console, "error").mockImplementation(() => {})
    expect(() =>
      render(<RelativityBars ariaLabel="Rates" bars={[{ key: "x", label: "x", value: Number.NaN }]} />),
    ).toThrow(/x has a non-finite value/)
    vi.mocked(console.error).mockRestore()
  })

  it("makes rows focusable and reports the active row when interactive", () => {
    const onActivate = vi.fn()
    render(
      <RelativityBars
        ariaLabel="Rates"
        bars={BARS}
        interaction={{
          activeKey: "b",
          onActivate,
          describe: (bar) => `${bar.label} rate ${bar.value}`,
        }}
      />,
    )

    const high = screen.getByRole("button", { name: "High rate 1.4" })
    expect(high).toHaveAttribute("tabindex", "0")
    expect(screen.getByRole("button", { name: "Mid rate 1" })).toHaveAttribute("aria-pressed", "true")
    expect(high).toHaveAttribute("aria-pressed", "false")

    fireEvent.focus(high)
    expect(onActivate).toHaveBeenLastCalledWith("c")
    fireEvent.mouseEnter(screen.getByRole("button", { name: "Low rate 0.8" }))
    expect(onActivate).toHaveBeenLastCalledWith("a")
  })

  it("renders an aligned side strip cell in every row, under its header", () => {
    render(
      <RelativityBars
        ariaLabel="Rates"
        bars={BARS}
        aside={{
          header: "Quotes",
          width: 80,
          render: (bar, active) => <span data-testid="aside">{`${bar.label}:${active}`}</span>,
        }}
        interaction={{ activeKey: "c", onActivate: () => {}, describe: (bar) => bar.label }}
      />,
    )

    expect(screen.getByText("Quotes")).toBeInTheDocument()
    const rows = screen.getAllByTestId("relativity-row")
    expect(rows.map((row) => within(row).getByTestId("aside").textContent)).toEqual([
      "Low:false",
      "Mid:false",
      "High:true",
    ])
  })

  it("keeps every label below the compact threshold", () => {
    const bars = Array.from({ length: 24 }, (_, i) => ({ key: `k${i}`, label: `L${i}`, value: 1 + i / 100 }))
    render(<RelativityBars ariaLabel="Rates" bars={bars} compactFrom={25} />)

    expect(screen.getAllByTestId("relativity-label").map((label) => label.textContent)).toEqual(
      bars.map((bar) => bar.label),
    )
  })

  it("thins labels on compact rows, keeping the first and last and every row focusable", () => {
    const bars = Array.from({ length: 80 }, (_, i) => ({ key: `k${i}`, label: `Age ${i + 17}`, value: 0.8 + i / 200 }))
    render(
      <RelativityBars
        ariaLabel="Rates"
        bars={bars}
        compactFrom={25}
        interaction={{ activeKey: null, onActivate: () => {}, describe: (bar) => bar.label }}
      />,
    )

    const shown = screen
      .getAllByTestId("relativity-label")
      .map((label) => label.textContent)
      .filter((text) => text !== "")
    expect(shown.length).toBeGreaterThan(2)
    expect(shown.length).toBeLessThan(80)
    expect(shown[0]).toBe("Age 17")
    expect(shown[shown.length - 1]).toBe("Age 96")
    expect(screen.getAllByRole("button")).toHaveLength(80)
    expect(screen.getByRole("button", { name: "Age 50" })).toBeInTheDocument()
  })
})
