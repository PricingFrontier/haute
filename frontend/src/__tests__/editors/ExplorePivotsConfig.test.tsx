import { useState } from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import ExplorePivotsConfig from "../../panels/editors/ExplorePivotsConfig"
import type { OnUpdateConfig } from "../../panels/editors/_shared"

afterEach(cleanup)

function PivotConfigHarness({ initialConfig = {} }: { initialConfig?: Record<string, unknown> }) {
  const [config, setConfig] = useState(initialConfig)
  const onUpdate: OnUpdateConfig = (keyOrUpdates, value) => {
    setConfig((current) =>
      typeof keyOrUpdates === "string"
        ? { ...current, [keyOrUpdates]: value }
        : { ...current, ...keyOrUpdates },
    )
    return { ok: true }
  }

  return <ExplorePivotsConfig config={config} onUpdate={onUpdate} />
}

describe("ExplorePivotsConfig", () => {
  it("adds neutral cards, configures one without mutation, and returns to the list", () => {
    render(<PivotConfigHarness />)

    expect(screen.getByText(/No pivots yet/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Add Pivot" }))
    fireEvent.click(screen.getByRole("button", { name: "Add Pivot" }))

    expect(screen.getByRole("group", { name: "Pivot 1" })).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Pivot 2" })).toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Configure Pivot 1" }))

    expect(screen.getByRole("heading", { name: "Configure Pivot 1" })).toBeInTheDocument()
    expect(screen.getByText(/Pivot settings will be added here/i)).toBeInTheDocument()
    expect(screen.queryByRole("group", { name: "Pivot 2" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Back to pivots" }))

    expect(screen.getByRole("group", { name: "Pivot 1" })).toBeInTheDocument()
    expect(screen.getByRole("group", { name: "Pivot 2" })).toBeInTheDocument()
  })

  it("uses the first unused stable id and preserves future pivot settings", () => {
    const onUpdate = vi.fn(() => ({ ok: true as const }))
    render(
      <ExplorePivotsConfig
        config={{
          pivots: [
            { id: "pivot_1", future_setting: { rows: ["region"] } },
            { id: "pivot_3" },
          ],
        }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Add Pivot" }))

    expect(onUpdate).toHaveBeenCalledWith("pivots", [
      { id: "pivot_1", future_setting: { rows: ["region"] } },
      { id: "pivot_3" },
      { id: "pivot_2" },
    ])
  })

  it("surfaces malformed persisted pivots without offering destructive controls", () => {
    render(
      <ExplorePivotsConfig
        config={{ pivots: [{ id: "pivot_1" }, { id: "pivot_1" }] }}
        onUpdate={vi.fn()}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(/duplicate pivot id/i)
    expect(screen.queryByRole("button", { name: "Add Pivot" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Configure Pivot/i })).not.toBeInTheDocument()
  })
})
