import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import FrontierChart from "../FrontierChart"

describe("FrontierChart", () => {
  afterEach(cleanup)

  it("separates overlapping frontier points into distinct pointer targets", () => {
    render(
      <FrontierChart
        points={[
          { total_objective: 100, total_loss_ratio: 0.55 },
          { total_objective: 110, total_loss_ratio: 0.57 },
          { total_objective: 120, total_loss_ratio: 0.59 },
          { total_objective: 130, total_loss_ratio: 0.61 },
          { total_objective: 110, total_loss_ratio: 0.57 },
        ]}
        xKey="total_loss_ratio"
        yKey="total_objective"
        xLabel="loss_ratio"
        selectedIdx={null}
        currentX={0.57}
        currentY={110}
        onPointClick={vi.fn()}
      />,
    )

    const pointTwo = screen.getByRole("button", { name: "Select frontier point 2" })
    const pointFive = screen.getByRole("button", { name: "Select frontier point 5" })

    expect(`${pointTwo.getAttribute("cx")}:${pointTwo.getAttribute("cy")}`).not.toBe(
      `${pointFive.getAttribute("cx")}:${pointFive.getAttribute("cy")}`,
    )
  })

  it("keeps the current solve marker decorative so overlapping points remain clickable", () => {
    render(
      <FrontierChart
        points={[
          { total_objective: 100, total_loss_ratio: 0.55 },
          { total_objective: 110, total_loss_ratio: 0.57 },
          { total_objective: 120, total_loss_ratio: 0.59 },
        ]}
        xKey="total_loss_ratio"
        yKey="total_objective"
        xLabel="loss_ratio"
        selectedIdx={null}
        currentX={0.57}
        currentY={110}
        onPointClick={vi.fn()}
      />,
    )

    expect(screen.getByRole("button", { name: "Select frontier point 2" })).toBeInTheDocument()

    const decorativeMarker = document.querySelector("g[aria-hidden='true']")
    expect(decorativeMarker).toHaveStyle({ pointerEvents: "none" })
    expect(decorativeMarker).toHaveAttribute("pointer-events", "none")

    const markerCircles = decorativeMarker?.querySelectorAll("circle") ?? []
    expect(markerCircles).toHaveLength(2)
    markerCircles.forEach(circle => {
      expect(circle).toHaveAttribute("pointer-events", "none")
    })
  })
})
