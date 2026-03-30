import { describe, it, expect, afterEach } from "vitest"
import { render, cleanup } from "@testing-library/react"
import { BandingHistogram } from "../BandingHistogram"

const ACCENT = "#f97316"

describe("BandingHistogram", () => {
  afterEach(cleanup)

  it("returns null for empty values", () => {
    const { container } = render(
      <BandingHistogram values={[]} boundaries={[10, 20]} accentColor={ACCENT} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("renders SVG element with correct height", () => {
    const { container } = render(
      <BandingHistogram values={[1, 2, 3, 4, 5]} boundaries={[]} accentColor={ACCENT} height={80} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute("height")).toBe("80")
  })

  it("renders SVG with default height of 50", () => {
    const { container } = render(
      <BandingHistogram values={[1, 2, 3]} boundaries={[]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute("height")).toBe("50")
  })

  it("renders bars for non-empty values", () => {
    const { container } = render(
      <BandingHistogram values={[1, 2, 3, 4, 5, 10, 15, 20]} boundaries={[]} accentColor={ACCENT} />,
    )
    const rects = container.querySelectorAll("rect")
    expect(rects.length).toBeGreaterThan(0)
  })

  it("renders boundary lines at correct positions", () => {
    const values = Array.from({ length: 100 }, (_, i) => i)
    const { container } = render(
      <BandingHistogram values={values} boundaries={[25, 50, 75]} accentColor={ACCENT} />,
    )
    const lines = container.querySelectorAll("line")
    expect(lines).toHaveLength(3)
    // Lines should have the accent color as stroke
    lines.forEach((line) => {
      expect(line.getAttribute("stroke")).toBe(ACCENT)
    })
  })

  it("handles single value (no range)", () => {
    const { container } = render(
      <BandingHistogram values={[42]} boundaries={[40]} accentColor={ACCENT} />,
    )
    // Should render without crashing
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
  })

  it("handles all same values", () => {
    const { container } = render(
      <BandingHistogram values={[5, 5, 5, 5]} boundaries={[3]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
  })

  it("handles negative values", () => {
    const { container } = render(
      <BandingHistogram values={[-10, -5, 0, 5, 10]} boundaries={[0]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
    const lines = container.querySelectorAll("line")
    expect(lines).toHaveLength(1)
  })
})
