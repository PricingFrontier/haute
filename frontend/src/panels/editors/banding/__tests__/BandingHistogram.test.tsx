import { describe, it, expect, afterEach } from "vitest"
import { render, cleanup } from "@testing-library/react"
import { BandingHistogram } from "../BandingHistogram"
import { equalWidthBins } from "../bandingBins"

const ACCENT = "#f97316"

/** The bins the editor would draw for these values, from either source. */
const binsFor = (values: number[]) => equalWidthBins(values, 40)

describe("BandingHistogram", () => {
  afterEach(cleanup)

  it("returns null when there is no distribution to draw", () => {
    const { container } = render(
      <BandingHistogram bins={[]} boundaries={[10, 20]} accentColor={ACCENT} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("renders SVG element with correct height", () => {
    const { container } = render(
      <BandingHistogram
        bins={binsFor([1, 2, 3, 4, 5])}
        boundaries={[]}
        accentColor={ACCENT}
        height={80}
      />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute("height")).toBe("80")
  })

  it("renders SVG with default height of 50", () => {
    const { container } = render(
      <BandingHistogram bins={binsFor([1, 2, 3])} boundaries={[]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute("height")).toBe("50")
  })

  it("renders a bar for every bin that holds something", () => {
    const { container } = render(
      <BandingHistogram
        bins={[
          { lower: 0, upper: 5, count: 3 },
          { lower: 5, upper: 10, count: 0 },
          { lower: 10, upper: 15, count: 1 },
        ]}
        boundaries={[]}
        accentColor={ACCENT}
      />,
    )
    // The empty bin draws nothing, so the gap in the data is visible as a gap.
    expect(container.querySelectorAll("rect")).toHaveLength(2)
  })

  it("places a bar across the interval its bin covers", () => {
    const { container } = render(
      <BandingHistogram
        bins={[
          { lower: 0, upper: 5, count: 1 },
          { lower: 5, upper: 10, count: 2 },
        ]}
        boundaries={[]}
        accentColor={ACCENT}
      />,
    )
    const rects = Array.from(container.querySelectorAll("rect"))
    expect(rects.map((rect) => rect.getAttribute("x"))).toEqual(["0", "50"])
    expect(rects.map((rect) => rect.getAttribute("width"))).toEqual(["50", "50"])
  })

  it("renders boundary lines at correct positions", () => {
    const values = Array.from({ length: 100 }, (_, i) => i)
    const { container } = render(
      <BandingHistogram bins={binsFor(values)} boundaries={[25, 50, 75]} accentColor={ACCENT} />,
    )
    const lines = container.querySelectorAll("line")
    expect(lines).toHaveLength(3)
    lines.forEach((line) => {
      expect(line.getAttribute("stroke")).toBe(ACCENT)
    })
  })

  it("handles single value (no range)", () => {
    const { container } = render(
      <BandingHistogram bins={binsFor([42])} boundaries={[40]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
  })

  it("handles all same values", () => {
    const { container } = render(
      <BandingHistogram bins={binsFor([5, 5, 5, 5])} boundaries={[3]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
  })

  it("handles negative values", () => {
    const { container } = render(
      <BandingHistogram bins={binsFor([-10, -5, 0, 5, 10])} boundaries={[0]} accentColor={ACCENT} />,
    )
    const svg = container.querySelector("svg")
    expect(svg).toBeInTheDocument()
    const lines = container.querySelectorAll("line")
    expect(lines).toHaveLength(1)
  })
})
