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
    const line = container.querySelector("svg line[stroke-dasharray]")!
    expect(line).toBeInTheDocument()
    expect(line.getAttribute("stroke")).toBe(CHART_COLORS.best)
    expect(line.getAttribute("stroke-dasharray")).toBe("3,2")
  })

  it("fixes the axes from the first row: rounds to the budget, loss from 0 to a little over the start", () => {
    const data: LossEntry[] = [
      { iteration: 1, train_rmse: 1.0, eval_rmse: 0.9 },
      { iteration: 100, train_rmse: 0.5, eval_rmse: 0.6 },
    ]
    const { container } = render(<LossChart lossHistory={data} totalIterations={1000} />)
    const labels = [...container.querySelectorAll("svg text")].map((text) => text.textContent)
    expect(labels).toEqual(["1.1", "0", "1,000"])
    // A tenth of the way through the budget, the curve ends a tenth of the way across.
    const [train] = container.querySelectorAll("svg path")
    const xs = [...train.getAttribute("d")!.matchAll(/[ML]([\d.]+),/g)].map((m) => Number(m[1]))
    const [, baseline] = container.querySelectorAll("svg line")
    const left = Number(baseline.getAttribute("x1"))
    const width = Number(baseline.getAttribute("x2")) - left
    expect((xs[1] - left) / width).toBeCloseTo(0.1, 2)
  })

  it("draws the frame from a fit's first row", () => {
    const { container } = render(
      <LossChart lossHistory={[{ iteration: 1, train_rmse: 0.4 }]} totalIterations={500} />,
    )
    expect(container.querySelector("svg")).toBeInTheDocument()
    expect(container.textContent).toContain("500")
  })

  it("widens the loss axis when a later value outgrows the start", () => {
    const data: LossEntry[] = [
      { iteration: 1, train_rmse: 1.0, eval_rmse: 1.0 },
      { iteration: 2, train_rmse: 0.9, eval_rmse: 2.0 },
    ]
    const { container } = render(<LossChart lossHistory={data} totalIterations={10} />)
    expect(container.querySelector("svg text")!.textContent).toBe("2.2")
  })
})
