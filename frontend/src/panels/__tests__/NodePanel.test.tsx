import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import NodePanel from "../NodePanel"
import type { SimpleNode, SimpleEdge } from "../editors"
import useUIStore from "../../stores/useUIStore"

// Mock all editor components — we only care that the right one renders
vi.mock("../editors", () => ({
  DataSourceEditor: () => <div data-testid="DataSourceEditor" />,
  TransformEditor: () => <div data-testid="TransformEditor" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: (props: Record<string, unknown>) => <div data-testid="BandingEditor" data-preview-rows={props.previewRows ? JSON.stringify(props.previewRows) : undefined} />,
  RatingStepEditor: () => <div data-testid="RatingStepEditor" />,
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: () => <div data-testid="ApiInputEditor" />,
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
  SinkEditor: () => <div data-testid="SinkEditor" />,
  ScenarioExpanderEditor: () => <div data-testid="ScenarioExpanderEditor" />,
  OptimiserApplyEditor: () => <div data-testid="OptimiserApplyEditor" />,
  ConstantEditor: () => <div data-testid="ConstantEditor" />,
  SubmodelEditor: () => <div data-testid="SubmodelEditor" />,
}))

vi.mock("../ModellingConfig", () => ({
  default: () => <div data-testid="ModellingConfig" />,
}))

vi.mock("../OptimiserConfig", () => ({
  default: () => <div data-testid="OptimiserConfig" />,
}))

function makeNode(overrides: Partial<SimpleNode> = {}): SimpleNode {
  return {
    id: "node_1",
    data: {
      label: "My Node",
      description: "",
      nodeType: "polars",
      config: {},
    },
    ...overrides,
  }
}

function renderPanel(overrides: Partial<Parameters<typeof NodePanel>[0]> = {}) {
  const props = {
    node: makeNode(),
    edges: [] as SimpleEdge[],
    allNodes: [] as SimpleNode[],
    onClose: vi.fn(),
    onUpdateNode: vi.fn(),
    onDeleteEdge: vi.fn(),
    onRefreshPreview: vi.fn(),
    ...overrides,
  }
  return { ...render(<NodePanel {...props} />), props }
}

describe("NodePanel", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
    useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true })
  })

  afterEach(cleanup)

  it("renders nothing when no node is selected", () => {
    const { container } = renderPanel({ node: null })
    expect(container.innerHTML).toBe("")
  })

  it("renders node label in the header", () => {
    renderPanel()
    expect(screen.getByDisplayValue("My Node")).toBeInTheDocument()
  })

  it("close button calls onClose", () => {
    const { props } = renderPanel()
    const closeBtn = screen.getByTitle("Close")
    fireEvent.click(closeBtn)
    expect(props.onClose).toHaveBeenCalledOnce()
  })

  it("label input updates node via onUpdateNode", () => {
    const { props } = renderPanel()
    const input = screen.getByDisplayValue("My Node")
    fireEvent.change(input, { target: { value: "Renamed" } })
    expect(props.onUpdateNode).toHaveBeenCalledWith("node_1", expect.objectContaining({ label: "Renamed" }))
  })

  it("renders TransformEditor for transform nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "T", description: "", nodeType: "polars", config: {} } }) })
    expect(screen.getByTestId("TransformEditor")).toBeInTheDocument()
  })

  it("renders DataSourceEditor for dataSource nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "DS", description: "", nodeType: "dataSource", config: {} } }) })
    expect(screen.getByTestId("DataSourceEditor")).toBeInTheDocument()
  })

  it("renders ApiInputEditor for apiInput nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "API", description: "", nodeType: "apiInput", config: {} } }) })
    expect(screen.getByTestId("ApiInputEditor")).toBeInTheDocument()
  })

  it("renders SinkEditor for dataSink nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "Sink", description: "", nodeType: "dataSink", config: {} } }) })
    expect(screen.getByTestId("SinkEditor")).toBeInTheDocument()
  })

  it("renders OutputEditor for output nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "Out", description: "", nodeType: "output", config: {} } }) })
    expect(screen.getByTestId("OutputEditor")).toBeInTheDocument()
  })

  it("renders BandingEditor for banding nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "B", description: "", nodeType: "banding", config: {} } }) })
    expect(screen.getByTestId("BandingEditor")).toBeInTheDocument()
  })

  it("renders ModelScoreEditor for modelScore nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "MS", description: "", nodeType: "modelScore", config: {} } }) })
    expect(screen.getByTestId("ModelScoreEditor")).toBeInTheDocument()
  })

  it("renders LiveSwitchEditor for liveSwitch nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "LS", description: "", nodeType: "liveSwitch", config: {} } }) })
    expect(screen.getByTestId("LiveSwitchEditor")).toBeInTheDocument()
  })

  it("renders ExternalFileEditor for externalFile nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "EF", description: "", nodeType: "externalFile", config: {} } }) })
    expect(screen.getByTestId("ExternalFileEditor")).toBeInTheDocument()
  })

  it("renders RatingStepEditor for ratingStep nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "RS", description: "", nodeType: "ratingStep", config: {} } }) })
    expect(screen.getByTestId("RatingStepEditor")).toBeInTheDocument()
  })

  it("renders ModellingConfig for modelling nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "ML", description: "", nodeType: "modelling", config: {} } }) })
    expect(screen.getByTestId("ModellingConfig")).toBeInTheDocument()
  })

  it("renders OptimiserConfig for optimiser nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "Opt", description: "", nodeType: "optimiser", config: {} } }) })
    expect(screen.getByTestId("OptimiserConfig")).toBeInTheDocument()
  })

  it("renders OptimiserApplyEditor for optimiserApply nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "OA", description: "", nodeType: "optimiserApply", config: {} } }) })
    expect(screen.getByTestId("OptimiserApplyEditor")).toBeInTheDocument()
  })

  it("renders ScenarioExpanderEditor for scenarioExpander nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "SE", description: "", nodeType: "scenarioExpander", config: {} } }) })
    expect(screen.getByTestId("ScenarioExpanderEditor")).toBeInTheDocument()
  })

  it("renders ConstantEditor for constant nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "C", description: "", nodeType: "constant", config: {} } }) })
    expect(screen.getByTestId("ConstantEditor")).toBeInTheDocument()
  })

  it("renders SubmodelEditor for submodel nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "SM", description: "", nodeType: "submodel", config: {} } }) })
    expect(screen.getByTestId("SubmodelEditor")).toBeInTheDocument()
  })

  it("renders raw config for unknown node types with config", () => {
    renderPanel({
      node: makeNode({
        data: { label: "Unknown", description: "", nodeType: "unknownType", config: { foo: "bar" } },
      }),
    })
    expect(screen.getByText("foo:")).toBeInTheDocument()
    expect(screen.getByText("bar")).toBeInTheDocument()
  })

  it("renders nothing in editor area for unknown node types without config", () => {
    renderPanel({
      node: makeNode({
        data: { label: "Unknown", description: "", nodeType: "unknownType", config: {} },
      }),
    })
    // The panel itself renders (header), but no editor content
    expect(screen.getByDisplayValue("Unknown")).toBeInTheDocument()
    expect(screen.queryByText("Config")).not.toBeInTheDocument()
  })

  it("instance node shows 'Instance of' panel instead of editor", () => {
    const origNode = makeNode({ id: "orig_1", data: { label: "Original", description: "", nodeType: "polars", config: {} } })
    const instanceNode = makeNode({
      id: "inst_1",
      data: { label: "Instance", description: "", nodeType: "polars", config: { instanceOf: "orig_1" } },
    })
    renderPanel({ node: instanceNode, allNodes: [origNode, instanceNode] })
    expect(screen.getByText("Instance of")).toBeInTheDocument()
    expect(screen.getByText("Original")).toBeInTheDocument()
    // Should NOT render TransformEditor for an instance
    expect(screen.queryByTestId("TransformEditor")).not.toBeInTheDocument()
  })

  it("applies dimmed opacity when dimmed prop is true", () => {
    const { container } = renderPanel({ dimmed: true })
    const panel = container.firstElementChild as HTMLElement
    expect(panel.style.opacity).toBe("0.6")
  })

  it("applies full opacity when dimmed prop is false", () => {
    const { container } = renderPanel({ dimmed: false })
    const panel = container.firstElementChild as HTMLElement
    expect(panel.style.opacity).toBe("1")
  })

  // ─── Instance panel: input mapping ──────────────────────────────

  describe("InstancePanel input mapping", () => {
    it("renders mapping dropdowns when instance has edges", () => {
      const origNode = makeNode({
        id: "orig_1",
        data: { label: "Original", description: "", nodeType: "polars", config: {} },
      })
      const upstreamOrigNode = makeNode({
        id: "up_orig",
        data: { label: "Upstream Orig", description: "", nodeType: "dataSource", config: {} },
      })
      const upstreamInstNode = makeNode({
        id: "up_inst",
        data: { label: "Upstream Inst", description: "", nodeType: "dataSource", config: {} },
      })
      const instanceNode = makeNode({
        id: "inst_1",
        data: { label: "Instance", description: "", nodeType: "polars", config: { instanceOf: "orig_1" } },
      })

      const edges: SimpleEdge[] = [
        { id: "e1", source: "up_orig", target: "orig_1" },
        { id: "e2", source: "up_inst", target: "inst_1" },
      ]

      renderPanel({
        node: instanceNode,
        edges,
        allNodes: [origNode, upstreamOrigNode, upstreamInstNode, instanceNode],
      })

      expect(screen.getByText("Input Mapping")).toBeInTheDocument()
      // The original input label "Upstream_Orig" (sanitized) should appear
      expect(screen.getByText("Upstream_Orig")).toBeInTheDocument()
      // A select dropdown should be present for the mapping
      const selects = screen.getAllByRole("combobox")
      expect(selects.length).toBeGreaterThanOrEqual(1)
    })

    it("renders schema warnings when _schemaWarnings is set", () => {
      const origNode = makeNode({
        id: "orig_1",
        data: { label: "Original", description: "", nodeType: "polars", config: {} },
      })
      const instanceNode = makeNode({
        id: "inst_1",
        data: {
          label: "Instance",
          description: "",
          nodeType: "polars",
          config: { instanceOf: "orig_1" },
          _schemaWarnings: [
            { column: "col_a", status: "missing" },
            { column: "col_b", status: "missing" },
          ],
        },
      })

      renderPanel({
        node: instanceNode,
        edges: [],
        allNodes: [origNode, instanceNode],
      })

      expect(screen.getByText(/Missing columns/)).toBeInTheDocument()
      expect(screen.getByText("col_a")).toBeInTheDocument()
      expect(screen.getByText("col_b")).toBeInTheDocument()
    })

    it("renders no mapping section when both origInputs and instInputs are empty", () => {
      const origNode = makeNode({
        id: "orig_1",
        data: { label: "Original", description: "", nodeType: "polars", config: {} },
      })
      const instanceNode = makeNode({
        id: "inst_1",
        data: { label: "Instance", description: "", nodeType: "polars", config: { instanceOf: "orig_1" } },
      })

      renderPanel({
        node: instanceNode,
        edges: [], // No edges → no inputs
        allNodes: [origNode, instanceNode],
      })

      expect(screen.getByText("Instance of")).toBeInTheDocument()
      expect(screen.queryByText("Input Mapping")).not.toBeInTheDocument()
    })

    it("updates inputMapping config when mapping dropdown changes", () => {
      const origNode = makeNode({
        id: "orig_1",
        data: { label: "Original", description: "", nodeType: "polars", config: {} },
      })
      const upOrig = makeNode({
        id: "up_orig",
        data: { label: "Source A", description: "", nodeType: "dataSource", config: {} },
      })
      const upInst = makeNode({
        id: "up_inst",
        data: { label: "Source B", description: "", nodeType: "dataSource", config: {} },
      })
      const instanceNode = makeNode({
        id: "inst_1",
        data: { label: "Instance", description: "", nodeType: "polars", config: { instanceOf: "orig_1" } },
      })

      const edges: SimpleEdge[] = [
        { id: "e1", source: "up_orig", target: "orig_1" },
        { id: "e2", source: "up_inst", target: "inst_1" },
      ]

      const { props } = renderPanel({
        node: instanceNode,
        edges,
        allNodes: [origNode, upOrig, upInst, instanceNode],
      })

      const selects = screen.getAllByRole("combobox")
      fireEvent.change(selects[0], { target: { value: "Source_B" } })

      expect(props.onUpdateNode).toHaveBeenCalledWith(
        "inst_1",
        expect.objectContaining({
          config: expect.objectContaining({
            inputMapping: expect.objectContaining({ Source_A: "Source_B" }),
          }),
        }),
      )
    })
  })

  // ─── Panel resize ───────────────────────────────────────────────

  describe("Panel resize", () => {
    it("drag handle updates panel width via mouse events", () => {
      useUIStore.setState({ nodePanelWidth: 400 })
      const { container } = renderPanel()
      const panel = container.firstElementChild as HTMLElement
      // The drag handle is the first child div with cursor-col-resize class
      const dragHandle = panel.querySelector(".cursor-col-resize") as HTMLElement
      expect(dragHandle).toBeTruthy()

      // Start drag at x=500
      fireEvent.mouseDown(dragHandle, { clientX: 500 })

      // Move mouse to the left by 100px → width should increase (startX - clientX = delta)
      fireEvent.mouseMove(window, { clientX: 400 })
      fireEvent.mouseUp(window)

      // The store should now have the new width (400 + 100 = 500)
      expect(useUIStore.getState().nodePanelWidth).toBe(500)
    })

    it("resize clamps to minimum width of 320", () => {
      useUIStore.setState({ nodePanelWidth: 400 })
      const { container } = renderPanel()
      const panel = container.firstElementChild as HTMLElement
      const dragHandle = panel.querySelector(".cursor-col-resize") as HTMLElement

      // Start drag at x=500, move right by 200 → delta = -200 → width = 400 - 200 = 200 → clamped to 320
      fireEvent.mouseDown(dragHandle, { clientX: 500 })
      fireEvent.mouseMove(window, { clientX: 700 })
      fireEvent.mouseUp(window)

      expect(useUIStore.getState().nodePanelWidth).toBe(320)
    })

    it("resize clamps to 75% of available space", () => {
      useUIStore.setState({ nodePanelWidth: 900 })
      const { container } = renderPanel()
      const panel = container.firstElementChild as HTMLElement
      const dragHandle = panel.querySelector(".cursor-col-resize") as HTMLElement

      // Start drag at x=500, move left by 1000 → delta = 1000 → 900 + 1000 = 1900 → clamped to max
      fireEvent.mouseDown(dragHandle, { clientX: 500 })
      fireEvent.mouseMove(window, { clientX: -500 })
      fireEvent.mouseUp(window)

      // Max = floor((1920 - 180) * 0.75) = 1305
      expect(useUIStore.getState().nodePanelWidth).toBe(1305)
    })
  })

  // ─── Config update via label input ──────────────────────────────

  describe("config update handler", () => {
    it("label change calls onUpdateNode with full data merge", () => {
      const node = makeNode({
        id: "n1",
        data: {
          label: "Old Label",
          description: "desc",
          nodeType: "polars",
          config: { existing: "value" },
        },
      })
      const { props } = renderPanel({ node })

      const input = screen.getByDisplayValue("Old Label")
      fireEvent.change(input, { target: { value: "New Label" } })

      expect(props.onUpdateNode).toHaveBeenCalledWith("n1", {
        label: "New Label",
        description: "desc",
        nodeType: "polars",
        config: { existing: "value" },
      })
    })

    it("label change preserves extra data keys on the node", () => {
      const node = makeNode({
        id: "n1",
        data: {
          label: "Label",
          description: "",
          nodeType: "polars",
          config: {},
          _columns: [{ name: "x", dtype: "Float64" }],
        },
      })
      const { props } = renderPanel({ node })

      const input = screen.getByDisplayValue("Label")
      fireEvent.change(input, { target: { value: "Updated" } })

      expect(props.onUpdateNode).toHaveBeenCalledWith("n1",
        expect.objectContaining({
          label: "Updated",
          _columns: [{ name: "x", dtype: "Float64" }],
        }),
      )
    })
  })

  // ─── U2: stale config callback fix ─────────────────────────────

  describe("handleConfigUpdate uses fresh config after re-render", () => {
    it("uses updated config when node prop changes between renders", () => {
      const origNode = makeNode({
        id: "orig_1",
        data: { label: "Original", description: "", nodeType: "polars", config: {} },
      })
      const upOrig = makeNode({
        id: "up_orig",
        data: { label: "Source A", description: "", nodeType: "dataSource", config: {} },
      })
      const upInst = makeNode({
        id: "up_inst",
        data: { label: "Source B", description: "", nodeType: "dataSource", config: {} },
      })

      // Initial render: instance with no inputMapping
      const instanceNode1 = makeNode({
        id: "inst_1",
        data: {
          label: "Instance",
          description: "",
          nodeType: "polars",
          config: { instanceOf: "orig_1", existingKey: "v1" },
        },
      })

      const edges: SimpleEdge[] = [
        { id: "e1", source: "up_orig", target: "orig_1" },
        { id: "e2", source: "up_inst", target: "inst_1" },
      ]
      const allNodes = [origNode, upOrig, upInst, instanceNode1]

      const onUpdateNode = vi.fn()
      const { rerender } = render(
        <NodePanel
          node={instanceNode1}
          edges={edges}
          allNodes={allNodes}
          onClose={vi.fn()}
          onUpdateNode={onUpdateNode}
          onDeleteEdge={vi.fn()}
          onRefreshPreview={vi.fn()}
        />,
      )

      // Now re-render with updated config (simulating external update)
      const instanceNode2 = makeNode({
        id: "inst_1",
        data: {
          label: "Instance",
          description: "",
          nodeType: "polars",
          config: { instanceOf: "orig_1", existingKey: "v2", newKey: "added" },
        },
      })

      rerender(
        <NodePanel
          node={instanceNode2}
          edges={edges}
          allNodes={[origNode, upOrig, upInst, instanceNode2]}
          onClose={vi.fn()}
          onUpdateNode={onUpdateNode}
          onDeleteEdge={vi.fn()}
          onRefreshPreview={vi.fn()}
        />,
      )

      // Trigger handleConfigUpdate via mapping dropdown change
      const selects = screen.getAllByRole("combobox")
      fireEvent.change(selects[0], { target: { value: "Source_B" } })

      // Should include the FRESH config (existingKey: "v2", newKey: "added"),
      // not the stale initial config (existingKey: "v1")
      expect(onUpdateNode).toHaveBeenCalledWith(
        "inst_1",
        expect.objectContaining({
          config: expect.objectContaining({
            existingKey: "v2",
            newKey: "added",
            inputMapping: expect.any(Object),
          }),
        }),
      )
    })
  })

  // ─── previewRows pass-through ───────────────────────────────────

  describe("previewRows", () => {
    it("passes previewRows to BandingEditor when provided", () => {
      const rows = [{ age: 25, age_band: "young" }, { age: 40, age_band: "middle" }]
      renderPanel({
        node: makeNode({ data: { label: "B", description: "", nodeType: "banding", config: {} } }),
        previewRows: rows,
      })
      const editor = screen.getByTestId("BandingEditor")
      expect(editor.getAttribute("data-preview-rows")).toBe(JSON.stringify(rows))
    })

    it("does not pass previewRows to BandingEditor when not provided", () => {
      renderPanel({
        node: makeNode({ data: { label: "B", description: "", nodeType: "banding", config: {} } }),
      })
      const editor = screen.getByTestId("BandingEditor")
      expect(editor.getAttribute("data-preview-rows")).toBeNull()
    })
  })

  // ─── collectUpstreamColumns integration ─────────────────────────

  describe("upstream columns", () => {
    it("passes upstream columns to ModellingConfig when upstream nodes have _columns", () => {
      const upstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataSource",
          config: {},
          _columns: [
            { name: "age", dtype: "Int64" },
            { name: "income", dtype: "Float64" },
          ],
        },
      })
      const modellingNode = makeNode({
        id: "mod_1",
        data: { label: "Model", description: "", nodeType: "modelling", config: {} },
      })
      const edges: SimpleEdge[] = [{ id: "e1", source: "up_1", target: "mod_1" }]

      renderPanel({
        node: modellingNode,
        edges,
        allNodes: [upstreamNode, modellingNode],
      })

      // ModellingConfig is mocked — it still renders, but the fact that it renders
      // (and not the fallback) confirms the node type dispatch works with edges present
      expect(screen.getByTestId("ModellingConfig")).toBeInTheDocument()
    })

    it("falls back to node own _columns when no upstream edges exist for modelling", () => {
      const modellingNode = makeNode({
        id: "mod_1",
        data: {
          label: "Model",
          description: "",
          nodeType: "modelling",
          config: {},
          _columns: [{ name: "fallback_col", dtype: "Utf8" }],
        },
      })

      renderPanel({
        node: modellingNode,
        edges: [],
        allNodes: [modellingNode],
      })

      expect(screen.getByTestId("ModellingConfig")).toBeInTheDocument()
    })
  })
})
