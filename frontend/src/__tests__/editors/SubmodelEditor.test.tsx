import { describe, expect, it, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import useGraphStore from "../../stores/useGraphStore"
import SubmodelEditor from "../../panels/editors/SubmodelEditor"
afterEach(() => { cleanup(); useGraphStore.setState({ submodels: {} }) })
const config = { definitionId: "definition_pricing", alias: "pricing" }
const definition = { definitionId: "definition_pricing", file: "pipelines/pricing.py", graph: { nodes: [{ id: "prepare" }, { id: "score" }], edges: [] }, inputPorts: [{ name: "policy", targets: [{ nodeId: "prepare", handleId: null }] }], outputPorts: [{ name: "premium", source: { nodeId: "score", handleId: null } }] }
const renderEditor = () => { useGraphStore.setState({ submodels: { definition_pricing: definition } }); return render(<SubmodelEditor config={config} accentColor="#64748b" />) }
describe("SubmodelEditor", () => {
  it("renders definition-owned metadata and public port names", () => { renderEditor(); expect(screen.getByText("Submodel")).toBeTruthy(); expect(screen.getByText("2 nodes")).toBeTruthy(); expect(screen.getByText("pipelines/pricing.py")).toBeTruthy(); expect(screen.getByText("policy")).toBeTruthy(); expect(screen.getByText("premium")).toBeTruthy() })
  it("renders an error for an unavailable definition", () => { render(<SubmodelEditor config={config} accentColor="#64748b" />); expect(screen.getByRole("alert")).toHaveTextContent("invalid") })
})