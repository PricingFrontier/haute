import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { LiftTab } from "../LiftTab"
import { ResidualsTab } from "../ResidualsTab"
import { makeTrainResult } from "../../../test-utils/factories"

afterEach(cleanup)

const baseResult = makeTrainResult({
  feature_importance: [],
  model_path: "",
  development_rows: 10,
  final_test_rows: 5,
})

describe("LiftTab", () => {
  it("renders a labelled Lorenz curve when lift values are absent", () => {
    render(
      <LiftTab
        width={420}
        result={{
          ...baseResult,
          double_lift: [],
          lorenz_curve: [
            { cum_weight_frac: 0, cum_actual_frac: 0 },
            { cum_weight_frac: 1, cum_actual_frac: 1 },
          ],
        }}
      />,
    )
    expect(screen.getByRole("img", { name: "Lorenz curve" })).toBeInTheDocument()
    expect(screen.getByText("Lorenz curve")).toBeInTheDocument()
  })

  it("uses a switch when narrow and shows both views when wide", () => {
    const result = {
      ...baseResult,
      double_lift: [{ decile: 1, actual: 0.1, predicted: 0.12, count: 12 }],
      lorenz_curve: [
        { cum_weight_frac: 0, cum_actual_frac: 0 },
        { cum_weight_frac: 1, cum_actual_frac: 1 },
      ],
    }
    const { rerender } = render(<LiftTab width={600} result={result} />)
    expect(screen.getByRole("button", { name: "Double lift" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    fireEvent.click(screen.getByRole("button", { name: "Lorenz curve" }))
    expect(screen.getByRole("button", { name: "Lorenz curve" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    rerender(<LiftTab width={920} result={result} />)
    expect(screen.getAllByRole("img")).toHaveLength(2)
    rerender(<LiftTab width={600} result={result} />)
    expect(screen.getByRole("button", { name: "Lorenz curve" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("keeps raw lift values behind a native disclosure", () => {
    render(
      <LiftTab
        width={420}
        result={{
          ...baseResult,
          double_lift: [{ decile: 1, actual: 0.1, predicted: 0.12, count: 12 }],
        }}
      />,
    )
    const disclosure = screen.getByText("View lift values").closest("details")!
    expect(disclosure).not.toHaveAttribute("open")
    expect(screen.getByRole("table")).toBeInTheDocument()
  })

  it("falls back to lift when a remembered Lorenz view loses its Lorenz data", () => {
    const both = {
      ...baseResult,
      double_lift: [{ decile: 1, actual: 0.1, predicted: 0.12, count: 12 }],
      lorenz_curve: [
        { cum_weight_frac: 0, cum_actual_frac: 0 },
        { cum_weight_frac: 1, cum_actual_frac: 1 },
      ],
    }
    const { rerender } = render(<LiftTab width={600} result={both} />)
    fireEvent.click(screen.getByRole("button", { name: "Lorenz curve" }))
    rerender(<LiftTab width={600} result={{ ...both, lorenz_curve: [] }} />)
    expect(screen.getByRole("img", { name: "Double lift chart" })).toBeInTheDocument()
  })
})

describe("ResidualsTab", () => {
  it("puts the histogram and scatter side by side from 760 px and stacks them below", () => {
    const result = {
      ...baseResult,
      residuals_histogram: [{ bin_center: 0, count: 4, weighted_count: 4 }],
      actual_vs_predicted: [{ actual: 1, predicted: 1.1, weight: 1 }],
    }
    const widths = () =>
      ["Residuals distribution histogram", "Actual versus predicted scatter plot"].map((name) =>
        screen.getByRole("img", { name }).getAttribute("width"),
      )
    const inTwoColumns = () =>
      screen.getByRole("img", { name: "Residuals distribution histogram" }).closest(".grid-cols-2") !== null
    const { rerender } = render(<ResidualsTab width={800} result={result} />)
    expect(widths()).toEqual(["388", "388"])
    expect(inTwoColumns()).toBe(true)
    rerender(<ResidualsTab width={759} result={result} />)
    expect(widths()).toEqual(["759", "759"])
    expect(inTwoColumns()).toBe(false)
  })

  it("renders numeric histogram ticks and keeps bars within the plot", () => {
    render(
      <ResidualsTab
        width={360}
        result={{
          ...baseResult,
          residuals_histogram: [
            { bin_center: -1, count: 2, weighted_count: 2 },
            { bin_center: 0, count: 4, weighted_count: 4 },
            { bin_center: 1, count: 3, weighted_count: 3 },
          ],
        }}
      />,
    )
    const svg = screen.getByRole("img", { name: "Residuals distribution histogram" })
    expect(svg.textContent).toContain("-1.5")
    Array.from(screen.getAllByTestId("residual-histogram-bar")).forEach((bar) => {
      const x = Number(bar.getAttribute("x")),
        width = Number(bar.getAttribute("width"))
      expect(x).toBeGreaterThanOrEqual(50)
      expect(x + width).toBeLessThanOrEqual(348)
    })
  })

  it("keeps all-identical bin centers finite", () => {
    render(
      <ResidualsTab
        width={360}
        result={{
          ...baseResult,
          residuals_histogram: [
            { bin_center: 2, count: 2, weighted_count: 2 },
            { bin_center: 2, count: 3, weighted_count: 3 },
          ],
        }}
      />,
    )
    const geometry = Array.from(
      screen
        .getByRole("img", { name: "Residuals distribution histogram" })
        .querySelectorAll("rect"),
    )
      .map((element) =>
        [
          element.getAttribute("x"),
          element.getAttribute("width"),
          element.getAttribute("height"),
        ].join(" "),
      )
      .join(" ")
    expect(geometry).not.toMatch(/NaN|Infinity/)
  })

  it("uses a finite nonzero y-domain when every weighted count is zero", () => {
    render(
      <ResidualsTab
        width={360}
        result={{
          ...baseResult,
          residuals_histogram: [
            { bin_center: -1, count: 0, weighted_count: 0 },
            { bin_center: 1, count: 0, weighted_count: 0 },
          ],
        }}
      />,
    )
    const svg = screen.getByRole("img", { name: "Residuals distribution histogram" })
    const text = Array.from(svg.querySelectorAll("text")).map((element) => element.textContent)
    expect(new Set(text).size).toBeGreaterThan(4)
    expect(text).toEqual(expect.arrayContaining(["0.00", "0.25", "0.50", "0.75", "1.00"]))
    const geometry = Array.from(svg.querySelectorAll("rect"))
      .map((element) =>
        [
          element.getAttribute("x"),
          element.getAttribute("width"),
          element.getAttribute("height"),
        ].join(" "),
      )
      .join(" ")
    expect(geometry).not.toMatch(/NaN|Infinity/)
  })

  it("retains the scatter sampling disclosure", () => {
    const data = Array.from({ length: 2001 }, (_, i) => ({ actual: i, predicted: i, weight: 1 }))
    render(<ResidualsTab width={400} result={{ ...baseResult, actual_vs_predicted: data }} />)
    expect(screen.getByText("Showing 2,000 of 2,001 points (sampled)")).toBeInTheDocument()
  })
})
