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

    expect(pointTwo).toHaveAttribute("data-overlap-count", "2")
    expect(pointTwo).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(pointTwo).toHaveAttribute("r", "4")
    expect(screen.queryByRole("button", { name: "Select frontier point 5" })).not.toBeInTheDocument()
    expect(screen.getAllByRole("button")).toHaveLength(4)

    fireEvent.click(pointTwo)
    expect(onPointClick).toHaveBeenCalledWith(1)

    fireEvent.keyDown(pointTwo, { key: "Enter" })
    expect(onPointClick).toHaveBeenCalledTimes(2)
    expect(onPointClick).toHaveBeenLastCalledWith(1)
  })

  it("highlights the aggregate marker when a duplicate coordinate is selected", () => {
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

    const aggregatePoint = screen.getByRole("button", { name: /Select frontier point 2/ })

    expect(aggregatePoint).toHaveAttribute("data-overlap-count", "2")
    expect(aggregatePoint).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(aggregatePoint).toHaveAttribute("r", "6")
    expect(screen.queryByRole("button", { name: "Select frontier point 4" })).not.toBeInTheDocument()
    expect(screen.getAllByRole("button")).toHaveLength(3)
  })

  it("keeps point 2 as the visible aggregate target when the first overlapping row is selected", () => {
    const onPointClick = vi.fn()
    render(
      <FrontierChart
        points={[
          { total_objective: 100, total_loss_ratio: 0.55 },
          { total_objective: 100, total_loss_ratio: 0.55 },
          { total_objective: 120, total_loss_ratio: 0.59 },
        ]}
        xKey="total_loss_ratio"
        yKey="total_objective"
        xLabel="loss_ratio"
        selectedIdx={0}
        currentX={0.59}
        currentY={120}
        onPointClick={onPointClick}
      />,
    )

    const pointTwo = screen.getByRole("button", { name: /Select frontier point 2/ })

    expect(pointTwo).toHaveAccessibleName("Select frontier point 2 (2 overlapping frontier points)")
    expect(pointTwo).toHaveAttribute("r", "6")
    expect(screen.queryByRole("button", { name: "Select frontier point 1" })).not.toBeInTheDocument()

    fireEvent.click(pointTwo)
    expect(onPointClick).toHaveBeenCalledWith(1)
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
