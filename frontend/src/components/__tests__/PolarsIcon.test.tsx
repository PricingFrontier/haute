import { describe, it, expect, afterEach } from "vitest"
import { render, cleanup } from "@testing-library/react"
import PolarsIcon from "../PolarsIcon"

afterEach(cleanup)

describe("PolarsIcon", () => {
  it("renders SVG with default props", () => {
    const { container } = render(<PolarsIcon />)
    const svg = container.querySelector("svg")!
    expect(svg).toBeInTheDocument()
    expect(svg).toHaveAttribute("width", "16")
    expect(svg).toHaveAttribute("height", "16")
    expect(svg.querySelectorAll("rect")).toHaveLength(5)
    // Default color applied to all rects
    for (const rect of svg.querySelectorAll("rect")) {
      expect(rect).toHaveAttribute("fill", "currentColor")
    }
  })

  it("renders with custom size and color", () => {
    const { container } = render(<PolarsIcon size={32} color="#ff0000" />)
    const svg = container.querySelector("svg")!
    expect(svg).toHaveAttribute("width", "32")
    expect(svg).toHaveAttribute("height", "32")
    for (const rect of svg.querySelectorAll("rect")) {
      expect(rect).toHaveAttribute("fill", "#ff0000")
    }
  })
})
