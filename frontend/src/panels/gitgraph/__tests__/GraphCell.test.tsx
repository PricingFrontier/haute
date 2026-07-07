import { describe, it, expect, afterEach } from "vitest"
import { render, cleanup } from "@testing-library/react"
import { GraphRailOverlay } from "../GraphCell"
import type { RailRun } from "../layout"

afterEach(cleanup)

// A consolidated run at some x, spanning y1=20 (top) → y2=100 (bottom).
const run = (over: Partial<RailRun> = {}): RailRun => ({
  kind: "spine",
  x: 10,
  y1: 20,
  y2: 100,
  dotted: false,
  branch: "trunk",
  colorIndex: 0,
  ...over,
})

/** The overlay swallows nothing, so a no-op menu handler is fine. */
const noop = () => {}

describe("GraphRailOverlay — dotted runs render bottom-anchored", () => {
  it("draws a dotted run with its dash phase 0 at the BOTTOM end (endpoints swapped)", () => {
    const { container } = render(
      <GraphRailOverlay runs={[run({ dotted: true })]} dimmed={false} onLaneContextMenu={noop} />,
    )
    const line = container.querySelector("line[data-run]")
    expect(line).not.toBeNull()
    // Bottom-anchored: y1 takes the run's y2 (bottom), y2 takes y1 (top) — so
    // the SVG dash pattern, which starts phase 0 at the path start, begins at
    // the run's bottom, meeting the dotted transition/dot seam without a break.
    expect(line?.getAttribute("y1")).toBe("100") // run.y2
    expect(line?.getAttribute("y2")).toBe("20") // run.y1
    expect(line?.getAttribute("stroke-dasharray")).toBe("1.5 3.5")
  })

  it("keeps a solid run top→bottom (endpoints in natural order, no dash)", () => {
    const { container } = render(
      <GraphRailOverlay runs={[run({ dotted: false })]} dimmed={false} onLaneContextMenu={noop} />,
    )
    const line = container.querySelector("line[data-run]")
    expect(line).not.toBeNull()
    expect(line?.getAttribute("y1")).toBe("20") // run.y1
    expect(line?.getAttribute("y2")).toBe("100") // run.y2
    expect(line?.getAttribute("stroke-dasharray")).toBeNull()
  })

  it("anchors a dotted SIDING run at the bottom too (same swap, thinner stroke)", () => {
    const { container } = render(
      <GraphRailOverlay
        runs={[run({ kind: "siding", dotted: true, x: 15 })]}
        dimmed={false}
        onLaneContextMenu={noop}
      />,
    )
    const line = container.querySelector("line[data-run]")
    expect(line?.getAttribute("y1")).toBe("100")
    expect(line?.getAttribute("y2")).toBe("20")
    expect(line?.getAttribute("data-edge-kind")).toBe("sub-rail")
  })
})
