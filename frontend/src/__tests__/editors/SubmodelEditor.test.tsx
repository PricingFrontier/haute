/**
 * Render tests for SubmodelEditor.
 *
 * Tests: submodel badge, node count, file path, input/output ports,
 * empty port sections, double-click hint.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import SubmodelEditor from "../../panels/editors/SubmodelEditor"

afterEach(cleanup)

const DEFAULT_PROPS = {
  config: {} as Record<string, unknown>,
  accentColor: "#64748b",
}

describe("SubmodelEditor", () => {
  it("renders Wrapper badge text", () => {
    render(<SubmodelEditor {...DEFAULT_PROPS} />)
    expect(screen.getByText("Wrapper")).toBeTruthy()
  })

  it("shows node count from childNodeIds", () => {
    const config = { childNodeIds: ["node_1", "node_2", "node_3"] }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.getByText("3 nodes")).toBeTruthy()
  })

  it("renders file path when config.file is set", () => {
    const config = { file: "pipelines/sub_model.py" }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.getByText("File")).toBeTruthy()
    expect(screen.getByText("pipelines/sub_model.py")).toBeTruthy()
  })

  it("does NOT render file section when config.file is empty", () => {
    const config = { file: "" }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.queryByText("File")).toBeNull()
  })

  it("renders input ports as badges", () => {
    const config = { inputPorts: ["df_in", "rates"] }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.getByText("Inputs")).toBeTruthy()
    expect(screen.getByText("df_in")).toBeTruthy()
    expect(screen.getByText("rates")).toBeTruthy()
  })

  it("renders output ports as badges", () => {
    const config = { outputPorts: ["df_out", "summary"] }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.getByText("Outputs")).toBeTruthy()
    expect(screen.getByText("df_out")).toBeTruthy()
    expect(screen.getByText("summary")).toBeTruthy()
  })

  it("does NOT render inputs section when inputPorts is empty", () => {
    const config = { inputPorts: [] }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.queryByText("Inputs")).toBeNull()
  })

  it("does NOT render outputs section when outputPorts is empty", () => {
    const config = { outputPorts: [] }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    expect(screen.queryByText("Outputs")).toBeNull()
  })

  it("shows double-click hint", () => {
    render(<SubmodelEditor {...DEFAULT_PROPS} />)
    expect(
      screen.getByText("Double-click to view internal nodes"),
    ).toBeTruthy()
  })
})

describe("SubmodelEditor — collapsible per-frame I/O", () => {
  it("renders each input port as a collapsible frame row", () => {
    render(<SubmodelEditor config={{ inputPorts: ["df_in", "rates"] }} accentColor="#64748b" />)
    expect(screen.getByTestId("wrapper-frame-input-df_in")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-input-rates")).toBeTruthy()
  })

  it("renders each output port as a collapsible frame row", () => {
    render(<SubmodelEditor config={{ outputPorts: ["df_out", "summary"] }} accentColor="#64748b" />)
    expect(screen.getByTestId("wrapper-frame-output-df_out")).toBeTruthy()
    expect(screen.getByTestId("wrapper-frame-output-summary")).toBeTruthy()
  })

  // Render-gate (AGENTS.md §UI Test Assertions rule 3): every persisted port
  // must surface a row — a port that exists on disk but renders nowhere is
  // still live at execute time with no surface to inspect it.
  it("surfaces every persisted input/output port (none silently dropped)", () => {
    const config = {
      inputPorts: ["a_in", "b_in"],
      outputPorts: ["x_out", "y_out", "z_out"],
    }
    render(<SubmodelEditor config={config} accentColor="#64748b" />)
    for (const p of config.inputPorts) {
      expect(screen.getByTestId(`wrapper-frame-input-${p}`)).toBeTruthy()
    }
    for (const p of config.outputPorts) {
      expect(screen.getByTestId(`wrapper-frame-output-${p}`)).toBeTruthy()
    }
  })

  it("collapses frame detail by default and expands it on click", () => {
    render(<SubmodelEditor config={{ outputPorts: ["summary"] }} accentColor="#64748b" />)
    const row = screen.getByTestId("wrapper-frame-output-summary")
    expect(row.getAttribute("aria-expanded")).toBe("false")
    // Read-only detail (incl. the forward-looking note) is hidden while collapsed.
    expect(screen.queryByText(/arrive with the wrapper output model/)).toBeNull()

    fireEvent.click(row)
    expect(row.getAttribute("aria-expanded")).toBe("true")
    expect(screen.getByText(/arrive with the wrapper output model/)).toBeTruthy()
    expect(screen.getByText(/Output frame produced by node/)).toBeTruthy()
  })

  it("input frame detail names its binding node and the gated affordances", () => {
    render(<SubmodelEditor config={{ inputPorts: ["df_in"] }} accentColor="#64748b" />)
    fireEvent.click(screen.getByTestId("wrapper-frame-input-df_in"))
    expect(screen.getByText(/Input frame feeding node/)).toBeTruthy()
    expect(screen.getByText(/arrive with the wrapper output model/)).toBeTruthy()
  })
})
