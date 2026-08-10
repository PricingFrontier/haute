import { useState } from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import ExploreChartsConfig from "../../panels/editors/ExploreChartsConfig"
import type { OnUpdateConfig } from "../../panels/editors/_shared"

afterEach(cleanup)

function ChartConfigHarness({ initialConfig = {} }: { initialConfig?: Record<string, unknown> }) {
  const [config, setConfig] = useState(initialConfig)
  const onUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
    setConfig((current) =>
      typeof keyOrUpdates === "string"
        ? { ...current, [keyOrUpdates]: value }
        : { ...current, ...keyOrUpdates },
    )
    return { ok: true }
  }

  return <ExploreChartsConfig config={config} onUpdate={onUpdate} />
}

describe("ExploreChartsConfig", () => {
  it("adds enabled cards, configures without toggling, returns, and toggles independently", () => {
    render(<ChartConfigHarness />)

    expect(screen.getByText(/No charts yet/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))
    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))

    expect(screen.getByRole("checkbox", { name: "Show Chart 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getByRole("checkbox", { name: "Show Chart 2" })).toHaveAttribute(
      "aria-checked",
      "true",
    )

    fireEvent.click(screen.getByRole("button", { name: "Configure Chart 1" }))

    expect(screen.getByRole("heading", { name: "Configure Chart 1" })).toBeInTheDocument()
    expect(screen.getByText(/Chart settings will be added here/i)).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Show Chart 1" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Back to charts" }))

    expect(screen.getByRole("checkbox", { name: "Show Chart 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    fireEvent.click(screen.getByRole("checkbox", { name: "Show Chart 2" }))
    expect(screen.getByRole("checkbox", { name: "Show Chart 1" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getByRole("checkbox", { name: "Show Chart 2" })).toHaveAttribute(
      "aria-checked",
      "false",
    )
  })

  it("uses the first unused stable id and preserves future chart settings", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <ExploreChartsConfig
        config={{
          charts: [
            { id: "chart_1", enabled: true, future_setting: { palette: "warm" } },
            { id: "chart_3", enabled: false },
          ],
        }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.click(screen.getByRole("checkbox", { name: "Show Chart 1" }))
    expect(onUpdate).toHaveBeenLastCalledWith("charts", [
      { id: "chart_1", enabled: false, future_setting: { palette: "warm" } },
      { id: "chart_3", enabled: false },
    ])

    fireEvent.click(screen.getByRole("button", { name: "Add Chart" }))
    expect(onUpdate).toHaveBeenLastCalledWith("charts", [
      { id: "chart_1", enabled: true, future_setting: { palette: "warm" } },
      { id: "chart_3", enabled: false },
      { id: "chart_2", enabled: true },
    ])
  })

  it("surfaces malformed persisted charts without offering destructive controls", () => {
    render(
      <ExploreChartsConfig
        config={{ charts: [{ id: "chart_1", enabled: true }, { id: "chart_1", enabled: false }] }}
        onUpdate={vi.fn()}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate chart id/i)
    expect(screen.queryByRole("button", { name: "Add Chart" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  })
})
