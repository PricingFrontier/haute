import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import FrontierChart from "../FrontierChart"

describe("FrontierChart", () => {
  afterEach(cleanup)

  it("keeps overlapping frontier rows at the true coordinate without visual spread", () => {
    const onPointClick = vi.fn()
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
        onPointClick={onPointClick}
      />,
    )

    const pointTwo = screen.getByRole("button", { name: /Select frontier point 2/ })
    const pointFive = screen.getByRole("button", { name: "Select frontier point 5" })

    expect(pointTwo).toHaveAttribute("data-overlap-count", "2")
    expect(pointTwo).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(`${pointTwo.getAttribute("cx")}:${pointTwo.getAttribute("cy")}`).toBe(
      `${pointFive.getAttribute("cx")}:${pointFive.getAttribute("cy")}`,
    )
    expect(pointFive).toHaveAttribute("r", "0")
    expect(pointFive).toHaveAttribute("opacity", "0")
    expect(pointFive).toHaveAttribute("pointer-events", "none")
    expect(screen.getAllByRole("button")).toHaveLength(5)

    fireEvent.click(pointTwo)
    expect(onPointClick).toHaveBeenCalledWith(1)

    fireEvent.keyDown(pointFive, { key: "Enter" })
    expect(onPointClick).toHaveBeenCalledWith(4)
  })

  it("uses the selected duplicate as the visible overlapping point representative", () => {
    render(
      <FrontierChart
        points={[
          { total_objective: 100, total_loss_ratio: 0.55 },
          { total_objective: 110, total_loss_ratio: 0.57 },
          { total_objective: 120, total_loss_ratio: 0.59 },
          { total_objective: 110, total_loss_ratio: 0.57 },
        ]}
        xKey="total_loss_ratio"
        yKey="total_objective"
        xLabel="loss_ratio"
        selectedIdx={3}
        currentX={0.57}
        currentY={110}
        onPointClick={vi.fn()}
      />,
    )

    const selectedDuplicate = screen.getByRole("button", { name: /Select frontier point 4/ })
    const firstDuplicate = screen.getByRole("button", { name: "Select frontier point 2" })

    expect(selectedDuplicate).toHaveAttribute("data-overlap-count", "2")
    expect(selectedDuplicate).toHaveAccessibleName("Select frontier point 4 (2 overlapping frontier points)")
    expect(selectedDuplicate).toHaveAttribute("r", "6")
    expect(firstDuplicate).toHaveAttribute("r", "0")
    expect(`${selectedDuplicate.getAttribute("cx")}:${selectedDuplicate.getAttribute("cy")}`).toBe(
      `${firstDuplicate.getAttribute("cx")}:${firstDuplicate.getAttribute("cy")}`,
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
