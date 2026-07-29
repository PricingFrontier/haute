import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useSettingsStore from "../../../stores/useSettingsStore"
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
  const label = screen
    .getAllByText(name)
    .find((element) => element.tagName === "SPAN")
  if (!label?.parentElement) throw new Error(`Missing feature row for ${name}`)
  return label.parentElement
}

describe("CommonFeatureConfig", () => {
  beforeEach(() => {
    useSettingsStore.setState({ openSections: {} })
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

    fireEvent.change(screen.getByLabelText("Filter features"), {
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

  it("offers monotonicity only for final selected numeric features", () => {
    useSettingsStore.setState({
      openSections: { "modelling.monotonic": true },
    })
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
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "severity: increasing" }),
    ).toBeNull()
    expect(
      screen.queryByRole("button", { name: "region: increasing" }),
    ).toBeNull()
  })

  it("cancels or applies a selected-feature removal as one exact update", () => {
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
    render(
      <CommonFeatureConfig
        config={config}
        onUpdate={onUpdate}
        columns={columns}
        algorithm="glm"
      />,
    )

    confirmMock.mockReturnValueOnce(false)
    fireEvent.click(within(featureRow("age")).getByRole("button", { name: "Exclude" }))
    expect(onUpdate).not.toHaveBeenCalled()

    confirmMock.mockReturnValueOnce(true)
    fireEvent.click(within(featureRow("age")).getByRole("button", { name: "Exclude" }))
    expect(onUpdate).toHaveBeenCalledWith({
      exclude: ["age"],
      monotone_constraints: { severity: -1 },
      terms: { region: { type: "categorical" } },
      interactions: [
        { factors: ["region", "severity"], include_main: false },
      ],
    })
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
      within(featureRow("severity")).getByRole("button", { name: "Exclude" }),
    )

    expect(confirmMock).not.toHaveBeenCalled()
    expect(onUpdate).toHaveBeenCalledWith({ exclude: ["severity"] })
  })
})
