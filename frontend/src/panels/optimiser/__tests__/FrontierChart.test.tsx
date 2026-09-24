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

  it("plots on padded domains with ticks and labels over the data range", () => {
    const { container } = render(
      <FrontierChart
        points={[
          { total_objective: 10, total_loss_ratio: 0 },
          { total_objective: 20, total_loss_ratio: 100 },
        ]}
        xKey="total_loss_ratio"
        yKey="total_objective"
        xLabel="loss_ratio"
        selectedIdx={null}
        currentX={50}
        currentY={15}
        onPointClick={vi.fn()}
      />,
    )

    // x domain [-8, 108] and y domain [9.2, 20.8] over a 314 x 176 plot at (50, 16).
    const first = screen.getByRole("button", { name: "Select frontier point 1" })
    const second = screen.getByRole("button", { name: "Select frontier point 2" })
    expect(Number(first.getAttribute("cx"))).toBeCloseTo(50 + (8 / 116) * 314, 6)
    expect(Number(first.getAttribute("cy"))).toBeCloseTo(192 - (0.8 / 11.6) * 176, 6)
    expect(Number(second.getAttribute("cx"))).toBeCloseTo(50 + (108 / 116) * 314, 6)
    expect(Number(second.getAttribute("cy"))).toBeCloseTo(192 - (10.8 / 11.6) * 176, 6)

    const labels = Array.from(container.querySelectorAll("text")).map((text) => text.textContent)
    expect(labels).toEqual(expect.arrayContaining(["10", "12.5", "15", "17.5", "20", "0", "25", "50", "75", "100"]))
  })

  it("gives a single repeated point a finite scale and one tick per axis", () => {
    const { container } = render(
      <FrontierChart
        points={[{ total_objective: 5, total_loss_ratio: 0.5 }]}
        xKey="total_loss_ratio"
        yKey="total_objective"
        xLabel="loss_ratio"
        selectedIdx={null}
        currentX={0.5}
        currentY={5}
        onPointClick={vi.fn()}
      />,
    )

    const point = screen.getByRole("button", { name: "Select frontier point 1" })
    expect(Number(point.getAttribute("cx"))).toBeCloseTo(50 + 314 / 2, 6)
    expect(Number(point.getAttribute("cy"))).toBeCloseTo(16 + 176 / 2, 6)
    const tickLabels = Array.from(container.querySelectorAll("text"))
      .map((text) => text.textContent)
      .filter((text) => text === "5" || text === "0.5")
    expect(tickLabels).toEqual(["5", "0.5"])
  })
})
