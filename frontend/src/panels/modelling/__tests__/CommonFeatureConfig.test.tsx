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

  it("starts with every feature unticked and writes a ticked one to feature_columns", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <CommonFeatureConfig config={{ target: "target" }} onUpdate={onUpdate} columns={columns} />,
    )

    for (const name of ["weight", "date", "age", "region", "severity"]) {
      expect(
        within(featureRow(name)).getByRole("checkbox", { name: `Include ${name}` }),
      ).not.toBeChecked()
    }
    expect(screen.getByText("0 included · 5 excluded")).toBeInTheDocument()

    fireEvent.click(within(featureRow("age")).getByRole("checkbox", { name: "Include age" }))
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["age"] })
  })

  it("shares filtering, dtype labels, role omission, and stale-feature repair", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          weight: "weight",
          feature_columns: ["missing_feature"],
          evaluation: { strategy: "temporal", date_column: "date" },
        }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )

    expect(screen.queryByText("target")).toBeNull()
    expect(screen.queryByText("weight")).toBeNull()
    expect(screen.queryByText("date")).toBeNull()
    expect(screen.getByText("missing_feature - not found")).toBeInTheDocument()
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
        name: "Remove missing_feature feature",
      }),
    )
    expect(onUpdate).toHaveBeenCalledWith("feature_columns", [])
  })

  it("shows current inclusion states as checkboxes and toggles them in upstream order", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    const { rerender } = render(
      <CommonFeatureConfig
        onUpdate={onUpdate}
        columns={columns}
        config={{ target: "target", feature_columns: ["severity"] }}
      />,
    )

    const ageButton = within(featureRow("age")).getByRole("checkbox", { name: "Include age" })
    expect(ageButton).not.toBeChecked()
    expect(
      within(featureRow("severity")).getByRole("checkbox", { name: "Include severity" }),
    ).toBeChecked()

    fireEvent.click(ageButton)
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["age", "severity"] })

    rerender(
      <CommonFeatureConfig
        onUpdate={onUpdate}
        columns={columns}
        config={{ target: "target", feature_columns: ["age", "severity"] }}
      />,
    )
    const checkedAge = within(featureRow("age")).getByRole("checkbox", { name: "Include age" })
    expect(checkedAge).toBeChecked()
    fireEvent.click(checkedAge)
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["severity"] })
  })

  it("applies bulk inclusion to every feature regardless of the search", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    // A dormant role entry (weight) and a stale entry survive both bulk actions.
    const baseConfig = {
      target: "target",
      weight: "weight",
      feature_columns: ["weight", "missing_feature"],
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

    fireEvent.click(screen.getByRole("button", { name: "Include all features" }))
    const included = ["weight", "age", "region", "severity", "missing_feature"]
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: included })

    rerender(
      <CommonFeatureConfig
        config={{ ...baseConfig, feature_columns: included }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Exclude all features" }))
    expect(onUpdate).toHaveBeenLastCalledWith({
      feature_columns: ["weight", "missing_feature"],
    })
  })

  it("treats a ticked column that takes a role as dormant, not a feature", () => {
    render(
      <CommonFeatureConfig
        config={{ target: "age", feature_columns: ["age", "region"] }}
        onUpdate={vi.fn(() => ({ ok: true as const }))}
        columns={columns}
      />,
    )

    expect(screen.queryByRole("group", { name: "age feature" })).toBeNull()
    expect(screen.queryByText(/age - not found/)).toBeNull()
    expect(
      within(featureRow("region")).getByRole("checkbox", { name: "Include region" }),
    ).toBeChecked()
    expect(screen.getByText("1 included · 4 excluded")).toBeInTheDocument()
  })

  it("enables monotonicity only for included numeric features", () => {
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          feature_columns: ["age", "region"],
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
          feature_columns: ["age"],
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
      feature_columns: ["age"],
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
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: [] })

    rerender(
      <CommonFeatureConfig
        config={{ ...config, feature_columns: [] }}
        onUpdate={onUpdate}
        columns={columns}
      />,
    )
    const dormantUp = screen.getByRole("button", { name: "age: increasing" })
    expect(dormantUp).toBeDisabled()
    expect(dormantUp).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(within(featureRow("age")).getByRole("checkbox", { name: "Include age" }))
    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenLastCalledWith({ feature_columns: ["age"] })

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
  it("filters included and excluded features", () => {
    render(<CommonFeatureConfig config={{ target: "target", feature_columns: ["weight", "date", "age", "severity"] }} onUpdate={vi.fn()} columns={columns} />)
    fireEvent.click(screen.getByRole("button", { name: "Excluded (1)" }))
    expect(featureRow("region")).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "age feature" })).toBeNull()
  })

})

describe("CommonFeatureConfig before the columns load", () => {
  afterEach(cleanup)

  it("keeps saved features without flagging them as not found", () => {
    render(<CommonFeatureConfig config={{ target: "target", feature_columns: ["age", "region"] }} onUpdate={vi.fn(() => ({ ok: true as const }))} columns={[]} />)
    expect(screen.queryByText(/not found/)).toBeNull()
    expect(screen.getByText("The upstream columns are not known yet; 2 saved features will apply.")).toBeInTheDocument()
  })
})
