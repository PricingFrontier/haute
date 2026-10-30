import { describe, expect, it, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent, within } from "@testing-library/react"
import { applyPreviewResultColumnsToNodes } from "../../hooks/usePipelineAPI"
import type { SchemaWarning } from "../../api/types"
import { makeNode } from "../../test-utils/factories"
import useGraphStore from "../../stores/useGraphStore"
import SubmodelEditor from "../../panels/editors/SubmodelEditor"
afterEach(() => { cleanup(); useGraphStore.setState({ submodels: {} }) })
const config = { definitionId: "definition_pricing", alias: "pricing" }
const definition = { definitionId: "definition_pricing", file: "pipelines/pricing.py", graph: { nodes: [{ id: "prepare" }, { id: "score" }], edges: [] }, inputPorts: [{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }], outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: null } }] }
const renderEditor = () => { useGraphStore.setState({ submodels: { definition_pricing: definition } }); return render(<SubmodelEditor nodeId="pricing" config={config} accentColor="#64748b" />) }
describe("SubmodelEditor", () => {
  it("lists occurrence warnings by input, including unavailable errors, and clears them on refresh", () => {
    const instanceConfig = { ...config, alias: "copy", instanceOf: "pricing" }
    const nodes = [makeNode("copy", "submodel", { data: { label: "copy", config: instanceConfig } })]
    const warnings: SchemaWarning[] = [
      { input: "policy", column: "region", status: "missing" },
      { input: "policy", column: "limit", status: "missing" },
      { input: "rates", column: null, status: "unavailable", detail: "Cannot resolve rates: missing rate_file.parquet" },
    ]
    useGraphStore.setState({ edges: [], nodes: applyPreviewResultColumnsToNodes(nodes, "submodel_runtime/copy/score", {
      status: "ok", node_schema_warnings: { copy: warnings },
    }, "live", 0), submodels: { definition_pricing: definition } })
    const view = render(<SubmodelEditor nodeId="copy" config={instanceConfig} accentColor="#64748b" />)
    const section = screen.getByRole("region", { name: "Missing columns" })
    expect(within(section).getByText("policy")).toBeVisible()
    expect(within(section).getByText("region")).toBeVisible()
    expect(within(section).getByText("limit")).toBeVisible()
    expect(within(section).getByText("rates")).toBeVisible()
    expect(within(section).getByText(warnings[2].detail!)).toBeVisible()
    view.rerender(<SubmodelEditor nodeId="copy" config={{ ...instanceConfig, instanceOf: undefined }} accentColor="#64748b" />)
    expect(screen.queryByRole("region", { name: "Missing columns" })).toBeNull()
    view.rerender(<SubmodelEditor nodeId="copy" config={instanceConfig} accentColor="#64748b" />)
    const refreshed = applyPreviewResultColumnsToNodes(useGraphStore.getState().nodes, "submodel_runtime/copy/score", {
      status: "ok", node_schema_warnings: { copy: [] },
    }, "live", 0)
    useGraphStore.setState({ nodes: refreshed })
    view.rerender(<SubmodelEditor nodeId="copy" config={instanceConfig} accentColor="#64748b" />)
    expect(screen.queryByRole("region", { name: "Missing columns" })).toBeNull()
  })
  it("shows every public input and retargets a live instance connection in one undoable edit", () => {
    const instanceConfig = { ...config, alias: "copy", instanceOf: "pricing" }
    const nodes = [makeNode("copy", "submodel", { data: { label: "copy", config: instanceConfig } }),
      makeNode("a", "polars", { data: { _defaultInputName: "quotes" } }),
      makeNode("b", "polars", { data: { _defaultInputName: "lookup" } })]
    const edges = [
      { id: "a", source: "a", target: "copy", targetHandle: "in__policy", data: { _inputName: "quotes" } },
      { id: "b", source: "b", target: "copy", targetHandle: "in__rates", data: { _inputName: "lookup" } },
    ]
    useGraphStore.setState({ nodes, edges, submodels: { definition_pricing: { ...definition, inputPorts: [...definition.inputPorts, { name: "rates", targets: [] }, { name: "claims", targets: [] }] } }, undoStack: [], redoStack: [] })
    render(<SubmodelEditor nodeId="copy" config={instanceConfig} accentColor="#64748b" />)
    expect(screen.getByRole("combobox", { name: "claims" })).toHaveValue("")
    fireEvent.change(screen.getByRole("combobox", { name: "policy" }), { target: { value: "b" } })
    expect(useGraphStore.getState().edges).toEqual([{ ...edges[1], targetHandle: "in__policy" }])
    expect(useGraphStore.getState().undoStack).toHaveLength(1)
    useGraphStore.getState().undo()
    expect(useGraphStore.getState().edges).toEqual(edges)
    useGraphStore.getState().redo()
    expect(useGraphStore.getState().edges).toEqual([{ ...edges[1], targetHandle: "in__policy" }])
  })
  it("renders definition-owned metadata and public port names", () => { renderEditor(); expect(screen.getByText("Submodel")).toBeTruthy(); expect(screen.getByText("2 nodes")).toBeTruthy(); expect(screen.getByText("pipelines/pricing.py")).toBeTruthy(); expect(screen.getByText("policy")).toBeTruthy(); expect(screen.getByText("premium")).toBeTruthy() })
  it("renders an error for an unavailable definition", () => { render(<SubmodelEditor nodeId="pricing" config={config} accentColor="#64748b" />); expect(screen.getByRole("alert")).toHaveTextContent("invalid") })
})
