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
        algorithm="catboost"
      />,
    )

    expect(screen.queryByText("target")).toBeNull()
    expect(screen.queryByText("weight")).toBeNull()
    expect(screen.queryByText("date")).toBeNull()
    expect(screen.getByText("missing_feature — not found")).toBeInTheDocument()
    expect(within(featureRow("region")).getByText("String")).toBeInTheDocument()

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

  it("shows current inclusion states as green/red card buttons and toggles them", () => {
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

    const ageButton = within(featureRow("age")).getByRole("button", {
      name: "age is included; click to exclude",
    })
    expect(ageButton).toHaveTextContent("Include")
    expect(ageButton.style.background).toBe("rgba(0, 179, 134, 0.1)")
    expect(ageButton.style.border).toContain("rgb(0, 179, 134)")
    expect(ageButton.style.color).toBe("rgb(0, 179, 134)")
    expect(ageButton).toHaveClass("px-2.5", "py-1")
    expect(ageButton).not.toHaveClass("w-full")

    const regionButton = within(featureRow("region")).getByRole("button", {
      name: "region is excluded; click to include",
    })
    expect(regionButton).toHaveTextContent("Exclude")
    expect(regionButton).toHaveStyle({
      background: "var(--danger-soft)",
      color: "var(--danger)",
    })
    expect(regionButton.getAttribute("style")).toContain(
      "border: 1px solid var(--danger)",
    )
    expect(regionButton).toHaveClass("px-2.5", "py-1")
    expect(regionButton).not.toHaveClass("w-full")

    fireEvent.click(ageButton)
    expect(onUpdate).toHaveBeenCalledWith({ exclude: ["region", "age"] })

    rerender(
      <CommonFeatureConfig
        {...baseProps}
        config={{ target: "target", exclude: ["region", "age"] }}
      />,
    )
    expect(
      within(featureRow("age")).getByRole("button", {
        name: "age is excluded; click to include",
      }),
    ).toHaveTextContent("Exclude")
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
        algorithm="catboost"
      />,
    )

    fireEvent.change(screen.getByLabelText("Search features"), {
      target: { value: "age" },
    })
    expect(screen.queryByText("region")).toBeNull()

    const includeAll = screen.getByRole("button", { name: "Include all features" })
    const excludeAll = screen.getByRole("button", { name: "Exclude all features" })
    expect(includeAll.style.background).toBe("rgba(0, 179, 134, 0.1)")
    expect(includeAll.style.color).toBe("rgb(0, 179, 134)")
    expect(excludeAll).toHaveStyle({
      background: "var(--danger-soft)",
      color: "var(--danger)",
    })
    expect(includeAll).toHaveClass("px-2.5", "py-1")
    expect(excludeAll).toHaveClass("px-2.5", "py-1")

    fireEvent.click(includeAll)
    expect(onUpdate).toHaveBeenLastCalledWith({ exclude: [] })

    rerender(
      <CommonFeatureConfig
        config={{ ...baseConfig, exclude: [] }}
        onUpdate={onUpdate}
        columns={columns}
        algorithm="catboost"
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Exclude all features" }))
    expect(onUpdate).toHaveBeenLastCalledWith({
      exclude: ["age", "region", "severity"],
    })
  })

  it("places arrow monotonicity controls on every card and enables final numeric features only", () => {
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          terms: {
            age: { type: "linear" },
            region: { type: "categorical" },
          },
        }}
        onUpdate={vi.fn(() => ({ ok: true as const }))}
        columns={columns}
        algorithm="glm"
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
    const down = within(featureRow("age")).getByRole("button", {
      name: "age: decreasing",
    })
    const neutral = within(featureRow("age")).getByRole("button", {
      name: "age: no constraint",
    })
    const up = within(featureRow("age")).getByRole("button", {
      name: "age: increasing",
    })
    const ageRow = featureRow("age")
    const ageInclusion = within(ageRow).getByRole("button", {
      name: "age is included; click to exclude",
    })
    expect(ageRow).toHaveClass("flex", "items-center", "px-2", "py-1.5")
    expect(ageInclusion.parentElement).toBe(ageRow)
    expect(down.closest("fieldset")?.parentElement).toBe(ageRow)
    expect(within(ageRow).getByText("Monotonicity")).toHaveClass("sr-only")
    expect(down).toHaveTextContent("↓")
    expect(down).toHaveStyle({ color: "var(--danger)" })
    expect(neutral).toHaveTextContent("−")
    expect(neutral).toHaveStyle({
      background: "var(--warning-soft)",
      color: "var(--warning-strong)",
    })
    expect(neutral.getAttribute("style")).toContain(
      "border: 1px solid var(--warning-strong)",
    )
    expect(up).toHaveTextContent("↑")
    expect(up.style.color).toBe("rgb(0, 179, 134)")
    expect(up).toHaveStyle({ background: "var(--bg-input)" })
    expect(down).toHaveClass("h-6", "w-6")
    expect(neutral).toHaveClass("h-6", "w-6")
    expect(up).toHaveClass("h-6", "w-6")
  })

  it("writes arrow monotonicity choices and removes the key for the dash", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          monotone_constraints: { age: -1 },
        }}
        onUpdate={onUpdate}
        columns={columns}
        algorithm="catboost"
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
        algorithm="glm"
      />,
    )

    expect(screen.getByRole("button", { name: "age: increasing" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
    fireEvent.click(within(featureRow("age")).getByRole("button", {
      name: "age is included; click to exclude",
    }))
    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenLastCalledWith({ exclude: ["age"] })

    rerender(
      <CommonFeatureConfig
        config={{ ...config, exclude: ["age"] }}
        onUpdate={onUpdate}
        columns={columns}
        algorithm="glm"
      />,
    )
    const dormantUp = screen.getByRole("button", { name: "age: increasing" })
    expect(dormantUp).toBeDisabled()
    expect(dormantUp).toHaveAttribute("aria-pressed", "true")
    expect(dormantUp.closest("fieldset")).toHaveClass(
      "disabled:opacity-40",
      "disabled:grayscale",
    )

    fireEvent.click(within(featureRow("age")).getByRole("button", {
      name: "age is excluded; click to include",
    }))
    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenLastCalledWith({ exclude: [] })

    rerender(
      <CommonFeatureConfig
        config={config}
        onUpdate={onUpdate}
        columns={columns}
        algorithm="glm"
      />,
    )
    expect(screen.getByRole("button", { name: "age: increasing" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "age: increasing" })).toHaveAttribute(
      "aria-pressed",
      "true",
    )
  })

  it("does not ask for confirmation when excluding a GLM column outside final selection", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    const confirmMock = vi.mocked(confirm)
    render(
      <CommonFeatureConfig
        config={{
          target: "target",
          terms: { age: { type: "linear" } },
        }}
        onUpdate={onUpdate}
        columns={columns}
        algorithm="glm"
      />,
    )

    fireEvent.click(
      within(featureRow("severity")).getByRole("button", {
        name: "severity is included; click to exclude",
      }),
    )

    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenCalledWith({ exclude: ["severity"] })
  })
})
