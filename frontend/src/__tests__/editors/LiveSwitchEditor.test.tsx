/**
 * Render tests for LiveSwitchEditor.
 *
 * Tests: renders source info, input mapping, active indicator.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import LiveSwitchEditor from "../../panels/editors/LiveSwitchEditor"
import useSettingsStore from "../../stores/useSettingsStore"

afterEach(cleanup)

// Reset store before each test
beforeEach(() => {
  useSettingsStore.setState({
    sources: ["live", "backtest"],
    activeSource: "live",
  })
})

describe("LiveSwitchEditor", () => {
  it("renders source routing description", () => {
    render(
      <LiveSwitchEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#34d399" />,
    )
    expect(screen.getByText("Routes inputs based on the active source")).toBeTruthy()
  })

  it("shows active source", () => {
    render(
      <LiveSwitchEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#34d399" />,
    )
    expect(screen.getByText("● live")).toBeTruthy()
  })

  it("renders input mapping section with count", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
      { sourceNodeId: "test-source", name: "backtest_data", sourceLabel: "Backtest Data", edgeId: "e2" },
    ]
    render(
      <LiveSwitchEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} accentColor="#34d399" />,
    )
    expect(screen.getByText("Input → Source Mapping (2)")).toBeTruthy()
    expect(screen.getByText("live_data")).toBeTruthy()
    expect(screen.getByText("backtest_data")).toBeTruthy()
  })

  it("renders source dropdowns for each input", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
    ]
    render(
      <LiveSwitchEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} accentColor="#34d399" />,
    )
    const selects = screen.getAllByRole("combobox")
    expect(selects.length).toBeGreaterThanOrEqual(1)
    // Should have source options
    const options = Array.from((selects[0] as HTMLSelectElement).options).map(o => o.value)
    expect(options).toContain("live")
    expect(options).toContain("backtest")
  })

  it("shows active indicator when input is mapped to active source", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
    ]
    const config = {
      input_scenario_map: { live_data: "live" },
    }
    render(
      <LiveSwitchEditor config={config} onUpdate={vi.fn()} inputSources={inputs} accentColor="#34d399" />,
    )
    expect(screen.getByText("active")).toBeTruthy()
  })

  it("does not show active indicator when mapped to non-active source", () => {
    const inputs = [
      { sourceNodeId: "test-source", name: "backtest_data", sourceLabel: "Backtest Data", edgeId: "e2" },
    ]
    const config = {
      input_scenario_map: { backtest_data: "backtest" },
    }
    render(
      <LiveSwitchEditor config={config} onUpdate={vi.fn()} inputSources={inputs} accentColor="#34d399" />,
    )
    expect(screen.queryByText("active")).toBeNull()
  })

  it("calls onUpdate when selecting a source for an input", () => {
    const onUpdate = vi.fn()
    const inputs = [
      { sourceNodeId: "test-source", name: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
    ]
    render(
      <LiveSwitchEditor config={{}} onUpdate={onUpdate} inputSources={inputs} accentColor="#34d399" />,
    )
    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement
    fireEvent.change(select, { target: { value: "backtest" } })
    expect(onUpdate).toHaveBeenCalledWith("input_scenario_map", { live_data: "backtest" })
  })

  it("renders with non-live active source", () => {
    useSettingsStore.setState({ activeSource: "backtest" })
    render(
      <LiveSwitchEditor config={{}} onUpdate={vi.fn()} inputSources={[]} accentColor="#34d399" />,
    )
    expect(screen.getByText("backtest")).toBeTruthy()
  })

  it("routes two frames from one apiInput independently by their input names", () => {
    useSettingsStore.setState({ sources: ["live", "batch"], activeSource: "live" })
    const onUpdate = vi.fn()
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined)
    try {
      render(
        <LiveSwitchEditor
          config={{ input_scenario_map: { quotes: "live", drivers: "batch" } }}
          onUpdate={onUpdate}
          inputSources={[
            {
              sourceNodeId: "api",
              name: "quotes",
              sourceLabel: "Quote API",
              edgeId: "edge_quotes",
            },
            {
              sourceNodeId: "api",
              name: "drivers",
              sourceLabel: "Quote API",
              edgeId: "edge_drivers",
            },
          ]}
          accentColor="#34d399"
        />,
      )

      expect(screen.getByText("quotes")).toBeInTheDocument()
      expect(screen.getByText("drivers")).toBeInTheDocument()
      expect(screen.queryByText("Quote API")).not.toBeInTheDocument()
      expect(
        screen.queryByLabelText(/unresolved.*frame|frame.*unresolved/i),
      ).not.toBeInTheDocument()
      const selects = screen.getAllByRole("combobox") as HTMLSelectElement[]
      expect(selects.map((select) => select.value)).toEqual(["live", "batch"])

      fireEvent.change(selects[1], { target: { value: "live" } })
      expect(onUpdate).toHaveBeenCalledWith("input_scenario_map", {
        quotes: "live",
        drivers: "live",
      })
      expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(
        /same key|unique ["']key["']/i,
      )
    } finally {
      consoleError.mockRestore()
    }
  })

  it("shows unresolved frame state while continuing to map by name", () => {
    const onUpdate = vi.fn()
    render(
      <LiveSwitchEditor
        config={{ input_scenario_map: { stale_quotes: "backtest" } }}
        onUpdate={onUpdate}
        inputSources={[
          {
            sourceNodeId: "api",
            name: "stale_quotes",
            sourceLabel: "Quote API",
            edgeId: "edge_api",
            frameUnresolved: true,
          },
        ]}
        accentColor="#34d399"
      />,
    )

    expect(screen.getByText("stale_quotes")).toBeInTheDocument()
    const warning = screen.getByLabelText(/unresolved.*frame|frame.*unresolved/i)
    expect(warning).toBeVisible()
    expect(warning).toHaveAttribute(
      "title",
      expect.stringMatching(/eligible|emitted|resolv/i),
    )

    const select = screen.getByRole("combobox") as HTMLSelectElement
    expect(select.value).toBe("backtest")
    fireEvent.change(select, { target: { value: "live" } })
    expect(onUpdate).toHaveBeenCalledWith("input_scenario_map", { stale_quotes: "live" })
  })

  it("keeps same-parent rows distinct when their input names differ", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined)
    try {
      render(
        <LiveSwitchEditor
          config={{}}
          onUpdate={vi.fn()}
          inputSources={[
            {
              sourceNodeId: "api",
              name: "shared_a",
              sourceLabel: "Shared Source",
              edgeId: "edge_a",
            },
            {
              sourceNodeId: "api",
              name: "shared_b",
              sourceLabel: "Shared Source",
              edgeId: "edge_b",
            },
          ]}
          accentColor="#34d399"
        />,
      )

      expect(screen.getByText("shared_a")).toBeInTheDocument()
      expect(screen.getByText("shared_b")).toBeInTheDocument()
      expect(screen.getAllByRole("combobox")).toHaveLength(2)
      expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(
        /same key|unique ["']key["']/i,
      )
    } finally {
      consoleError.mockRestore()
    }
  })
})
