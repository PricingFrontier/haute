/**
 * Render tests for SubmodelEditor.
 *
 * Tests: submodel badge, node count, file path, input/output ports,
 * empty port sections, double-click hint.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
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
