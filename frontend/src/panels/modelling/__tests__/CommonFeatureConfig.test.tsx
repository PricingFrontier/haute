import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { CommonFeatureConfig } from "../CommonFeatureConfig"

const columns = [
  { name: "target", dtype: "Float64" },
  { name: "weight", dtype: "Float64" },
  { name: "date", dtype: "Date" },
  { name: "age", dtype: "Int64" },
  { name: "region", dtype: "String" },
  { name: "severity", dtype: "Float64" },
]

function featureRow(name: string): HTMLElement {
  return screen.getByRole("group", { name: `${name} feature` })
}

describe("CommonFeatureConfig", () => {
  beforeEach(() => {
    vi.stubGlobal("confirm", vi.fn(() => true))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it("shares filtering, dtype labels, role omission, and stale-exclusion repair", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          weight: "weight",
          exclude: ["missing_feature"],
          evaluation: { strategy: "temporal", date_column: "date" },
        }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )

    expect(screen.queryByText("target")).toBeNull()
    expect(screen.queryByText("weight")).toBeNull()
    expect(screen.queryByText("date")).toBeNull()
    expect(screen.getByText("missing_feature — not found")).toBeInTheDocument()
    expect(within(featureRow("region")).getByText("String")).toHaveClass("text-amber-400")
    expect(within(featureRow("age")).getByText("Int64")).toHaveClass("text-blue-400")
    expect(within(featureRow("severity")).getByText("Float64")).toHaveClass("text-emerald-400")

    fireEvent.change(screen.getByLabelText("Search features"), {
      target: { value: "REG" },
    })
    expect(screen.getByText("region")).toBeInTheDocument()
    expect(screen.queryByText("severity")).toBeNull()

    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove missing_feature exclusion",
      }),
    )
    expect(onUpdate).toHaveBeenCalledWith("exclude", [])
  })

  it("shows current inclusion states as checkboxes and toggles them", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    const baseProps = {
      onUpdate,
      columns,
      algorithm: "catboost" as const,
    }
    const { rerender } = render(
      <CommonFeatureConfig
        {...baseProps}
        config={{ target: "target", exclude: ["region"] }}
      />,
    )

    const ageButton = within(featureRow("age")).getByRole("checkbox", { name: "Include age" })
    expect(ageButton).toBeChecked()

    const regionButton = within(featureRow("region")).getByRole("checkbox", { name: "Include region" })
    expect(regionButton).not.toBeChecked()

    fireEvent.click(ageButton)
    expect(onUpdate).toHaveBeenCalledWith({ exclude: ["region", "age"] })

    rerender(
      <CommonFeatureConfig
        {...baseProps}
        config={{ target: "target", exclude: ["region", "age"] }}
      />,
    )
    expect(
      within(featureRow("age")).getByRole("checkbox", { name: "Include age" }),
    ).not.toBeChecked()
  })

  it("applies bulk inclusion to every feature regardless of the search", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    const baseConfig = {
      target: "target",
      weight: "weight",
      exclude: ["age", "region", "severity"],
      evaluation: { strategy: "temporal", date_column: "date" },
    }
    const { rerender } = render(
      <CommonFeatureConfig
        config={baseConfig}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )

    fireEvent.change(screen.getByLabelText("Search features"), {
      target: { value: "age" },
    })
    expect(screen.queryByText("region")).toBeNull()

    const includeAll = screen.getByRole("button", { name: "Include all features" })
    const excludeAll = screen.getByRole("button", { name: "Exclude all features" })
    fireEvent.click(includeAll)
    expect(onUpdate).toHaveBeenLastCalledWith({ exclude: [] })

    rerender(
      <CommonFeatureConfig
        config={{ ...baseConfig, exclude: [] }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )
    fireEvent.click(excludeAll)
    expect(onUpdate).toHaveBeenLastCalledWith({
      exclude: ["age", "region", "severity"],
    })
  })

  it("enables monotonicity only for included numeric features", () => {
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          exclude: ["severity"],
        }}
        onUpdate={vi.fn(() => ({ ok: true as const }))}
        columns={columns}
      />,
    )

    expect(
      screen.getByRole("button", { name: "age: increasing" }),
    ).toBeEnabled()
    expect(
      screen.getByRole("button", { name: "severity: increasing" }),
    ).toBeDisabled()
    expect(
      screen.getByRole("button", { name: "region: increasing" }),
    ).toBeDisabled()
    expect(screen.getByRole("button", { name: "age: no constraint" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "age: decreasing" })).toHaveTextContent("↓")
    expect(screen.getByRole("button", { name: "age: no constraint" })).toHaveTextContent("−")
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveTextContent("↑")

  })

  it("writes monotonicity choices and removes the key for no constraint", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          monotone_constraints: { age: -1 },
        }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "age: increasing" }))
    expect(onUpdate).toHaveBeenLastCalledWith("monotone_constraints", {
      age: 1,
    })

    fireEvent.click(screen.getByRole("button", { name: "age: no constraint" }))
    expect(onUpdate).toHaveBeenLastCalledWith("monotone_constraints", null)
  })

  it("keeps feature settings dormant across confirmation-free exclusion and re-inclusion", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    const confirmMock = vi.mocked(confirm)
    const config = {
      target: "target",
      terms: {
        age: { type: "linear" },
        region: { type: "categorical" },
      },
      monotone_constraints: { age: 1, severity: -1 },
      interactions: [
        { factors: ["age", "region"], include_main: true },
        { factors: ["region", "severity"], include_main: false },
      ],
    }
    const { rerender } = render(
      <CommonFeatureConfig
        config={config}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )

    expect(screen.getByRole("button", { name: "age: increasing" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(within(featureRow("age")).getByRole("checkbox", { name: "Include age" }))
    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenLastCalledWith({ exclude: ["age"] })

    rerender(
      <CommonFeatureConfig
        config={{ ...config, exclude: ["age"] }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )
    const dormantUp = screen.getByRole("button", { name: "age: increasing" })
    expect(dormantUp).toBeDisabled()
    expect(dormantUp).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(within(featureRow("age")).getByRole("checkbox", { name: "Include age" }))
    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenLastCalledWith({ exclude: [] })

    rerender(
      <CommonFeatureConfig
        config={config}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )
    expect(screen.getByRole("button", { name: "age: increasing" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute("aria-pressed", "true")
  })
  it("filters included and excluded features without hiding role explanations", () => {
    render(<CommonFeatureConfig config={{ target: "target", exclude: ["region"] }} onUpdate={vi.fn()} columns={columns} />)
    fireEvent.click(screen.getByRole("button", { name: "Excluded (1)" }))
    expect(featureRow("region")).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "age feature" })).toBeNull()
    expect(screen.getByText(/Excluded from predictors: target/)).toBeInTheDocument()
  })

})
