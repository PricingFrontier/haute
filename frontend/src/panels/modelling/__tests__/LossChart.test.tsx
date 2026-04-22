import { describe, it, expect, afterEach } from "vitest"
import { render, cleanup } from "@testing-library/react"
import { LossChart } from "../LossChart"
import type { LossEntry } from "../LossChart"
import { CHART_COLORS } from "../../../theme/colors"

describe("LossChart", () => {
  afterEach(cleanup)
  it("returns null with empty data", () => {
    const { container } = render(<LossChart lossHistory={[]} />)
    expect(container.innerHTML).toBe("")
  })

  it("returns null with < 2 entries", () => {
    const { container } = render(
      <LossChart lossHistory={[{ iteration: 0, train_rmse: 1.0 }]} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("renders SVG with train curve path", () => {
    const data: LossEntry[] = [
      { iteration: 0, train_rmse: 1.0 },
      { iteration: 1, train_rmse: 0.8 },
      { iteration: 2, train_rmse: 0.6 },
    ]
    const { container } = render(<LossChart lossHistory={data} />)
    const svg = container.querySelector("svg")!
    expect(svg).toBeInTheDocument()
    const paths = svg.querySelectorAll("path")
    expect(paths.length).toBeGreaterThanOrEqual(1)
    // Train path should have a 'd' attribute with path commands
    expect(paths[0].getAttribute("d")).toMatch(/^M/)
  })

  it("renders eval curve if present", () => {
    const data: LossEntry[] = [
      { iteration: 0, train_rmse: 1.0, eval_rmse: 1.1 },
      { iteration: 1, train_rmse: 0.8, eval_rmse: 0.9 },
      { iteration: 2, train_rmse: 0.6, eval_rmse: 0.7 },
    ]
    const { container } = render(<LossChart lossHistory={data} />)
    const paths = container.querySelectorAll("svg path")
    expect(paths.length).toBe(2)
    expect(paths[1].getAttribute("stroke")).toBe(CHART_COLORS.eval)
  })

  it("renders best iteration line", () => {
    const data: LossEntry[] = [
      { iteration: 0, train_rmse: 1.0 },
      { iteration: 1, train_rmse: 0.8 },
      { iteration: 2, train_rmse: 0.6 },
    ]
    const { container } = render(<LossChart lossHistory={data} bestIteration={1} />)
    const line = container.querySelector("svg line")!
    expect(line).toBeInTheDocument()
    expect(line.getAttribute("stroke")).toBe(CHART_COLORS.best)
    expect(line.getAttribute("stroke-dasharray")).toBe("3,2")
  })
})
