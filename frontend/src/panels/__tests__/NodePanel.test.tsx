import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import NodePanel from "../NodePanel"
import { GraphProvider } from "../GraphContext"
import type { SimpleNode, SimpleEdge } from "../editors"
import useUIStore from "../../stores/useUIStore"

const { transformEditorProps, edgeJoinEditorProps, exploreCodeEditorProps, bandingEditorProps, dataInputEditorProps, dataOutputEditorProps, modellingConfigProps, optimiserConfigProps } = vi.hoisted(() => ({
  transformEditorProps: [] as Record<string, unknown>[],
  edgeJoinEditorProps: [] as Record<string, unknown>[],
  exploreCodeEditorProps: [] as Record<string, unknown>[],
  bandingEditorProps: [] as Record<string, unknown>[],
  dataInputEditorProps: [] as Record<string, unknown>[],
  dataOutputEditorProps: [] as Record<string, unknown>[],
  modellingConfigProps: [] as Record<string, unknown>[],
  optimiserConfigProps: [] as Record<string, unknown>[],
}))

// Mock all editor components — we only care that the right one renders
vi.mock("../LazyNodeEditors", async () => {
  const { InputSourcesBar } = await vi.importActual<typeof import("../editors/_shared")>(
    "../editors/_shared",
  )
  return {
  LazyEditorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TransformEditor: (props: Record<string, unknown>) => {
    transformEditorProps.push(props)
    return (
      <div data-testid="TransformEditor">
        <InputSourcesBar
          inputSources={props.inputSources as Parameters<typeof InputSourcesBar>[0]["inputSources"]}
          onDeleteInput={props.onDeleteInput as ((edgeId: string) => void) | undefined}
        />
      </div>
    )
  },
  EdgeJoinEditor: (props: Record<string, unknown>) => {
    edgeJoinEditorProps.push(props)
    return <div data-testid="EdgeJoinEditor" />
  },
  ExploreCodeEditor: (props: Record<string, unknown>) => {
    exploreCodeEditorProps.push(props)
    return <div data-testid="ExploreCodeEditor" />
  },
  ExploreOverviewConfig: () => <div data-testid="explore-overview-config" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: (props: Record<string, unknown>) => {
    bandingEditorProps.push(props)
    return <div data-testid="BandingEditor" data-preview-rows={props.previewRows ? JSON.stringify(props.previewRows) : undefined} />
  },
  RatingStepEditor: (props: Record<string, unknown>) => (
    <div
      data-testid="RatingStepEditor"
      data-node-id={props.nodeId ? String(props.nodeId) : undefined}
      data-preview-rows={props.previewRows ? JSON.stringify(props.previewRows) : undefined}
      data-upstream-columns={props.upstreamColumns ? JSON.stringify(props.upstreamColumns) : undefined}
    />
  ),
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: () => <div data-testid="ApiInputEditor" />,
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
  DataInputEditor: (props: Record<string, unknown>) => {
    dataInputEditorProps.push(props)
    return <div data-testid="DataInputEditor" />
  },
  DataOutputEditor: (props: Record<string, unknown>) => {
    dataOutputEditorProps.push(props)
    return <div data-testid="DataOutputEditor" />
  },
  ScenarioExpanderEditor: () => <div data-testid="ScenarioExpanderEditor" />,
  OptimiserApplyEditor: () => <div data-testid="OptimiserApplyEditor" />,
  ConstantEditor: () => <div data-testid="ConstantEditor" />,
  SubmodelEditor: () => <div data-testid="SubmodelEditor" />,
  ColumnsTab: () => <div data-testid="ColumnsTab" />,
  ModellingConfig: (props: Record<string, unknown>) => {
    modellingConfigProps.push(props)
    return <div data-testid="ModellingConfig" />
  },
  OptimiserConfig: (props: Record<string, unknown>) => {
    optimiserConfigProps.push(props)
    return <div data-testid="OptimiserConfig" />
  },
  }
})

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

type RenderPanelOverrides = Partial<Parameters<typeof NodePanel>[0]> & {
  edges?: SimpleEdge[]
  allNodes?: SimpleNode[]
  submodels?: Record<string, unknown>
  preamble?: string
}

function renderPanel(overrides: RenderPanelOverrides = {}) {
  const {
    edges = [] as SimpleEdge[],
    allNodes = [] as SimpleNode[],
    submodels,
    preamble,
    ...panelOverrides
  } = overrides
  const props = {
    node: makeNode(),
    onClose: vi.fn(),
    onUpdateNode: vi.fn(() => ({ ok: true as const })),
    onDeleteEdge: vi.fn(),
    onSwapEdgeJoinInputs: vi.fn(),
    onRefreshPreview: vi.fn(),
    ...panelOverrides,
  }
  const result = render(
    <GraphProvider allNodes={allNodes} edges={edges} submodels={submodels} preamble={preamble}>
      <NodePanel {...props} />
    </GraphProvider>,
  )
  return { ...result, props }
}

type Phase3InputSource = {
  sourceNodeId: string
  name: string
  sourceLabel: string
  edgeId: string
  frameUnresolved?: boolean
}

function latestTransformInputSources(): Phase3InputSource[] {
  return transformEditorProps.at(-1)?.inputSources as Phase3InputSource[]
}

function eligibleApiInputTable(label: string) {
  return {
    label,
    emit: true,
    columns: [{ name: "value", selected: true }],
  }
}

describe("NodePanel", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
    useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true, explorePanes: {}, explorePreviewPanes: {} })
    transformEditorProps.length = 0
    edgeJoinEditorProps.length = 0
    exploreCodeEditorProps.length = 0
    bandingEditorProps.length = 0
    dataInputEditorProps.length = 0
    dataOutputEditorProps.length = 0
    modellingConfigProps.length = 0
    optimiserConfigProps.length = 0
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
    // Commit-on-blur (undo-atomicity): typing buffers locally.
    expect(props.onUpdateNode).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(props.onUpdateNode).toHaveBeenCalledWith("node_1", expect.objectContaining({ label: "Renamed" }))
  })

  it("clears cached result columns when config changes", () => {
    const node = makeNode({
      data: {
        label: "Transform",
        description: "",
        nodeType: "polars",
        config: { code: "old" },
        _columns: [{ name: "old_output", dtype: "f64" }],
        _availableColumns: [{ name: "old_output", dtype: "f64" }],
        _schemaWarnings: [{ column: "old_output", status: "stale" }],
      },
    })
    const onUpdateNode = vi.fn((_nodeId: string, _data: Record<string, unknown>) => ({ ok: true as const }))
    renderPanel({ node, onUpdateNode })
    const onUpdate = transformEditorProps.at(-1)?.onUpdate as (key: string, value: unknown) => void

    onUpdate("code", "new")

    const updatedData = onUpdateNode.mock.calls.at(-1)?.[1] as Record<string, unknown>
    expect(updatedData.config).toEqual({ code: "new" })
    expect(updatedData._columns).toBeUndefined()
    expect(updatedData._availableColumns).toBeUndefined()
    expect(updatedData._schemaWarnings).toBeUndefined()
  })

  it("renders TransformEditor for transform nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "T", description: "", nodeType: "polars", config: {} } }) })
    expect(screen.getByTestId("TransformEditor")).toBeInTheDocument()
  })

  it("renders EdgeJoinEditor for edgeJoin nodes", () => {
    const onSwapEdgeJoinInputs = vi.fn()
    renderPanel({
      node: makeNode({
        id: "edge_join_1",
        data: {
          label: "Edge Join",
          description: "",
          nodeType: "edgeJoin",
          config: { baseInput: "quotes", joinInput: "lookup", how: "left", on: ["policy_id"] },
        },
      }),
      onSwapEdgeJoinInputs,
    })

    expect(screen.getByTestId("EdgeJoinEditor")).toBeInTheDocument()
    expect(edgeJoinEditorProps.at(-1)).toMatchObject({
      nodeId: "edge_join_1",
      config: { baseInput: "quotes", joinInput: "lookup", how: "left", on: ["policy_id"] },
    })
    const onSwapInputs = edgeJoinEditorProps.at(-1)?.onSwapInputs as (() => void) | undefined
    expect(onSwapInputs).toBeTypeOf("function")

    onSwapInputs?.()

    expect(onSwapEdgeJoinInputs).toHaveBeenCalledWith("edge_join_1")
  })

  it("shows standard Config and Columns panes for edgeJoin nodes", () => {
    renderPanel({
      node: makeNode({
        id: "edge_join_1",
        data: {
          label: "Edge Join",
          description: "",
          nodeType: "edgeJoin",
          config: { baseInput: "quotes", joinInput: "lookup", how: "left", on: ["policy_id"] },
          _columns: [{ name: "policy_id", dtype: "String" }],
          _availableColumns: [{ name: "policy_id", dtype: "String" }],
        },
      }),
    })

    expect(screen.getByRole("button", { name: /^config$/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^columns$/i })).toBeInTheDocument()
    expect(screen.getByTestId("EdgeJoinEditor")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /^columns$/i }))

    expect(screen.getByTestId("ColumnsTab")).toBeInTheDocument()
  })

  it("renders ApiInputEditor for apiInput nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "API", description: "", nodeType: "apiInput", config: {} } }) })
    expect(screen.getByTestId("ApiInputEditor")).toBeInTheDocument()
  })

  it("renders DataInputEditor for dataInput nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "In", description: "", nodeType: "dataInput", config: {} } }) })
    expect(screen.getByTestId("DataInputEditor")).toBeInTheDocument()
  })

  it("renders DataOutputEditor for dataOutput nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "Out", description: "", nodeType: "dataOutput", config: {} } }) })
    expect(screen.getByTestId("DataOutputEditor")).toBeInTheDocument()
  })

  it.each([
    ["dataInput", dataInputEditorProps],
    ["dataOutput", dataOutputEditorProps],
  ] as const)("replaces the complete config for %s nodes and clears cached result shape", (nodeType, editorProps) => {
    const node = makeNode({
      id: "io_node",
      data: {
        label: "I/O",
        description: "",
        nodeType,
        config: { stale: true },
        _columns: [{ name: "old_output", dtype: "f64" }],
        _availableColumns: [{ name: "old_output", dtype: "f64" }],
        _schemaWarnings: [{ column: "old_output", status: "stale" }],
        _columnsSource: "preview",
      },
    })
    const onUpdateNode = vi.fn(() => ({ ok: true as const }))
    renderPanel({ node, onUpdateNode })
    const onReplaceConfig = editorProps.at(-1)?.onReplaceConfig as (
      config: Record<string, unknown>,
    ) => { ok: boolean }

    expect(onReplaceConfig({ format: "parquet" })).toEqual({ ok: true })
    expect(onUpdateNode).toHaveBeenCalledWith("io_node", {
      label: "I/O",
      description: "",
      nodeType,
      config: { format: "parquet" },
    })
  })

  it("reports when a complete config replacement has no node update handler", () => {
    renderPanel({
      node: makeNode({
        data: { label: "In", description: "", nodeType: "dataInput", config: {} },
      }),
      onUpdateNode: undefined,
    })
    const onReplaceConfig = dataInputEditorProps.at(-1)?.onReplaceConfig as (
      config: Record<string, unknown>,
    ) => { ok: boolean; error?: string }

    expect(onReplaceConfig({ format: "parquet" })).toEqual({
      ok: false,
      error: "Node update handler is unavailable.",
    })
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
    renderPanel({ node: makeNode({ id: "rating_1", data: { label: "RS", description: "", nodeType: "ratingStep", config: {} } }) })
    expect(screen.getByTestId("RatingStepEditor")).toHaveAttribute("data-node-id", "rating_1")
  })

  it("renders ModellingConfig for modelling nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "ML", description: "", nodeType: "modelling", config: {} } }) })
    expect(screen.getByTestId("ModellingConfig")).toBeInTheDocument()
  })

  it("renders OptimiserConfig for optimiser nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "Opt", description: "", nodeType: "optimiser", config: {} } }) })
    expect(screen.getByTestId("OptimiserConfig")).toBeInTheDocument()
  })

  it("asks OptimiserConfig to defer fallback column fetches while selected preview is loading", () => {
    renderPanel({
      node: makeNode({
        id: "opt_1",
        data: {
          label: "Opt",
          description: "",
          nodeType: "optimiser",
          config: { data_input: "input_1" },
        },
      }),
      selectedPreviewLoading: true,
    } as unknown as RenderPanelOverrides)

    expect(optimiserConfigProps.at(-1)?.deferColumnFetch).toBe(true)
  })

  it("renders OptimiserApplyEditor for optimiserApply nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "OA", description: "", nodeType: "optimiserApply", config: {} } }) })
    expect(screen.getByTestId("OptimiserApplyEditor")).toBeInTheDocument()
  })

  it("hides generic config controls but shows the refresh action for explore nodes", () => {
    const onRefreshPreview = vi.fn()
    renderPanel({
      node: makeNode({
        data: { label: "Explore Claims", description: "", nodeType: "explore", config: {} },
      }),
      onRefreshPreview,
    })

    expect(screen.queryByRole("button", { name: /^config$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^columns$/i })).not.toBeInTheDocument()
    const refreshButton = screen.getByTitle("Refresh Explore outputs")
    const closeButton = screen.getByTitle("Close")
    expect(refreshButton.compareDocumentPosition(closeButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(refreshButton)

    expect(onRefreshPreview).toHaveBeenCalledOnce()
  })

  it("renders Explore code before analysis panes and switches between them", () => {
    const exploreNode = makeNode({
      id: "explore_1",
      data: {
        label: "Explore Claims",
        description: "",
        nodeType: "explore",
        config: { code: "df = df.filter(pl.col('premium') > 0)" },
      },
    })
    const sourceNode = makeNode({
      id: "source_1",
      data: {
        label: "Claims Source",
        description: "",
        nodeType: "dataInput",
        config: {},
        _columns: [{ name: "premium", dtype: "Int64" }],
      },
    })
    const sourceEdge = { id: "e_source_explore", source: "source_1", target: "explore_1" }
    const otherNode = makeNode({
      id: "polars_1",
      data: { label: "Transform", description: "", nodeType: "polars", config: {} },
    })
    const { rerender, props } = renderPanel({
      node: exploreNode,
      allNodes: [sourceNode, exploreNode],
      edges: [sourceEdge],
    })

    const code = screen.getByRole("tab", { name: "Polars Code" })
    const overview = screen.getByRole("tab", { name: "Overview" })
    const relationships = screen.getByRole("tab", { name: "Relationships" })
    const charts = screen.getByRole("tab", { name: "Charts" })
    const exportPane = screen.getByRole("tab", { name: "Export" })

    expect(code).toHaveAttribute("aria-selected", "true")
    expect(overview).toHaveAttribute("aria-selected", "false")
    expect(relationships).toHaveAttribute("aria-selected", "false")
    expect(charts).toHaveAttribute("aria-selected", "false")
    expect(exportPane).toHaveAttribute("aria-selected", "false")
    expect(screen.getByTestId("ExploreCodeEditor")).toBeInTheDocument()
    expect(exploreCodeEditorProps.at(-1)).toMatchObject({
      config: { code: "df = df.filter(pl.col('premium') > 0)" },
      inputSources: [
        {
          sourceNodeId: "source_1",
          name: "Claims_Source",
          sourceLabel: "Claims Source",
          edgeId: "e_source_explore",
        },
      ],
      upstreamColumns: [{ name: "premium", dtype: "Int64" }],
    })

    fireEvent.click(charts)

    expect(charts).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("explore-charts-pane")).toBeEmptyDOMElement()
    expect(useUIStore.getState().explorePanes.explore_1).toBe("charts")

    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <NodePanel {...props} node={otherNode} />
      </GraphProvider>,
    )
    expect(screen.getByTestId("TransformEditor")).toBeInTheDocument()

    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <NodePanel {...props} node={exploreNode} />
      </GraphProvider>,
    )

    expect(screen.getByRole("tab", { name: "Charts" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("explore-charts-pane")).toBeEmptyDOMElement()
  })

  it("renders the dataset-header toggle when the Explore Overview pane is selected", () => {
    const exploreNode = makeNode({
      id: "explore_1",
      data: {
        label: "Explore Claims",
        description: "",
        nodeType: "explore",
        config: {},
      },
    })
    renderPanel({ node: exploreNode })

    // Default pane is "Polars Code"; switch to Overview.
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }))

    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("explore-overview-config")).toBeInTheDocument()
    // Sanity: the Code editor is no longer mounted while Overview is active.
    expect(screen.queryByTestId("ExploreCodeEditor")).not.toBeInTheDocument()
  })

  it("keeps Explore pane selection separate per Explore node", () => {
    const firstExploreNode = makeNode({
      id: "explore_1",
      data: { label: "Explore Claims", description: "", nodeType: "explore", config: {} },
    })
    const secondExploreNode = makeNode({
      id: "explore_2",
      data: { label: "Explore Policies", description: "", nodeType: "explore", config: {} },
    })
    const { rerender, props } = renderPanel({
      node: firstExploreNode,
    })

    fireEvent.click(screen.getByRole("tab", { name: "Export" }))
    expect(screen.getByRole("tab", { name: "Export" })).toHaveAttribute("aria-selected", "true")

    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <NodePanel {...props} node={secondExploreNode} />
      </GraphProvider>,
    )

    expect(screen.getByRole("tab", { name: "Polars Code" })).toHaveAttribute("aria-selected", "true")

    fireEvent.click(screen.getByRole("tab", { name: "Relationships" }))
    expect(screen.getByRole("tab", { name: "Relationships" })).toHaveAttribute("aria-selected", "true")

    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <NodePanel {...props} node={firstExploreNode} />
      </GraphProvider>,
    )

    expect(screen.getByRole("tab", { name: "Export" })).toHaveAttribute("aria-selected", "true")
    expect(useUIStore.getState().explorePanes).toEqual({
      explore_1: "export",
      explore_2: "relationships",
    })
  })

  it("renders ScenarioExpanderEditor for scenarioExpander nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "SE", description: "", nodeType: "scenarioExpander", config: {} } }) })
    expect(screen.getByTestId("ScenarioExpanderEditor")).toBeInTheDocument()
  })

  describe("frame-aware input source identity", () => {
    it("derives the executable input name for multi-frame, sole-frame, and ordinary edges", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const multiApi = makeNode({
        id: "multi_api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: {
            tables: [
              eligibleApiInputTable("policies"),
              eligibleApiInputTable("DriversRaw"),
            ],
          },
        },
      })
      const singleApi = makeNode({
        id: "single_api",
        data: {
          label: "Vehicle API",
          description: "",
          nodeType: "apiInput",
          config: { tables: [eligibleApiInputTable("vehicles")] },
        },
      })
      const ordinary = makeNode({
        id: "ordinary",
        data: { label: "Claims Source", description: "", nodeType: "dataInput", config: {} },
      })
      const edges: SimpleEdge[] = [
        {
          id: "edge_multi",
          source: multiApi.id,
          target: target.id,
          sourceHandle: "DriversRaw",
        },
        {
          id: "edge_single",
          source: singleApi.id,
          target: target.id,
          sourceHandle: "vehicles",
        },
        {
          id: "edge_ordinary",
          source: ordinary.id,
          target: target.id,
          sourceHandle: "out__claims",
        },
      ]

      renderPanel({ node: target, allNodes: [multiApi, singleApi, ordinary, target], edges })

      const sources = latestTransformInputSources()
      expect(sources).toHaveLength(3)
      expect(sources[0]).toMatchObject({
        edgeId: "edge_multi",
        name: "DriversRaw",
        sourceLabel: "Quote API",
      })
      expect(sources[0].frameUnresolved).not.toBe(true)
      expect(sources[1]).toMatchObject({
        edgeId: "edge_single",
        name: "vehicles",
        sourceLabel: "Vehicle API",
      })
      expect(sources[1].frameUnresolved).not.toBe(true)
      expect(sources[2]).toMatchObject({
        edgeId: "edge_ordinary",
        name: "Claims_Source",
        sourceLabel: "Claims Source",
      })
      expect(sources[2].frameUnresolved).not.toBe(true)
    })

    it("keeps a dangling apiInput handle verbatim and marks it unresolved", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const apiInput = makeNode({
        id: "api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: {
            tables: [{ label: "quotes", emit: true, columns: [{ name: "id", selected: false }] }],
          },
        },
      })

      renderPanel({
        node: target,
        allNodes: [apiInput, target],
        edges: [
          {
            id: "edge_api",
            source: apiInput.id,
            target: target.id,
            sourceHandle: "stale_quotes",
          },
        ],
      })

      expect(latestTransformInputSources()).toEqual([
        expect.objectContaining({
          edgeId: "edge_api",
          name: "stale_quotes",
          sourceLabel: "Quote API",
          frameUnresolved: true,
        }),
      ])
      const warning = screen.getByLabelText(/unresolved.*frame|frame.*unresolved/i)
      expect(warning).toBeVisible()
      expect(warning).toHaveAttribute(
        "title",
        expect.stringMatching(/eligible|emitted|resolv/i),
      )
    })

    it("keeps a null-handle apiInput edge visible as explicitly unresolved", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const apiInput = makeNode({
        id: "api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: { tables: [eligibleApiInputTable("quotes")] },
        },
      })

      renderPanel({
        node: target,
        allNodes: [apiInput, target],
        edges: [{
          id: "edge_api",
          source: apiInput.id,
          target: target.id,
          sourceHandle: null,
        }],
      })

      expect(latestTransformInputSources()).toEqual([
        expect.objectContaining({
          edgeId: "edge_api",
          name: "<unresolved>",
          sourceLabel: "Quote API",
          frameUnresolved: true,
        }),
      ])
      expect(screen.getByLabelText(/unresolved.*frame|frame.*unresolved/i)).toBeVisible()
    })

    it("refreshes a named apiInput display label when only sourceHandle changes", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const apiInput = makeNode({
        id: "api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: {
            tables: [eligibleApiInputTable("policies"), eligibleApiInputTable("drivers")],
          },
        },
      })
      const firstEdge: SimpleEdge = {
        id: "edge_api",
        source: apiInput.id,
        target: target.id,
        sourceHandle: "policies",
      }
      const { rerender, props } = renderPanel({
        node: target,
        allNodes: [apiInput, target],
        edges: [firstEdge],
      })
      const firstSources = latestTransformInputSources()
      expect(firstSources[0].name).toBe("policies")

      rerender(
        <GraphProvider
          allNodes={[apiInput, target]}
          edges={[{ ...firstEdge, sourceHandle: "drivers" }]}
        >
          <NodePanel {...props} />
        </GraphProvider>,
      )

      const reboundSources = latestTransformInputSources()
      expect(reboundSources[0].name).toBe("drivers")
    })

    it("refreshes the derived name when a sole-frame edge is rebound after rename", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const apiInput = makeNode({
        id: "api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: { tables: [eligibleApiInputTable("quotes")] },
        },
      })
      const edge: SimpleEdge = {
        id: "edge_api",
        source: apiInput.id,
        target: target.id,
        sourceHandle: "quotes",
      }
      const { rerender, props } = renderPanel({
        node: target,
        allNodes: [apiInput, target],
        edges: [edge],
      })
      const firstSources = latestTransformInputSources()
      expect(firstSources[0].name).toBe("quotes")

      const renamedApiInput = makeNode({
        ...apiInput,
        data: {
          ...apiInput.data,
          config: { tables: [eligibleApiInputTable("policies")] },
        },
      })
      rerender(
        <GraphProvider
          allNodes={[renamedApiInput, target]}
          edges={[{ ...edge, sourceHandle: "policies" }]}
        >
          <NodePanel {...props} />
        </GraphProvider>,
      )

      const renamedSources = latestTransformInputSources()
      expect(renamedSources[0].name).toBe("policies")
    })

    it("clears unresolved state when resolution changes under an unchanged derived name", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const unresolvedApi = makeNode({
        id: "api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: {
            tables: [{ label: "Quote_API", emit: true, columns: [{ name: "id", selected: false }] }],
          },
        },
      })
      const edge: SimpleEdge = {
        id: "edge_api",
        source: unresolvedApi.id,
        target: target.id,
        sourceHandle: "Quote_API",
      }
      const { rerender, props } = renderPanel({
        node: target,
        allNodes: [unresolvedApi, target],
        edges: [edge],
      })
      const unresolvedSources = latestTransformInputSources()
      expect(unresolvedSources[0]).toMatchObject({
        name: "Quote_API",
        frameUnresolved: true,
      })

      const resolvedApi = makeNode({
        ...unresolvedApi,
        data: {
          ...unresolvedApi.data,
          config: { tables: [eligibleApiInputTable("Quote_API")] },
        },
      })
      rerender(
        <GraphProvider allNodes={[resolvedApi, target]} edges={[edge]}>
          <NodePanel {...props} />
        </GraphProvider>,
      )

      const resolvedSources = latestTransformInputSources()
      expect(resolvedSources[0].name).toBe("Quote_API")
      expect(resolvedSources[0].frameUnresolved).not.toBe(true)
      expect(screen.queryByLabelText(/unresolved.*frame|frame.*unresolved/i)).not.toBeInTheDocument()
    })

    it("keeps duplicate-parent frame inputs distinct and routes removals by edge id", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const apiInput = makeNode({
        id: "api",
        data: {
          label: "Quote API",
          description: "",
          nodeType: "apiInput",
          config: {
            tables: [eligibleApiInputTable("quotes"), eligibleApiInputTable("drivers")],
          },
        },
      })
      const { props } = renderPanel({
        node: target,
        allNodes: [apiInput, target],
        edges: [
          { id: "edge_quotes", source: apiInput.id, target: target.id, sourceHandle: "quotes" },
          { id: "edge_drivers", source: apiInput.id, target: target.id, sourceHandle: "drivers" },
        ],
      })

      expect(latestTransformInputSources()).toEqual([
        expect.objectContaining({
          edgeId: "edge_quotes",
          name: "quotes",
        }),
        expect.objectContaining({
          edgeId: "edge_drivers",
          name: "drivers",
        }),
      ])

      expect(screen.getByText("quotes")).toBeInTheDocument()
      expect(screen.getByText("drivers")).toBeInTheDocument()
      const removeButtons = screen.getAllByRole("button", {
        name: /remove connection from Quote API/i,
      })
      expect(removeButtons).toHaveLength(2)
      fireEvent.click(removeButtons[0])
      fireEvent.click(removeButtons[1])
      expect(props.onDeleteEdge).toHaveBeenNthCalledWith(1, "edge_quotes")
      expect(props.onDeleteEdge).toHaveBeenNthCalledWith(2, "edge_drivers")
    })

    it("derives a flattened submodel output name from the referenced child label", () => {
      const target = makeNode({
        id: "target",
        data: { label: "Target", description: "", nodeType: "polars", config: {} },
      })
      const placeholder = makeNode({
        id: "submodel__pricing",
        data: {
          label: "Pricing Module",
          description: "",
          nodeType: "submodel",
          config: { childNodeIds: ["child_output"], outputPorts: ["child_output"] },
        },
      })
      const child = makeNode({
        id: "child_output",
        data: {
          label: "Child Output",
          description: "",
          nodeType: "polars",
          config: {},
        },
      })

      renderPanel({
        node: target,
        allNodes: [placeholder, target],
        edges: [
          {
            id: "edge_child",
            source: placeholder.id,
            target: target.id,
            sourceHandle: "out__child_output",
          },
        ],
        submodels: { pricing: { graph: { nodes: [child], edges: [] } } },
      })

      expect(latestTransformInputSources()).toEqual([
        expect.objectContaining({
          edgeId: "edge_child",
          name: "Child_Output",
          sourceLabel: "Pricing Module",
        }),
      ])
    })
  })

  it("renders ConstantEditor for constant nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "C", description: "", nodeType: "constant", config: {} } }) })
    expect(screen.getByTestId("ConstantEditor")).toBeInTheDocument()
  })

  it("renders SubmodelEditor for submodel nodes", () => {
    renderPanel({ node: makeNode({ data: { label: "SM", description: "", nodeType: "submodel", config: {} } }) })
    expect(screen.getByTestId("SubmodelEditor")).toBeInTheDocument()
  })

  it("hides preview refresh for submodel placeholders", () => {
    renderPanel({ node: makeNode({ data: { label: "SM", description: "", nodeType: "submodel", config: {} } }) })

    expect(screen.queryByTitle("Refresh preview")).not.toBeInTheDocument()
  })

  it("hides preview refresh and columns for submodel ports typed by React Flow", () => {
    renderPanel({
      node: {
        id: "port_in__source",
        type: "submodelPort",
        data: {
          label: "Source Port",
          description: "",
          config: {},
          portDirection: "input",
          portName: "Source Port",
        },
      } as unknown as SimpleNode,
    })

    expect(screen.queryByTitle("Refresh preview")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^columns$/i })).not.toBeInTheDocument()
  })

  it("renders a fail-loud unknown-node-type banner with read-only diagnostic config", () => {
    renderPanel({
      node: makeNode({
        data: {
          label: "Unknown",
          description: "",
          nodeType: "unknownType",
          config: { foo: "bar", nested: { count: 2 }, enabled: true },
        },
      }),
    })

    const banner = screen.getByRole("alert")
    expect(banner).toHaveTextContent("Unknown node type")
    expect(banner).toHaveTextContent("unknownType")
    expect(screen.getByRole("link", { name: /node documentation/i })).toHaveAttribute(
      "href",
      "/docs/building-models/nodes/",
    )

    const diagnostic = screen.getByTestId("unknown-node-config-diagnostic")
    expect(diagnostic.tagName).toBe("PRE")
    expect(diagnostic).toHaveTextContent('"foo": "bar"')
    expect(diagnostic).toHaveTextContent('"count": 2')
    expect(diagnostic).not.toHaveAttribute("contenteditable", "true")
    expect(screen.queryByText("foo:")).not.toBeInTheDocument()
  })

  it("renders the unknown-node-type banner even when the node has no config", () => {
    renderPanel({
      node: makeNode({
        data: { label: "Unknown", description: "", nodeType: "unknownType", config: {} },
      }),
    })

    expect(screen.getByDisplayValue("Unknown")).toBeInTheDocument()
    expect(screen.getByRole("alert")).toHaveTextContent("Unknown node type")
    expect(screen.getByRole("link", { name: /node documentation/i })).toBeInTheDocument()
    expect(screen.getByTestId("unknown-node-config-diagnostic")).toHaveTextContent("{}")
  })

  it("renders unknown instance-like nodes as diagnostics instead of the instance editor", () => {
    const origNode = makeNode({
      id: "orig_1",
      data: { label: "Original", description: "", nodeType: "polars", config: {} },
    })
    const unknownInstanceNode = makeNode({
      id: "unknown_inst",
      data: {
        label: "Unknown",
        description: "",
        nodeType: "unknownType",
        config: { instanceOf: "orig_1", inputMapping: { a: "b" } },
      },
    })

    renderPanel({ node: unknownInstanceNode, allNodes: [origNode, unknownInstanceNode] })

    expect(screen.getByRole("alert")).toHaveTextContent("Unknown node type")
    expect(screen.getByTestId("unknown-node-config-diagnostic")).toHaveTextContent('"instanceOf": "orig_1"')
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("renders prototype-key node types as unknown instead of treating them as inherited metadata", () => {
    const origNode = makeNode({
      id: "orig_1",
      data: { label: "Original", description: "", nodeType: "polars", config: {} },
    })
    const inheritedKeyNode = makeNode({
      id: "unknown_inst",
      data: {
        label: "Unknown",
        description: "",
        nodeType: "toString",
        config: { instanceOf: "orig_1", inputMapping: { a: "b" } },
      },
    })

    renderPanel({ node: inheritedKeyNode, allNodes: [origNode, inheritedKeyNode] })

    expect(screen.getByRole("alert")).toHaveTextContent("Unknown node type")
    expect(screen.getByRole("alert")).toHaveTextContent("toString")
    expect(screen.getByTestId("unknown-node-config-diagnostic")).toHaveTextContent('"instanceOf": "orig_1"')
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
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

  it("resolves an instance original from hidden submodel metadata", () => {
    const saleFlagNode = makeNode({
      id: "sale_flag",
      data: { label: "sale_flag", description: "", nodeType: "polars", config: {} },
    })
    const originalNode = makeNode({
      id: "competitor_features",
      data: { label: "competitor_features", description: "", nodeType: "polars", config: {} },
    })
    const premiumNode = makeNode({
      id: "premium",
      data: { label: "premium", description: "", nodeType: "scenarioExpander", config: {} },
    })
    const instanceNode = makeNode({
      id: "competitor_features_scenarios",
      data: {
        label: "competitor_features_scenarios",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "competitor_features" },
      },
    })
    const edges: SimpleEdge[] = [
      { id: "instance-input", source: "premium", target: "competitor_features_scenarios" },
    ]
    const submodels = {
      model_stuff: {
        file: "modules/model_stuff.py",
        graph: {
          nodes: [saleFlagNode, originalNode],
          edges: [{ id: "original-input", source: "sale_flag", target: "competitor_features" }],
        },
      },
    }

    renderPanel({
      node: instanceNode,
      edges,
      allNodes: [premiumNode, instanceNode],
      submodels,
    })

    expect(screen.getByText("Instance of")).toBeInTheDocument()
    expect(screen.getByText("competitor_features")).toBeInTheDocument()
    expect(screen.getByText("Input Mapping")).toBeInTheDocument()
    expect(screen.getByText("sale_flag")).toBeInTheDocument()
    expect(screen.getByRole("combobox")).toHaveValue("premium")
    expect(screen.queryByTestId("TransformEditor")).not.toBeInTheDocument()
  })

  it("resolves hidden original inputs from parent submodel boundary edges", () => {
    const originalNode = makeNode({
      id: "competitor_features",
      data: { label: "competitor_features", description: "", nodeType: "polars", config: {} },
    })
    const saleFlagNode = makeNode({
      id: "sale_flag",
      data: { label: "sale_flag", description: "", nodeType: "polars", config: {} },
    })
    const premiumNode = makeNode({
      id: "premium",
      data: { label: "premium", description: "", nodeType: "scenarioExpander", config: {} },
    })
    const submodelNode = makeNode({
      id: "submodel__model_stuff",
      data: { label: "model_stuff", description: "", nodeType: "submodel", config: {} },
    })
    const instanceNode = makeNode({
      id: "competitor_features_scenarios",
      data: {
        label: "competitor_features_scenarios",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "competitor_features" },
      },
    })
    const edges: SimpleEdge[] = [
      {
        id: "boundary-input",
        source: "sale_flag",
        target: "submodel__model_stuff",
        targetHandle: "in__competitor_features",
      },
      { id: "instance-input", source: "premium", target: "competitor_features_scenarios" },
    ]

    renderPanel({
      node: instanceNode,
      edges,
      allNodes: [saleFlagNode, premiumNode, submodelNode, instanceNode],
      submodels: {
        model_stuff: {
          graph: {
            nodes: [originalNode],
            edges: [],
          },
        },
      },
    })

    expect(screen.getByText("Instance of")).toBeInTheDocument()
    expect(screen.getByText("Input Mapping")).toBeInTheDocument()
    expect(screen.getByText("sale_flag")).toBeInTheDocument()
    expect(screen.getByRole("combobox")).toHaveValue("premium")
  })

  it("renders an in-panel diagnostic when visible and hidden originals share an id", () => {
    const visibleOriginal = makeNode({
      id: "shared_features",
      data: { label: "visible_shared_features", description: "", nodeType: "polars", config: {} },
    })
    const hiddenOriginal = makeNode({
      id: "shared_features",
      data: { label: "hidden_shared_features", description: "", nodeType: "polars", config: {} },
    })
    const instanceNode = makeNode({
      id: "shared_features_instance",
      data: {
        label: "shared_features_instance",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "shared_features" },
      },
    })

    renderPanel({
      node: instanceNode,
      allNodes: [visibleOriginal, instanceNode],
      submodels: {
        hidden_model: { graph: { nodes: [hiddenOriginal], edges: [] } },
      },
    })

    expect(screen.getByRole("alert")).toHaveTextContent("Broken instance reference")
    expect(screen.getByRole("alert")).toHaveTextContent("visible graph")
    expect(screen.getByRole("alert")).toHaveTextContent("hidden_model")
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
  })

  it("renders an in-panel diagnostic when multiple hidden submodels contain the original id", () => {
    const firstOriginal = makeNode({
      id: "shared_features",
      data: { label: "shared_features", description: "", nodeType: "polars", config: {} },
    })
    const secondOriginal = makeNode({
      id: "shared_features",
      data: { label: "shared_features", description: "", nodeType: "polars", config: {} },
    })
    const instanceNode = makeNode({
      id: "shared_features_instance",
      data: {
        label: "shared_features_instance",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "shared_features" },
      },
    })

    renderPanel({
      node: instanceNode,
      allNodes: [instanceNode],
      submodels: {
        first_model: { graph: { nodes: [firstOriginal], edges: [] } },
        second_model: { graph: { nodes: [secondOriginal], edges: [] } },
      },
    })

    expect(screen.getByRole("alert")).toHaveTextContent("Broken instance reference")
    expect(screen.getByRole("alert")).toHaveTextContent("first_model")
    expect(screen.getByRole("alert")).toHaveTextContent("second_model")
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
  })

  it("renders an in-panel diagnostic when one hidden submodel graph contains duplicate original ids", () => {
    const firstOriginal = makeNode({
      id: "shared_features",
      data: { label: "shared_features_a", description: "", nodeType: "polars", config: {} },
    })
    const secondOriginal = makeNode({
      id: "shared_features",
      data: { label: "shared_features_b", description: "", nodeType: "polars", config: {} },
    })
    const instanceNode = makeNode({
      id: "shared_features_instance",
      data: {
        label: "shared_features_instance",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "shared_features" },
      },
    })

    renderPanel({
      node: instanceNode,
      allNodes: [instanceNode],
      submodels: {
        duplicated_model: { graph: { nodes: [firstOriginal, secondOriginal], edges: [] } },
      },
    })

    expect(screen.getByRole("alert")).toHaveTextContent("Broken instance reference")
    expect(screen.getByRole("alert")).toHaveTextContent("duplicated_model#1")
    expect(screen.getByRole("alert")).toHaveTextContent("duplicated_model#2")
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
  })

  it("renders an in-panel diagnostic for malformed submodel metadata", () => {
    const instanceNode = makeNode({
      id: "inst_1",
      data: {
        label: "Instance",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "ghost_node" },
      },
    })

    renderPanel({
      node: instanceNode,
      allNodes: [instanceNode],
      submodels: {
        broken_model: { graph: { edges: [] } },
      },
    })

    expect(screen.getByRole("alert")).toHaveTextContent("Broken instance reference")
    expect(screen.getByRole("alert")).toHaveTextContent("broken_model")
    expect(screen.getByRole("alert")).toHaveTextContent("graph.nodes")
  })

  it("renders an in-panel diagnostic for malformed instanceOf values", () => {
    const instanceNode = makeNode({
      id: "inst_1",
      data: {
        label: "Instance",
        description: "",
        nodeType: "polars",
        config: { instanceOf: { id: "orig_1" } },
      },
    })

    renderPanel({ node: instanceNode, allNodes: [instanceNode] })

    expect(screen.getByRole("alert")).toHaveTextContent("Broken instance reference")
    expect(screen.getByRole("alert")).toHaveTextContent("non-empty string")
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
  })

  it("renders an in-panel diagnostic for a missing instance original", () => {
    const instanceNode = makeNode({
      id: "inst_1",
      data: {
        label: "Instance",
        description: "",
        nodeType: "polars",
        config: { instanceOf: "ghost_node" },
      },
    })

    renderPanel({ node: instanceNode, allNodes: [instanceNode] })

    expect(screen.getByRole("alert")).toHaveTextContent("Broken instance reference")
    expect(screen.getByRole("alert")).toHaveTextContent("ghost_node")
    expect(screen.getByTitle("Close")).toBeInTheDocument()
    expect(screen.queryByText("Instance of")).not.toBeInTheDocument()
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
        data: { label: "Upstream Orig", description: "", nodeType: "dataInput", config: {} },
      })
      const upstreamInstNode = makeNode({
        id: "up_inst",
        data: { label: "Upstream Inst", description: "", nodeType: "dataInput", config: {} },
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
        data: { label: "Source A", description: "", nodeType: "dataInput", config: {} },
      })
      const upInst = makeNode({
        id: "up_inst",
        data: { label: "Source B", description: "", nodeType: "dataInput", config: {} },
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
      fireEvent.blur(input)

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
      fireEvent.blur(input)

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
        data: { label: "Source A", description: "", nodeType: "dataInput", config: {} },
      })
      const upInst = makeNode({
        id: "up_inst",
        data: { label: "Source B", description: "", nodeType: "dataInput", config: {} },
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

      const onUpdateNode = vi.fn(() => ({ ok: true as const }))
      const { rerender } = render(
        <GraphProvider allNodes={allNodes} edges={edges}>
          <NodePanel
            node={instanceNode1}
            onClose={vi.fn()}
            onUpdateNode={onUpdateNode}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
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
        <GraphProvider allNodes={[origNode, upOrig, upInst, instanceNode2]} edges={edges}>
          <NodePanel
            node={instanceNode2}
            onClose={vi.fn()}
            onUpdateNode={onUpdateNode}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
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

    it("passes previewRows and upstream columns to RatingStepEditor", () => {
      const rows = [{ channel: "direct", premium: 100 }, { channel: "broker", premium: 125 }]
      const upstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [
            { name: "channel", dtype: "String" },
            { name: "premium", dtype: "Float64" },
          ],
        },
      })
      const ratingNode = makeNode({
        id: "rating_1",
        data: { label: "Rating", description: "", nodeType: "ratingStep", config: {} },
      })
      const edges: SimpleEdge[] = [{ id: "e1", source: "up_1", target: "rating_1" }]

      renderPanel({
        node: ratingNode,
        allNodes: [upstreamNode, ratingNode],
        edges,
        previewRows: rows,
      })

      const editor = screen.getByTestId("RatingStepEditor")
      expect(editor.getAttribute("data-preview-rows")).toBe(JSON.stringify(rows))
      expect(editor.getAttribute("data-upstream-columns")).toBe(JSON.stringify([
        { name: "channel", dtype: "String" },
        { name: "premium", dtype: "Float64" },
      ]))
    })
  })

  // ─── collectUpstreamColumns integration ─────────────────────────

  describe("upstream columns", () => {
    it("keeps upstreamColumns prop identity stable across rerenders with the same graph context", () => {
      const upstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [{ name: "age", dtype: "Int64" }],
        },
      })
      const transformNode = makeNode({
        id: "polars_1",
        data: { label: "Transform", description: "", nodeType: "polars", config: {} },
      })
      const edges: SimpleEdge[] = [{ id: "e1", source: "up_1", target: "polars_1" }]
      const allNodes = [upstreamNode, transformNode]

      const { rerender } = render(
        <GraphProvider allNodes={allNodes} edges={edges}>
          <NodePanel
            node={transformNode}
            onClose={vi.fn()}
            onUpdateNode={vi.fn(() => ({ ok: true as const }))}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
      )

      const firstColumns = transformEditorProps.at(-1)?.upstreamColumns

      rerender(
        <GraphProvider allNodes={allNodes} edges={edges}>
          <NodePanel
            node={{ ...transformNode }}
            onClose={vi.fn()}
            onUpdateNode={vi.fn(() => ({ ok: true as const }))}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
      )

      expect(transformEditorProps).toHaveLength(2)
      expect(transformEditorProps.at(-1)?.upstreamColumns).toBe(firstColumns)
    })

    it("keeps upstreamColumns prop identity stable when selected node config changes only", () => {
      const upstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [{ name: "age", dtype: "Int64" }],
        },
      })
      const transformNode = makeNode({
        id: "polars_1",
        data: { label: "Transform", description: "", nodeType: "polars", config: { code: "old" } },
      })
      const updatedTransformNode = makeNode({
        id: "polars_1",
        data: { label: "Transform", description: "", nodeType: "polars", config: { code: "new" } },
      })
      const edges: SimpleEdge[] = [{ id: "e1", source: "up_1", target: "polars_1" }]

      const { rerender } = render(
        <GraphProvider allNodes={[upstreamNode, transformNode]} edges={edges}>
          <NodePanel
            node={transformNode}
            onClose={vi.fn()}
            onUpdateNode={vi.fn(() => ({ ok: true as const }))}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
      )

      const firstColumns = transformEditorProps.at(-1)?.upstreamColumns

      rerender(
        <GraphProvider allNodes={[{ ...upstreamNode }, updatedTransformNode]} edges={edges}>
          <NodePanel
            node={updatedTransformNode}
            onClose={vi.fn()}
            onUpdateNode={vi.fn(() => ({ ok: true as const }))}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
      )

      expect(transformEditorProps).toHaveLength(2)
      expect(transformEditorProps.at(-1)?.upstreamColumns).toBe(firstColumns)
    })

    it("refreshes memoized upstreamColumns when upstream preview columns change in graph context", () => {
      const transformNode = makeNode({
        id: "polars_1",
        data: { label: "Transform", description: "", nodeType: "polars", config: {} },
      })
      const upstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [{ name: "age", dtype: "Int64" }],
        },
      })
      const updatedUpstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [
            { name: "age", dtype: "Int64" },
            { name: "income", dtype: "Float64" },
          ],
        },
      })
      const edges: SimpleEdge[] = [{ id: "e1", source: "up_1", target: "polars_1" }]

      const { rerender } = render(
        <GraphProvider allNodes={[upstreamNode, transformNode]} edges={edges}>
          <NodePanel
            node={transformNode}
            onClose={vi.fn()}
            onUpdateNode={vi.fn(() => ({ ok: true as const }))}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
      )

      const firstColumns = transformEditorProps.at(-1)?.upstreamColumns

      rerender(
        <GraphProvider allNodes={[updatedUpstreamNode, transformNode]} edges={edges}>
          <NodePanel
            node={transformNode}
            onClose={vi.fn()}
            onUpdateNode={vi.fn(() => ({ ok: true as const }))}
            onDeleteEdge={vi.fn()}
            onRefreshPreview={vi.fn()}
          />
        </GraphProvider>,
      )

      const nextColumns = transformEditorProps.at(-1)?.upstreamColumns
      expect(nextColumns).not.toBe(firstColumns)
      expect(nextColumns).toEqual([
        { name: "age", dtype: "Int64" },
        { name: "income", dtype: "Float64" },
      ])
    })

    it("dedupes upstream columns by name while preserving first upstream edge order", () => {
      const upstreamA = makeNode({
        id: "up_a",
        data: {
          label: "Source A",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [
            { name: "age", dtype: "Int64" },
            { name: "income", dtype: "Float64" },
          ],
        },
      })
      const upstreamB = makeNode({
        id: "up_b",
        data: {
          label: "Source B",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [
            { name: "income", dtype: "Decimal" },
            { name: "region", dtype: "Utf8" },
          ],
        },
      })
      const bandingNode = makeNode({
        id: "band_1",
        data: { label: "Band", description: "", nodeType: "banding", config: {} },
      })
      const edges: SimpleEdge[] = [
        { id: "e1", source: "up_a", target: "band_1" },
        { id: "e2", source: "up_b", target: "band_1" },
      ]

      renderPanel({
        node: bandingNode,
        edges,
        allNodes: [upstreamA, upstreamB, bandingNode],
      })

      expect(bandingEditorProps.at(-1)?.upstreamColumns).toEqual([
        { name: "age", dtype: "Int64" },
        { name: "income", dtype: "Float64" },
        { name: "region", dtype: "Utf8" },
      ])
    })

    it("passes upstream columns to ModellingConfig when upstream nodes have _columns", () => {
      const upstreamNode = makeNode({
        id: "up_1",
        data: {
          label: "Source",
          description: "",
          nodeType: "dataInput",
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
      expect(modellingConfigProps.at(-1)?.upstreamColumns).toEqual([
        { name: "age", dtype: "Int64" },
        { name: "income", dtype: "Float64" },
      ])
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
      expect(modellingConfigProps.at(-1)?.upstreamColumns).toEqual([
        { name: "fallback_col", dtype: "Utf8" },
      ])
    })
  })
})
