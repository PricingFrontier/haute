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
      { sourceNodeId: "test-source", varName: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
      { sourceNodeId: "test-source", varName: "backtest_data", sourceLabel: "Backtest Data", edgeId: "e2" },
    ]
    render(
      <LiveSwitchEditor config={{}} onUpdate={vi.fn()} inputSources={inputs} accentColor="#34d399" />,
    )
    expect(screen.getByText("Input → Source Mapping (2)")).toBeTruthy()
    expect(screen.getByText("Live Data")).toBeTruthy()
    expect(screen.getByText("Backtest Data")).toBeTruthy()
  })

  it("renders source dropdowns for each input", () => {
    const inputs = [
      { sourceNodeId: "test-source", varName: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
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
      { sourceNodeId: "test-source", varName: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
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
      { sourceNodeId: "test-source", varName: "backtest_data", sourceLabel: "Backtest Data", edgeId: "e2" },
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
      { sourceNodeId: "test-source", varName: "live_data", sourceLabel: "Live Data", edgeId: "e1" },
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
})
