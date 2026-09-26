import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { act, render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import { useEffect, useRef, useState } from "react"
import OptimiserConfig from "../OptimiserConfig"
import { GraphProvider } from "../GraphContext"
import useNodeResultsStore, { hashConfig } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore, { type OptimiserPane } from "../../stores/useUIStore"
import useGraphStore from "../../stores/useGraphStore"
import useOptimiserPublishStore from "../../stores/useOptimiserPublishStore"
import { solveConfigHash } from "../optimiser/solveActions"
import type { SimpleNode, SimpleEdge } from "../editors"
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../../api/types"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"
import { makeSolveResult } from "../../test-utils/factories"

// ── Mock API client ──
const mockSolveOptimiser = vi.fn()
const mockEstimateOptimiserSolve = vi.fn()
const mockStartOptimiserFrontierAutoRange = vi.fn()
const mockGetOptimiserFrontierAutoRangeStatus = vi.fn()
const mockCancelOptimiserFrontierAutoRange = vi.fn()
const mockCancelOptimiserSolve = vi.fn()
const mockSaveOptimiser = vi.fn()
const mockLogOptimiserToMlflow = vi.fn()
const mockSelectFrontierPoint = vi.fn()

vi.mock("../../api/client", () => ({
  solveOptimiser: (...args: unknown[]) => mockSolveOptimiser(...args),
  estimateOptimiserSolve: (...args: unknown[]) => mockEstimateOptimiserSolve(...args),
  startOptimiserFrontierAutoRange: (...args: unknown[]) => mockStartOptimiserFrontierAutoRange(...args),
  getOptimiserFrontierAutoRangeStatus: (...args: unknown[]) => mockGetOptimiserFrontierAutoRangeStatus(...args),
  cancelOptimiserFrontierAutoRange: (...args: unknown[]) => mockCancelOptimiserFrontierAutoRange(...args),
  cancelOptimiserSolve: (...args: unknown[]) => mockCancelOptimiserSolve(...args),
  saveOptimiser: (...args: unknown[]) => mockSaveOptimiser(...args),
  logOptimiserToMlflow: (...args: unknown[]) => mockLogOptimiserToMlflow(...args),
  selectFrontierPoint: (...args: unknown[]) => mockSelectFrontierPoint(...args),
}))

// ── Mock buildGraph ──
vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

// ── Mock banding utilities ──
vi.mock("../../utils/banding", () => ({
  classifyBandingNode: vi.fn(() => ({
    levels: {},
    configuredOutputs: [],
    zeroLevelOutputs: [],
    zeroLevelIssues: [],
  })),
}))
import { classifyBandingNode } from "../../utils/banding"

// ── Mock hooks ──
const mockHandleAddConstraint = vi.fn()
const mockHandleRemoveConstraint = vi.fn()
const mockHandleConstraintColumnChange = vi.fn()
const mockHandleConstraintValueChange = vi.fn()

vi.mock("../../hooks/useDataInputColumns", () => ({
  useDataInputColumns: vi.fn(() => [
    { name: "premium", dtype: "Float64" },
    { name: "loss_ratio", dtype: "Float64" },
    { name: "volume", dtype: "Float64" },
  ]),
}))
import { useDataInputColumns } from "../../hooks/useDataInputColumns"
const mockUseDataInputColumns = vi.mocked(useDataInputColumns)

vi.mock("../../hooks/useConstraintHandlers", () => ({
  useConstraintHandlers: vi.fn(() => ({
    handleAddConstraint: mockHandleAddConstraint,
    handleRemoveConstraint: mockHandleRemoveConstraint,
    handleConstraintColumnChange: mockHandleConstraintColumnChange,
    handleConstraintValueChange: mockHandleConstraintValueChange,
  })),
}))

// ── MLflow inventory helpers ─────────────────────────────────────

type MlflowSlice = ReturnType<typeof useSettingsStore.getState>["mlflow"]

function mlflowEntry(
  key: MlflowDestinationKey,
  over: Partial<MlflowDestinationEntry> = {},
): MlflowDestinationEntry {
  return {
    key,
    configured: true,
    destination: "",
    config_source: "env",
    detail: "",
    probed: false,
    ok: false,
    category: "",
    ...over,
  }
}

const MLFLOW_DATABRICKS = mlflowEntry("databricks", {
  destination: "databricks://team",
  probed: true,
  ok: true,
})
const MLFLOW_SERVER = mlflowEntry("server", {
  destination: "http://mlflow.example:5000",
  config_source: "toml",
  probed: true,
  ok: true,
})
const MLFLOW_LOCAL = mlflowEntry("local", {
  destination: "C:/proj/mlruns",
  config_source: "default",
})
const MLFLOW_SERVER_UNCONFIGURED = mlflowEntry("server", {
  configured: false,
  destination: "",
  config_source: "",
  detail: "No MLflow server is configured. Set [mlflow] tracking_uri in haute.toml.",
})

/** Default inventory: local only, so nothing probes and auto is local. */
function setMlflowInventory(over: Partial<MlflowSlice> = {}): void {
  useSettingsStore.setState({
    mlflow: {
      status: "ready",
      installed: true,
      importable: true,
      destinations: [MLFLOW_LOCAL],
      detail: "",
      ...over,
    },
  })
}

// ── Default graph fixture ───────────────────────────────────────────
// Matches the pre-refactor `makeProps` fixture — a single upstream data-source
// node connected to the optimiser.  Tests can override via the `graph` option
// on makeProps, which is threaded through `<GraphProvider>` in tests.
const DEFAULT_GRAPH_NODES: SimpleNode[] = [
  {
    id: "input_1",
    data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} },
  },
]
const DEFAULT_GRAPH_EDGES: SimpleEdge[] = [{ id: "e1", source: "input_1", target: "opt_1" }]

function withAuthoritativeInputIdentities(
  nodes: SimpleNode[],
  edges: SimpleEdge[],
): SimpleNode[] {
  return nodes.map((node) => {
    if (node.data.nodeType === "apiInput") {
      const handleNames = edges
        .filter((edge) => edge.source === node.id && typeof edge.sourceHandle === "string")
        .map((edge) => edge.sourceHandle as string)
      return {
        ...node,
        data: {
          ...node.data,
          _defaultInputName: null,
          _sourceHandleInputNames: Object.fromEntries(handleNames.map((name) => [name, name])),
        },
      }
    }
    return {
      ...node,
      data: {
        ...node.data,
        _defaultInputName: node.data._defaultInputName
          ?? node.data.label.replace(/[^A-Za-z0-9_]+/g, "_"),
        _sourceHandleInputNames: node.data._sourceHandleInputNames ?? {},
      },
    }
  })
}

// ── Default props ──
// The pane each describe block renders; `withPane` sets it per block.
let defaultPane: OptimiserPane = "data"
function withPane(pane: OptimiserPane) {
  beforeEach(() => {
    defaultPane = pane
  })
}

type MakePropsOverrides = Partial<Parameters<typeof OptimiserConfig>[0]> & {
  allNodes?: SimpleNode[]
  edges?: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

/**
 * Returns the component props plus the graph data, so tests can wrap
 * `<OptimiserConfig {...props} />` in `<GraphProvider {...graph}>` at render
 * time.  Graph keys (allNodes/edges/submodels/preamble) live on the returned
 * object under `graph` — they're not spread onto the component.
 */
function makeProps(overrides: MakePropsOverrides = {}) {
  const {
    allNodes = DEFAULT_GRAPH_NODES,
    edges = DEFAULT_GRAPH_EDGES,
    submodels,
    preamble,
    ...componentOverrides
  } = overrides
  const componentProps = {
    config: {
      _nodeId: "opt_1",
      mode: "online",
      objective: "premium",
      constraints: {},
    } as Record<string, unknown>,
    onUpdate: vi.fn(),
    accentColor: "var(--warning-strong)",
    activePane: defaultPane,
    upstreamColumns: [
      { name: "premium", dtype: "Float64" },
      { name: "loss_ratio", dtype: "Float64" },
      { name: "volume", dtype: "Float64" },
      { name: "quote_id", dtype: "String" },
      { name: "scenario_index", dtype: "Int64" },
      { name: "scenario_value", dtype: "Float64" },
    ],
    ...componentOverrides,
  }
  return {
    componentProps,
    graph: {
      allNodes: withAuthoritativeInputIdentities(allNodes, edges),
      edges,
      submodels,
      preamble,
    },
  }
}

/**
 * The editor bound to the UI store's remembered pane, as NodePanel binds it:
 * `showPane` and the Solve pane's Go to links switch panes mid-test.
 */
function PaneBoundConfig(props: Parameters<typeof OptimiserConfig>[0]) {
  const remembered = useUIStore((state) => state.optimiserPanes[String(props.config._nodeId)])
  return <OptimiserConfig {...props} activePane={remembered ?? props.activePane} />
}

function showPane(pane: OptimiserPane) {
  act(() => {
    useUIStore.getState().setOptimiserPane("opt_1", pane)
  })
}

/**
 * Renders OptimiserConfig wrapped in a GraphProvider seeded with the graph
 * data from `makeProps`.  This mirrors the production wiring in App.tsx.
 */
function renderConfig(made: ReturnType<typeof makeProps>) {
  return render(
    <GraphProvider
      allNodes={made.graph.allNodes}
      edges={made.graph.edges}
      submodels={made.graph.submodels}
      preamble={made.graph.preamble}
    >
      <PaneBoundConfig {...made.componentProps} />
    </GraphProvider>,
  )
}

function renderStatefulConfig(
  made: ReturnType<typeof makeProps>,
  onUpdateSpy = vi.fn(),
) {
  function StatefulConfig() {
    const [config, setConfig] = useState(made.componentProps.config)
    const configRef = useRef(config)
    useEffect(() => {
      configRef.current = config
    }, [config])
    const handleUpdate = (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => {
      const currentConfig = configRef.current
      if (typeof keyOrUpdates === "string") {
        onUpdateSpy(keyOrUpdates, value)
        setConfig({ ...currentConfig, [keyOrUpdates]: value })
      } else {
        onUpdateSpy(keyOrUpdates)
        setConfig({ ...currentConfig, ...keyOrUpdates })
      }
      return { ok: true as const }
    }

    return (
      <GraphProvider
        allNodes={made.graph.allNodes}
        edges={made.graph.edges}
        submodels={made.graph.submodels}
        preamble={made.graph.preamble}
      >
        <PaneBoundConfig
          {...made.componentProps}
          config={config}
          onUpdate={handleUpdate}
        />
      </GraphProvider>
    )
  }

  return render(<StatefulConfig />)
}

// ── Store reset ──
const INITIAL_STRUCTURAL_VERSION = useGraphStore.getState().structuralVersion

beforeEach(() => {
  defaultPane = "data"
  useGraphStore.setState({ structuralVersion: INITIAL_STRUCTURAL_VERSION })
  useUIStore.setState({ optimiserPanes: {} })
  useOptimiserPublishStore.setState({ byNode: {} })
  useNodeResultsStore.setState({
    solveJobs: {},
    solveResults: {},
  })
  useSettingsStore.setState({
    openSections: {},
  })
  setMlflowInventory()
  mockSolveOptimiser.mockReset()
  // Never-resolving promise so tests don't race with the estimate's async
  // settlement — mirrors the ModellingConfig.test.tsx pattern.
  mockEstimateOptimiserSolve.mockReset().mockReturnValue(new Promise(() => {}))
  mockStartOptimiserFrontierAutoRange.mockReset()
  mockGetOptimiserFrontierAutoRangeStatus.mockReset()
  mockCancelOptimiserFrontierAutoRange.mockReset()
  mockHandleAddConstraint.mockReset()
  mockHandleRemoveConstraint.mockReset()
  mockHandleConstraintColumnChange.mockReset()
  mockHandleConstraintValueChange.mockReset()
  mockUseDataInputColumns.mockReset().mockReturnValue([
    { name: "premium", dtype: "Float64" },
    { name: "loss_ratio", dtype: "Float64" },
    { name: "volume", dtype: "Float64" },
    { name: "quote_id", dtype: "String" },
    { name: "scenario_index", dtype: "Int64" },
    { name: "scenario_value", dtype: "Float64" },
  ])
  vi.mocked(classifyBandingNode).mockReset()
  vi.mocked(classifyBandingNode).mockReturnValue({
    levels: {},
    configuredOutputs: [],
    zeroLevelOutputs: [],
    zeroLevelIssues: [],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ═══════════════════════════════════════════════════════════════════
// Mode toggle
// ═══════════════════════════════════════════════════════════════════

describe("OptimiserConfig", () => {
  describe("Mode toggle", () => {
    withPane("data")

    it("renders with online mode selected by default", () => {
      renderConfig(makeProps())
      const onlineBtn = screen.getByRole("button", { name: "Online" })
      // Online button should have the active orange background
      expect(onlineBtn).toHaveStyle({ color: "var(--warning-strong)" })
    })

    it("renders ratebook mode as active when config.mode is ratebook", () => {
      renderConfig(makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } }))
      const ratebookBtn = screen.getByRole("button", { name: "Ratebook" })
      expect(ratebookBtn).toHaveStyle({ color: "var(--warning-strong)" })
    })

    it("clicking ratebook calls onUpdate with mode ratebook", () => {
      const props = makeProps()
      renderConfig(props)
      fireEvent.click(screen.getByRole("button", { name: "Ratebook" }))
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("mode", "ratebook")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Input / Objective selection
  // ═══════════════════════════════════════════════════════════════════

  describe("Input / Objective selection", () => {
    withPane("data")

    it("uses distinct executable edge input names for optimiser selectors", () => {
      const props = makeProps({
        config: { _nodeId: "opt_1", mode: "ratebook", data_input: "quote_info", banding_source: "Banding_node", objective: "premium", constraints: {} },
        allNodes: [
          { id: "api", data: { label: "Quote API", description: "", nodeType: "apiInput", config: {} } },
          { id: "banding", data: { label: "Banding node", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "quotes", source: "api", sourceHandle: "quote_info", target: "opt_1" },
          { id: "drivers", source: "api", sourceHandle: "driver_info", target: "opt_1" },
          { id: "banding-edge", source: "banding", target: "opt_1" },
        ],
      })
      renderConfig(props)

      const selects = screen.getAllByRole("combobox") as HTMLSelectElement[]
      expect(selects[0]).toHaveValue("quote_info")
      expect(Array.from(selects[0].options).map((option) => [option.value, option.text])).toEqual(
        expect.arrayContaining([["quote_info", "quote_info"], ["driver_info", "driver_info"]]),
      )

      showPane("factors")
      expect(screen.getByRole("combobox", { name: "Rating Factor Source" })).toHaveValue("Banding_node")
    })

    it("shows input node selector with connected nodes", () => {
      renderConfig(makeProps())
      // The dropdown should contain the connected node option
      expect(screen.getByText("Data_Input")).toBeInTheDocument()
    })

    it("shows 'No inputs connected' when no edges exist", () => {
      renderConfig(makeProps({ edges: [] }))
      expect(screen.getByText(/No inputs connected/)).toBeInTheDocument()
    })

    it("rejects a stale exact data-input selection even when one input remains", () => {
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          data_input: "removed_input",
          objective: "premium",
          constraints: {},
        },
      }))

      expect(screen.getByRole("alert")).toHaveTextContent(
        "The configured Objectives & Constraints input is not connected.",
      )
      expect(screen.getByRole("option", { name: "Missing input" })).toBeInTheDocument()
      showPane("solve")
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The selected Objectives & Constraints input is not connected.",
      )
      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "",
        expect.any(Array),
        expect.any(Array),
        undefined,
        undefined,
        expect.any(Object),
      )
    })

    it("rejects a falsey non-string data-input selector instead of inferring one input", () => {
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          data_input: 0,
          objective: "premium",
          constraints: {},
        },
      }))

      expect(screen.getByRole("alert")).toHaveTextContent(
        "The configured Objectives & Constraints input must be an input name.",
      )
      showPane("solve")
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The Objectives & Constraints input must be an input name.",
      )
      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "",
        expect.any(Array),
        expect.any(Array),
        undefined,
        undefined,
        expect.any(Object),
      )
    })

    it("objective column dropdown lists data input columns", () => {
      renderConfig(makeProps())
      // Upstream columns are supplied by NodePanel and should populate
      // the objective select without needing a schema-preview request.
      // These appear as options in the objective select
      const options = screen.getAllByText(/premium/)
      expect(options.length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText(/loss_ratio \(Float64\)/)).toBeInTheDocument()
      expect(screen.getByText(/volume \(Float64\)/)).toBeInTheDocument()
    })

    it("disables data-input column fetches when upstream columns exist", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          data_input: "Data_Input",
          objective: "expected_margin",
          constraints: {},
        },
        upstreamColumns: [{ name: "expected_margin", dtype: "Float64" }],
      })

      renderConfig(props)

      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "opt_1",
        props.graph.allNodes,
        props.graph.edges,
        undefined,
        undefined,
        {
          enabled: false,
          fallbackColumns: props.componentProps.upstreamColumns,
        },
      )
    })

    it("fetches the selected optimiser input without mixing multi-input upstream columns", () => {
      const dataInputNode = {
        id: "input_1",
        data: {
          label: "Data Input",
          description: "",
          nodeType: "dataInput",
          config: {},
          _columns: [{ name: "expected_margin", dtype: "Float64" }],
        },
      } satisfies SimpleNode
      const bandingNode = {
        id: "rating_factors",
        data: {
          label: "Rating Factors",
          description: "",
          nodeType: "banding",
          config: {},
          _columns: [{ name: "rating_factor_only", dtype: "Utf8" }],
        },
      } satisfies SimpleNode
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          data_input: "Data_Input",
          objective: "expected_margin",
          constraints: {},
        },
        allNodes: [dataInputNode, bandingNode],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "rating_factors", target: "opt_1" },
        ],
        upstreamColumns: [
          { name: "expected_margin", dtype: "Float64" },
          { name: "rating_factor_only", dtype: "Utf8" },
        ],
      })

      renderConfig(props)

      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "opt_1",
        props.graph.allNodes,
        props.graph.edges,
        undefined,
        undefined,
        {
          enabled: true,
          fallbackColumns: [],
        },
      )
      expect(screen.queryByText(/rating_factor_only \(Utf8\)/)).not.toBeInTheDocument()
    })

    it("defers data-input column fetches while the selected preview is loading", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          data_input: "Data_Input",
          objective: "expected_margin",
          constraints: {},
        },
        upstreamColumns: [],
        deferColumnFetch: true,
      } as unknown as MakePropsOverrides)

      renderConfig(props)

      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "opt_1",
        props.graph.allNodes,
        props.graph.edges,
        undefined,
        undefined,
        {
          enabled: false,
          fallbackColumns: [],
        },
      )
      expect(mockEstimateOptimiserSolve).not.toHaveBeenCalled()
    })

    it("objective change calls onUpdate with objective key", () => {
      const props = makeProps()
      renderConfig(props)
      // Find the objective select — it has the "Select objective..." placeholder
      const selects = screen.getAllByRole("combobox")
      const objectiveSelect = selects.find(s =>
        Array.from(s.querySelectorAll("option")).some(o => o.textContent === "Select objective..."),
      )!
      fireEvent.change(objectiveSelect, { target: { value: "loss_ratio" } })
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("objective", "loss_ratio")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Ratebook mode specific
  // ═══════════════════════════════════════════════════════════════════

  describe("Ratebook mode", () => {
    withPane("factors")

    it("shows Rating Factor Source section in ratebook mode", () => {
      renderConfig(makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } }))
      expect(screen.getByText("Rating Factor Source")).toBeInTheDocument()
    })

    it("shows 'No Banding nodes found' when no banding nodes connected", () => {
      renderConfig(makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } }))
      expect(screen.getByText(/No Banding nodes found/)).toBeInTheDocument()
    })

    it("warns when an explicit Banding source is no longer directly connected", () => {
      renderConfig(makeProps({
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {}, banding_source: "removed_banding" },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [{ id: "e1", source: "banding_1", target: "opt_1" }],
      }))
      expect(screen.getByRole("alert")).toHaveTextContent(/removed_banding/)
    })

    it("treats a whitespace-decorated Banding input name as stale", () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: { age_band: ["Young"] },
        configuredOutputs: ["age_band"],
        zeroLevelOutputs: [],
        zeroLevelIssues: [],
      })
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "ratebook",
          objective: "premium",
          constraints: {},
          banding_source: " Age_Vehicle_Banding ",
          factor_columns: [["age_band"]],
        },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Age Vehicle Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "banding_1", target: "opt_1" },
        ],
      }))

      expect(screen.getByRole("alert")).toHaveTextContent(/Age_Vehicle_Banding/)
    })

    it("does not rewrite a falsey non-string Banding selector to the sole input", () => {
      const made = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "ratebook",
          data_input: "Data_Input",
          objective: "premium",
          constraints: {},
          banding_source: 0,
        },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "banding_1", target: "opt_1" },
        ],
      })

      renderConfig(made)

      expect(screen.getByRole("alert")).toHaveTextContent(
        "The configured Rating Factor Source must be an input name.",
      )
      expect(made.componentProps.onUpdate).not.toHaveBeenCalledWith(
        "banding_source",
        "Banding",
      )
      showPane("solve")
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
      expect(screen.getByRole("alert")).toHaveTextContent("Select a connected Rating Factor Source.")
    })

    it("warns for zero-level outputs while keeping healthy factor controls", () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: { healthy_band: ["Yes"] }, configuredOutputs: ["healthy_band", "empty_band"],
        zeroLevelOutputs: ["empty_band"], zeroLevelIssues: [{ outputColumn: "empty_band" }],
      })
      renderConfig(makeProps({
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {}, banding_source: "Banding" },
        allNodes: [
          { id: "banding_1", data: { label: "Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [{ id: "e1", source: "banding_1", target: "opt_1" }],
      }))
      expect(screen.getByRole("alert")).toHaveTextContent(/empty_band/)
      expect(screen.getByText("healthy_band")).toBeInTheDocument()
    })

    it("shows banding source selector when banding nodes are connected", () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: { age: ["1", "2", "3"], region: ["A", "B"] },
        configuredOutputs: ["age", "region"],
        zeroLevelOutputs: [],
        zeroLevelIssues: [],
      })

      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} },
            allNodes: [
              { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
              { id: "banding_1", data: { label: "My Banding", description: "", nodeType: "banding", config: {} } },
            ],
            edges: [
              { id: "e1", source: "input_1", target: "opt_1" },
              { id: "e2", source: "banding_1", target: "opt_1" },
            ],
          }))
      // The exact executable name appears in the select option; use getAllByText since
      // banding factor buttons may also render the label
      expect(screen.getAllByText("My_Banding").length).toBeGreaterThanOrEqual(1)
    })

    it("auto-selects all banding factors for a loaded ratebook config with no factor_columns key", async () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: {
          channel_band: ["direct", "broker"],
          proposer_age_band: ["20-27"],
          vehicle_age_band: ["1-3"],
        },
        configuredOutputs: ["channel_band", "proposer_age_band", "vehicle_age_band"],
        zeroLevelOutputs: [],
        zeroLevelIssues: [],
      })
      const onUpdate = vi.fn()

      renderStatefulConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "ratebook",
          objective: "premium",
          constraints: {},
          data_input: "Data_Input",
          banding_source: "Age_Vehicle_Banding",
        },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Age Vehicle Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "banding_1", target: "opt_1" },
        ],
      }), onUpdate)

      await waitFor(() => {
        expect(onUpdate).toHaveBeenCalledWith("factor_columns", [
          ["channel_band"],
          ["proposer_age_band"],
          ["vehicle_age_band"],
        ])
      })
      expect(await screen.findByText("Rating Factors (3 selected)")).toBeInTheDocument()
      showPane("solve")
      expect(screen.getByRole("button", { name: /Optimise/ })).not.toBeDisabled()
      expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })

    it("changes banding source and derived factor columns in one atomic update", async () => {
      vi.mocked(classifyBandingNode).mockImplementation((node) => {
        const levels: Record<string, string[]> = node?.id === "banding_2"
          ? { new_factor: ["A", "B"] }
          : { old_factor: ["X", "Y"] }
        return {
          levels,
          configuredOutputs: node?.id === "banding_2" ? ["new_factor"] : ["old_factor"],
          zeroLevelOutputs: [],
          zeroLevelIssues: [],
        }
      })
      const onUpdate = vi.fn()

      renderStatefulConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "ratebook",
          objective: "premium",
          constraints: {},
          banding_source: "Old_Banding",
          factor_columns: [["old_factor"]],
        },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Old Banding", description: "", nodeType: "banding", config: {} } },
          { id: "banding_2", data: { label: "New Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "banding_1", target: "opt_1" },
          { id: "e3", source: "banding_2", target: "opt_1" },
        ],
      }), onUpdate)

      fireEvent.change(screen.getByRole("combobox", { name: "Rating Factor Source" }), {
        target: { value: "New_Banding" },
      })

      await waitFor(() => {
        expect(screen.getByText("new_factor")).toBeInTheDocument()
      })
      expect(onUpdate).toHaveBeenCalledTimes(1)
      expect(onUpdate).toHaveBeenCalledWith({
        banding_source: "New_Banding",
        factor_columns: [["new_factor"]],
      })
    })

    it("leaves an explicitly empty factor_columns list disabled", () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: {
          channel_band: ["direct", "broker"],
          proposer_age_band: ["20-27"],
        },
        configuredOutputs: ["channel_band", "proposer_age_band"],
        zeroLevelOutputs: [],
        zeroLevelIssues: [],
      })

      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "ratebook",
          objective: "premium",
          constraints: {},
          banding_source: "Age_Vehicle_Banding",
          factor_columns: [],
        },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Age Vehicle Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "banding_1", target: "opt_1" },
        ],
      }))

      expect(screen.getByText("Rating Factors (0 selected)")).toBeInTheDocument()
      showPane("solve")
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
      expect(screen.getByRole("alert")).toHaveTextContent("Select at least one rating factor.")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Panes
  // ═══════════════════════════════════════════════════════════════════

  describe("Panes", () => {
    it("renders only the selected pane's controls", () => {
      renderConfig(makeProps({
        activePane: "data",
        config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1 } } },
      }))
      expect(screen.getByRole("tabpanel")).toHaveAttribute("id", "optimiser-data-pane")
      expect(screen.getByText("Column to maximise")).toBeInTheDocument()
      expect(screen.queryByText(/Constraints \(1\)/)).not.toBeInTheDocument()
      expect(screen.queryByRole("button", { name: /Optimise/ })).not.toBeInTheDocument()
      expect(screen.queryByText("Solver settings")).not.toBeInTheDocument()
      expect(screen.queryByRole("radiogroup", { name: "MLflow destination" })).not.toBeInTheDocument()
    })

    it("shows Data for a Factors selection in online mode", () => {
      renderConfig(makeProps({ activePane: "factors" }))
      expect(screen.getByRole("tabpanel")).toHaveAttribute("id", "optimiser-data-pane")
      expect(screen.queryByText("Rating Factor Source")).not.toBeInTheDocument()
    })

    it("lists every solve-blocking issue and links each to its pane", () => {
      renderConfig(makeProps({
        activePane: "solve",
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "", constraints: {} },
        edges: [],
      }))
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
      const alert = screen.getByRole("alert")
      expect(alert).toHaveTextContent("Complete before optimising")
      expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
        "Connect an input that provides the objectives and constraints.Go to Data",
        "Choose the objective column to maximise.Go to Data",
        "Connect a Banding node to define the rating factors.Go to Factors",
      ])

      fireEvent.click(within(alert).getAllByRole("button", { name: "Go to Factors" })[0])
      expect(useUIStore.getState().optimiserPanes.opt_1).toBe("factors")
      expect(screen.getByRole("tabpanel")).toHaveAttribute("id", "optimiser-factors-pane")
      expect(screen.getByText(/No Banding nodes found/)).toBeInTheDocument()
    })

    it("persists inferred ratebook factors while another pane is open", async () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: { channel_band: ["direct", "broker"] },
        configuredOutputs: ["channel_band"],
        zeroLevelOutputs: [],
        zeroLevelIssues: [],
      })
      const onUpdate = vi.fn()
      renderStatefulConfig(makeProps({
        activePane: "constraints",
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {}, data_input: "Data_Input" },
        allNodes: [
          { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataInput", config: {} } },
          { id: "banding_1", data: { label: "Banding", description: "", nodeType: "banding", config: {} } },
        ],
        edges: [
          { id: "e1", source: "input_1", target: "opt_1" },
          { id: "e2", source: "banding_1", target: "opt_1" },
        ],
      }), onUpdate)

      await waitFor(() => {
        expect(onUpdate).toHaveBeenCalledWith({
          banding_source: "Banding",
          factor_columns: [["channel_band"]],
        })
      })
      showPane("solve")
      expect(screen.getByRole("button", { name: /Optimise/ })).not.toBeDisabled()
    })
  })

  describe("Solve readiness", () => {
    withPane("solve")

    it("blocks on a mapped column the input does not have, naming the default in use", () => {
      mockUseDataInputColumns.mockReturnValue([
        { name: "premium", dtype: "Float64" },
        { name: "scenario_index", dtype: "Int64" },
        { name: "scenario_value", dtype: "Float64" },
      ])
      renderConfig(makeProps({ upstreamColumns: [] }))
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
      expect(screen.getByRole("alert")).toHaveTextContent('Quote ID uses "quote_id", which the input does not have.')
    })

    it("blocks an efficient frontier with an incomplete or inverted range, or too many solves", () => {
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1 }, volume: { min: 10 } },
          frontier_enabled: true,
          frontier_steps: 101,
          frontier_ranges: { loss_ratio: { min: 2, max: 1 } },
        },
      }))
      const alert = screen.getByRole("alert")
      expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
        "The frontier range for loss_ratio needs a minimum below its maximum.Go to Constraints",
        "Set both ends of the frontier range for volume.Go to Constraints",
        "The frontier would run 10,201 solves; the limit is 10,000. Reduce the steps per constraint.Go to Constraints",
      ])
    })

    it("keeps Auto range available while the frontier ranges are still missing", () => {
      renderConfig(makeProps({
        activePane: "constraints",
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1 } },
          frontier_enabled: true,
        },
      }))
      expect(screen.getByRole("button", { name: "Auto range" })).toBeEnabled()
    })

    it("warns without blocking when each quote has one scenario", async () => {
      mockEstimateOptimiserSolve.mockResolvedValue({
        total_rows: 10,
        quote_count: 10,
        scenarios_per_quote_min: 1,
        scenarios_per_quote_max: 1,
        scenarios_per_quote_mean: 1,
        expanded_row_count: 10,
      })
      renderConfig(makeProps())
      expect(await screen.findByRole("list", { name: "Input warnings" })).toHaveTextContent(
        "Each quote has one scenario",
      )
      expect(screen.getByRole("button", { name: /Optimise/ })).toBeEnabled()
    })

    it("says the size is unknown when the server cannot count it", async () => {
      mockEstimateOptimiserSolve.mockResolvedValue({
        total_rows: null,
        quote_count: null,
        scenarios_per_quote_min: null,
        scenarios_per_quote_max: null,
        scenarios_per_quote_mean: null,
        expanded_row_count: null,
      })
      renderConfig(makeProps())
      expect(await screen.findByText(/Size unknown/)).toBeInTheDocument()
    })

    it("does not re-request the size estimate for constraint or export edits", async () => {
      mockEstimateOptimiserSolve.mockResolvedValue({
        total_rows: 10, quote_count: 10, scenarios_per_quote_min: 2, scenarios_per_quote_max: 2,
        scenarios_per_quote_mean: 2, expanded_row_count: 20,
      })
      const made = makeProps()
      const view = renderConfig(made)
      await waitFor(() => expect(mockEstimateOptimiserSolve).toHaveBeenCalledTimes(1))
      const rerenderWith = (config: Record<string, unknown>) => view.rerender(
        <GraphProvider allNodes={made.graph.allNodes} edges={made.graph.edges}>
          <PaneBoundConfig {...made.componentProps} config={config} />
        </GraphProvider>,
      )
      rerenderWith({ ...made.componentProps.config, constraints: { volume: { min: 5 } }, mlflow_experiment: "x", result_export_path: "out.json" })
      await act(async () => {})
      expect(mockEstimateOptimiserSolve).toHaveBeenCalledTimes(1)
      rerenderWith({ ...made.componentProps.config, scenario_value: "premium" })
      await waitFor(() => expect(mockEstimateOptimiserSolve).toHaveBeenCalledTimes(2))
    })

    it("does not mark the solve stale for export edits", () => {
      const config = { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {} }
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: makeSolveResult(),
            originalResult: makeSolveResult(),
            jobId: "job_1",
            configHash: hashConfig(config),
            source: "live",
            structuralVersion: useGraphStore.getState().structuralVersion,
            constraints: {},
            nodeLabel: "Opt",
            frontier: null,
            selectedPointIndex: null,
          },
        },
      })
      renderConfig(makeProps({ config: { ...config, mlflow_destination: "databricks", result_export_path: "out.json" } }))
      expect(screen.queryByText("Config changed since last solve")).not.toBeInTheDocument()
    })

    it("stops a running solve through the cancel route and records the outcome", async () => {
      mockCancelOptimiserSolve.mockResolvedValue({
        status: "cancelled",
        progress: 0.4,
        message: "Cancelled by user",
        elapsed_seconds: 3,
        result: null,
        frontier: null,
        terminal_reason: "cancelled",
        execution_metrics: null,
      })
      useNodeResultsStore.getState().startSolveJob("opt_1", "job_run", "Opt", {}, "hash", "live", 0)
      renderConfig(makeProps())
      fireEvent.click(screen.getByRole("button", { name: "Stop" }))
      await waitFor(() => expect(mockCancelOptimiserSolve).toHaveBeenCalledWith("job_run"))
      await waitFor(() => expect(useNodeResultsStore.getState().solveJobs.opt_1).toBeUndefined())
      expect(useNodeResultsStore.getState().solveResults.opt_1?.error).toBe("Cancelled by user")
    })

    it("Ctrl+Enter in an edited field solves the committed value, not the previous one", async () => {
      mockSolveOptimiser.mockResolvedValue({ status: "started", job_id: "job_key" })
      const config = {
        _nodeId: "opt_1",
        mode: "online",
        objective: "premium",
        constraints: { loss_ratio: { max: 1 } },
        frontier_enabled: true,
        frontier_ranges: { loss_ratio: { min: 1, max: 5 } },
      }
      renderStatefulConfig(makeProps({ activePane: "constraints", config }))
      const min = screen.getByLabelText("loss_ratio min value")
      fireEvent.change(min, { target: { value: "2" } })
      fireEvent.keyDown(min, { key: "Enter", ctrlKey: true })

      await waitFor(() => expect(useNodeResultsStore.getState().solveJobs.opt_1?.jobId).toBe("job_key"))
      expect(useNodeResultsStore.getState().solveJobs.opt_1?.configHash).toBe(solveConfigHash({
        ...config,
        frontier_ranges: { loss_ratio: { min: 2, max: 5 } },
      }))
    })

    it("Ctrl+Enter judges readiness after the commit, so a now-invalid edit does not solve", async () => {
      const config = {
        _nodeId: "opt_1",
        mode: "online",
        objective: "premium",
        constraints: { loss_ratio: { max: 1 } },
        frontier_enabled: true,
        frontier_ranges: { loss_ratio: { min: 1, max: 5 } },
      }
      renderStatefulConfig(makeProps({ activePane: "constraints", config }))
      const min = screen.getByLabelText("loss_ratio min value")
      fireEvent.change(min, { target: { value: "9" } })
      fireEvent.keyDown(min, { key: "Enter", ctrlKey: true })
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
      expect(mockSolveOptimiser).not.toHaveBeenCalled()
    })

    it("ignores a Stop reply that arrives after the stopped job gave way to a new one", async () => {
      let replyToStop: (value: unknown) => void = () => {}
      mockCancelOptimiserSolve.mockReturnValue(new Promise((resolve) => { replyToStop = resolve }))
      const store = useNodeResultsStore.getState()
      store.startSolveJob("opt_1", "job_a", "Opt", {}, "hash_a", "live", 0)
      renderConfig(makeProps())
      fireEvent.click(screen.getByRole("button", { name: "Stop" }))
      await waitFor(() => expect(mockCancelOptimiserSolve).toHaveBeenCalledWith("job_a"))

      // Polling finishes A and the user starts B before the Stop reply lands.
      act(() => {
        useNodeResultsStore.getState().completeSolveJob("opt_1", makeSolveResult())
        useNodeResultsStore.getState().startSolveJob("opt_1", "job_b", "Opt", {}, "hash_b", "live", 0)
      })
      await act(async () => {
        replyToStop({
          status: "cancelled", progress: 1, message: "Cancelled", elapsed_seconds: 1,
          result: null, frontier: null, terminal_reason: "cancelled", execution_metrics: null,
        })
      })
      expect(useNodeResultsStore.getState().solveJobs.opt_1?.jobId).toBe("job_b")
      expect(useNodeResultsStore.getState().solveResults.opt_1?.error).toBeUndefined()
    })

    it("does not re-request the size estimate when the node's own edit moves the structural version", async () => {
      mockEstimateOptimiserSolve.mockResolvedValue({
        total_rows: 10, quote_count: 10, scenarios_per_quote_min: 2, scenarios_per_quote_max: 2,
        scenarios_per_quote_mean: 2, expanded_row_count: 20,
      })
      const made = makeProps()
      const optimiserNode = (config: Record<string, unknown>): SimpleNode => ({
        id: "opt_1", data: { label: "Opt", description: "", nodeType: "optimiser", config },
      })
      const graphNodes = (config: Record<string, unknown>) => [...made.graph.allNodes, optimiserNode(config)]
      const view = render(
        <GraphProvider allNodes={graphNodes(made.componentProps.config)} edges={made.graph.edges}>
          <PaneBoundConfig {...made.componentProps} />
        </GraphProvider>,
      )
      await waitFor(() => expect(mockEstimateOptimiserSolve).toHaveBeenCalledTimes(1))

      // A real edit: the node's config, the graph and the structural version all move.
      const edited = { ...made.componentProps.config, constraints: { volume: { min: 5 } } }
      act(() => {
        useGraphStore.setState({ structuralVersion: useGraphStore.getState().structuralVersion + 1 })
      })
      view.rerender(
        <GraphProvider allNodes={graphNodes(edited)} edges={made.graph.edges}>
          <PaneBoundConfig {...made.componentProps} config={edited} />
        </GraphProvider>,
      )
      await act(async () => {})
      expect(mockEstimateOptimiserSolve).toHaveBeenCalledTimes(1)

      // An upstream change does re-estimate.
      const upstreamChanged = made.graph.allNodes.map((node, index) => (
        index === 0 ? { ...node, data: { ...node.data, description: "changed" } } : node
      ))
      view.rerender(
        <GraphProvider allNodes={[...upstreamChanged, optimiserNode(edited)]} edges={made.graph.edges}>
          <PaneBoundConfig {...made.componentProps} config={edited} />
        </GraphProvider>,
      )
      await waitFor(() => expect(mockEstimateOptimiserSolve).toHaveBeenCalledTimes(2))
    })

    it("starts the solve with Ctrl+Enter from any pane when it can", async () => {
      mockSolveOptimiser.mockResolvedValue({ status: "started", job_id: "job_key" })
      renderConfig(makeProps({ activePane: "data" }))
      fireEvent.keyDown(screen.getByRole("tabpanel"), { key: "Enter", ctrlKey: true })
      await waitFor(() => expect(mockSolveOptimiser).toHaveBeenCalledTimes(1))
    })
  })

  describe("Pane issue report", () => {
    it("reports the panes holding blocking issues for the tab indicators", () => {
      const onPaneIssuesChange = vi.fn()
      renderConfig(makeProps({
        onPaneIssuesChange,
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "", constraints: {} },
      }))
      expect(onPaneIssuesChange).toHaveBeenLastCalledWith("opt_1", ["data", "factors"])
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Column Mappings
  // ═══════════════════════════════════════════════════════════════════

  describe("Column Mappings", () => {
    withPane("data")

    it("renders Quote ID, Scenario Index, Scenario Value selectors", () => {
      renderConfig(makeProps())
      expect(screen.getByText("Quote ID")).toBeInTheDocument()
      expect(screen.getByText("Scenario Index")).toBeInTheDocument()
      expect(screen.getByText("Scenario Value")).toBeInTheDocument()
    })

    it("column mapping change calls onUpdate with correct key", () => {
      const props = makeProps()
      renderConfig(props)
      // Find all selects — look for the one with "Select quote id..." placeholder
      const selects = screen.getAllByRole("combobox")
      const quoteIdSelect = selects.find(s =>
        Array.from(s.querySelectorAll("option")).some(o => o.textContent === "Select quote id..."),
      )!
      fireEvent.change(quoteIdSelect, { target: { value: "premium" } })
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("quote_id", "premium")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Constraints
  // ═══════════════════════════════════════════════════════════════════

  describe("Constraints", () => {
    withPane("constraints")

    it("shows Constraints (0) with Add button when no constraints", () => {
      renderConfig(makeProps())
      expect(screen.getByText(/Constraints \(0\)/)).toBeInTheDocument()
      expect(screen.getByText("Add")).toBeInTheDocument()
      expect(screen.queryByTestId("constraint-card")).not.toBeInTheDocument()
    })

    it("does not render empty-state guidance when there are no constraints", () => {
      renderConfig(makeProps())
      expect(screen.queryByText(/No constraints added/)).not.toBeInTheDocument()
      expect(screen.queryByText(/portfolio total bound/)).not.toBeInTheDocument()
      expect(screen.queryByTestId("constraint-row")).not.toBeInTheDocument()
    })

    it("clicking Add calls handleAddConstraint", () => {
      renderConfig(makeProps())
      fireEvent.click(screen.getByText("Add"))
      expect(mockHandleAddConstraint).toHaveBeenCalledTimes(1)
    })

    it("renders constraint rows when constraints exist", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      expect(screen.getByText(/Constraints \(1\)/)).toBeInTheDocument()
      const settingsCard = screen.getByTestId("constraint-card")
      expect(within(settingsCard).getByTestId("constraint-row")).toBeInTheDocument()
      expect(within(settingsCard).getByRole("combobox", {
        name: "loss_ratio constraint column",
      })).toHaveValue("loss_ratio")
      expect(within(settingsCard).getByRole("button", {
        name: "Remove loss_ratio constraint",
      })).toBeInTheDocument()
      // Should not show "No constraints added"
      expect(screen.queryByText(/No constraints added/)).not.toBeInTheDocument()
    })

    it("constraint type dropdown only offers absolute sum-constraint keys", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      expect(screen.getByText("Minimum")).toBeInTheDocument()
      expect(screen.getByText("Maximum")).toBeInTheDocument()
      expect(screen.queryByText("Min %")).not.toBeInTheDocument()
      expect(screen.queryByText("Max %")).not.toBeInTheDocument()
      expect(screen.queryByDisplayValue("min_abs")).not.toBeInTheDocument()
      expect(screen.queryByDisplayValue("max_abs")).not.toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solver Tuning
  // ═══════════════════════════════════════════════════════════════════

  describe("Solver Tuning", () => {
    withPane("solve")

    it("max iterations input renders with default 50", () => {
      renderConfig(makeProps())
      const input = screen.getByDisplayValue("50")
      expect(input).toBeInTheDocument()
    })

    it("tolerance input renders with default value", () => {
      renderConfig(makeProps())
      const input = screen.getByDisplayValue("0.000001")
      expect(input).toBeInTheDocument()
    })

    it("changing max_iter calls onUpdate", () => {
      const props = makeProps()
      renderConfig(props)
      const input = screen.getByDisplayValue("50")
      fireEvent.change(input, { target: { value: "100" } })
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
      fireEvent.blur(input)
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("max_iter", 100)
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solver settings
  // ═══════════════════════════════════════════════════════════════════

  describe("Solver settings", () => {
    withPane("solve")

    it("shows every solver setting without a disclosure", () => {
      renderConfig(makeProps())
      expect(screen.getByRole("heading", { name: "Solver settings" })).toBeInTheDocument()
      expect(screen.queryByText("Advanced")).not.toBeInTheDocument()
      expect(screen.getByText("Chunk size")).toBeInTheDocument()
    })

    it("offers no history toggle: every solve records its history", () => {
      renderConfig(makeProps())
      expect(screen.getByDisplayValue("500000")).toBeInTheDocument()
      expect(screen.queryByText("Record history")).not.toBeInTheDocument()
      expect(screen.queryByRole("button", { name: /^(On|Off)$/ })).not.toBeInTheDocument()
    })

    it("ratebook mode shows CD iterations and CD tolerance", () => {
      renderConfig(makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } }))
      expect(screen.getByText("CD iterations")).toBeInTheDocument()
      expect(screen.getByText("CD tolerance")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solve action
  // ═══════════════════════════════════════════════════════════════════

  describe("Solve action", () => {
    withPane("solve")

    it("solve button is disabled when no objective is set", () => {
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "", constraints: {} },
          }))
      const btn = screen.getByRole("button", { name: /Optimise/ })
      expect(btn).toBeDisabled()
    })

    it("solve button is enabled when objective and constraints are set", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      const btn = screen.getByRole("button", { name: /Optimise/ })
      expect(btn).not.toBeDisabled()
    })

    it("solve button calls solveOptimiser with graph payload", async () => {
      mockSolveOptimiser.mockResolvedValue({ status: "started", job_id: "job_42", error: null })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      })
      renderConfig(props)
      fireEvent.click(screen.getByRole("button", { name: /Optimise/ }))
      await waitFor(() => {
        expect(mockSolveOptimiser).toHaveBeenCalledTimes(1)
      })
      // Verify it was called with a graph payload containing node_id
      expect(mockSolveOptimiser).toHaveBeenCalledWith(
        expect.objectContaining({ node_id: "opt_1" }),
      )
    })

    it("preserves structured execution metrics when solve admission fails", async () => {
      const executionMetrics = makeExecutionMetricsFixture({
        profile: "optimiser_setup",
        status: "memory_limited",
        terminal_reason: "memory_limited",
      })
      mockSolveOptimiser.mockRejectedValue(Object.assign(new Error("HTTP 507"), {
        name: "ApiError",
        status: 507,
        detail: JSON.stringify({
          message: "Optimiser rejected by admission control",
          terminal_reason: "memory_limited",
          execution_metrics: executionMetrics,
        }),
        rawDetail: {
          message: "Optimiser rejected by admission control",
          terminal_reason: "memory_limited",
          execution_metrics: executionMetrics,
        },
      }))
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      }))

      fireEvent.click(screen.getByRole("button", { name: /Optimise/ }))

      await waitFor(() => {
        const cached = useNodeResultsStore.getState().solveResults.opt_1
        expect(cached?.error).toBe("Optimiser rejected by admission control")
        expect(cached?.terminalStatus?.status).toBe("memory_limited")
        expect(cached?.terminalStatus?.terminal_reason).toBe("memory_limited")
        expect(cached?.terminalStatus?.execution_metrics).toBe(executionMetrics)
      })
    })

    it("stores immediate solve error responses even before a background job exists", async () => {
      mockSolveOptimiser.mockResolvedValue({
        status: "error",
        job_id: null,
        error: "Objective column is required",
      })
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      }))

      fireEvent.click(screen.getByRole("button", { name: /Optimise/ }))

      await waitFor(() => {
        const cached = useNodeResultsStore.getState().solveResults.opt_1
        expect(cached?.error).toBe("Objective column is required")
        expect(cached?.jobId).toBe("startup-failure:opt_1")
      })
    })

    it("shows 'Executing pipeline...' during active solve job before progress arrives", () => {
      useNodeResultsStore.setState({
        solveJobs: {
          opt_1: {
            jobId: "job_42",
            nodeId: "opt_1",
            nodeLabel: "Optimiser",
            progress: null,
            error: null,
            constraints: {},
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      expect(screen.getByText("Executing pipeline...")).toBeInTheDocument()
      // The Optimise button should not be visible while solving
      expect(screen.queryByRole("button", { name: /Optimise/ })).not.toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Constraint interactions
  // ═══════════════════════════════════════════════════════════════════

  describe("Constraint interactions", () => {
    withPane("constraints")

    it("clicking remove button on a constraint calls handleRemoveConstraint with the name", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      const removeButtons = document.querySelectorAll(".lucide-x")
      expect(removeButtons.length).toBeGreaterThanOrEqual(1)
      fireEvent.click(removeButtons[0].closest("button")!)
      expect(mockHandleRemoveConstraint).toHaveBeenCalledWith("loss_ratio")
    })

    it("changing the constraint column dropdown calls handleConstraintColumnChange", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      const constraintSelects = document.querySelectorAll("select")
      const columnSelect = Array.from(constraintSelects).find(s =>
        (s as HTMLSelectElement).value === "loss_ratio" &&
        (s as HTMLSelectElement).classList.contains("font-mono") &&
        Array.from(s.querySelectorAll("option")).length > 1,
      )!
      fireEvent.change(columnSelect, { target: { value: "volume" } })
      expect(mockHandleConstraintColumnChange).toHaveBeenCalledWith("loss_ratio", "volume")
    })

    it("changing the constraint value input calls handleConstraintValueChange", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      const valueInput = screen.getByRole("spinbutton", {
        name: "loss_ratio constraint value",
      })
      fireEvent.change(valueInput, { target: { value: "0.95" } })
      expect(mockHandleConstraintValueChange).not.toHaveBeenCalled()
      fireEvent.blur(valueInput)
      expect(mockHandleConstraintValueChange).toHaveBeenCalledWith("loss_ratio", "max", 0.95)
    })

    it("clearing a bound restores its value instead of relaxing the constraint to 0", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      const valueInput = screen.getByRole("spinbutton", {
        name: "loss_ratio constraint value",
      })
      fireEvent.change(valueInput, { target: { value: "" } })
      fireEvent.blur(valueInput)
      expect(mockHandleConstraintValueChange).not.toHaveBeenCalled()
      expect(valueInput).toHaveValue(1.05)
    })

    it("changing the constraint type dropdown calls handleConstraintValueChange", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      const typeSelect = screen.getByRole("combobox", {
        name: "loss_ratio constraint bound type",
      })
      fireEvent.change(typeSelect, { target: { value: "min" } })
      expect(mockHandleConstraintValueChange).toHaveBeenCalledWith("loss_ratio", "min", 1.05)
    })

    it("shows multiple constraints with correct count", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 }, volume: { min: 0.9 } },
            },
          }))
      expect(screen.getByText(/Constraints \(2\)/)).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solver Tuning extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Solver Tuning extended", () => {
    withPane("solve")

    it("changing tolerance calls onUpdate with tolerance key", () => {
      const props = makeProps()
      renderConfig(props)
      const input = screen.getByDisplayValue("0.000001")
      fireEvent.change(input, { target: { value: "0.001" } })
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
      fireEvent.blur(input)
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("tolerance", 0.001)
    })

    it("renders custom max_iter from config", () => {
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {}, max_iter: 200 },
          }))
      expect(screen.getByDisplayValue("200")).toBeInTheDocument()
    })

    it("renders custom tolerance from config", () => {
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {}, tolerance: 0.01 },
          }))
      expect(screen.getByDisplayValue("0.01")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solver settings extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Solver settings extended", () => {
    withPane("solve")

    it("changing chunk_size calls onUpdate", () => {
      const props = makeProps()
      renderConfig(props)
      const input = screen.getByDisplayValue("500000")
      fireEvent.change(input, { target: { value: "100000" } })
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
      fireEvent.blur(input)
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("chunk_size", 100000)
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Mode toggle extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Mode toggle extended", () => {
    withPane("data")

    it("clicking online mode from ratebook calls onUpdate with mode online", () => {
      const props = makeProps({
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} },
      })
      renderConfig(props)
      fireEvent.click(screen.getByRole("button", { name: "Online" }))
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("mode", "online")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solve action extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Solve action extended", () => {
    withPane("solve")

    it("solve button is enabled when valid config has objective set", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: {},
            },
          }))
      const btn = screen.getByRole("button", { name: /Optimise/ })
      expect(btn).not.toBeDisabled()
    })

    it("ratebook mode solve button is disabled when no factor columns selected", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "ratebook",
              objective: "premium",
              constraints: {},
              factor_columns: [],
            },
          }))
      const btn = screen.getByRole("button", { name: /Optimise/ })
      expect(btn).toBeDisabled()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Staleness extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Staleness extended", () => {
    withPane("solve")

    it("does not show staleness indicator when config hash matches", () => {
      const cfg = { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {} }
      const matchingHash = hashConfig(cfg as Record<string, unknown>)
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: makeSolveResult({
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            }),
            jobId: "job_42",
            configHash: matchingHash,
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Optimiser",
            originalResult: makeSolveResult({
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            }),
            frontier: null,
            selectedPointIndex: null,
          },
        },
      })
      renderConfig(makeProps({ config: cfg }))
      expect(screen.queryByText("Config changed since last solve")).not.toBeInTheDocument()
    })

    it("Re-run button calls solveOptimiser", async () => {
      mockSolveOptimiser.mockResolvedValue({ status: "started", job_id: "job_99", error: null })
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: makeSolveResult({
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            }),
            jobId: "job_42",
            configHash: "definitely_stale_hash",
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Optimiser",
            originalResult: makeSolveResult({
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            }),
            frontier: null,
            selectedPointIndex: null,
          },
        },
      })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      fireEvent.click(screen.getByRole("button", { name: "Re-run" }))
      await waitFor(() => {
        expect(mockSolveOptimiser).toHaveBeenCalledTimes(1)
      })
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Results display
  // ═══════════════════════════════════════════════════════════════════

  describe("Results display", () => {
    withPane("solve")

    const convergedResult = {
      result: makeSolveResult({
        total_objective: 1000,
        baseline_objective: 900,
        constraints: { loss_ratio: 0.65 },
        baseline_constraints: { loss_ratio: 0.6 },
        lambdas: { loss_ratio: 0.005 },
        converged: true,
        iterations: 15,
        n_quotes: 5000,
        n_steps: 3,
      }),
      jobId: "job_42",
      configHash: "",
      constraints: { loss_ratio: { max: 1.05 } },
      nodeLabel: "Optimiser",
      originalResult: makeSolveResult({
        total_objective: 1000,
        baseline_objective: 900,
        constraints: { loss_ratio: 0.65 },
        baseline_constraints: { loss_ratio: 0.6 },
        lambdas: { loss_ratio: 0.005 },
        converged: true,
        iterations: 15,
        n_quotes: 5000,
        n_steps: 3,
      }),
      frontier: null,
      selectedPointIndex: null,
      source: "live",
      structuralVersion: 0,
    }

    it("shows convergence status when solveResult exists", () => {
      // Set configHash to empty to match the result's configHash
      useNodeResultsStore.setState({ solveResults: { opt_1: convergedResult } })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText(/Converged/)).toBeInTheDocument()
      expect(screen.getByText(/15 iterations/)).toBeInTheDocument()
    })

    it("shows solver iterations instead of unknown CD iterations when ratebook CD count is absent", () => {
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            ...convergedResult,
            result: makeSolveResult({
              ...convergedResult.result,
              mode: "ratebook",
              iterations: 11,
              cd_iterations: null,
            }),
            originalResult: makeSolveResult({
              ...convergedResult.originalResult,
              mode: "ratebook",
              iterations: 11,
              cd_iterations: null,
            }),
          },
        },
      })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))

      expect(screen.getByText(/Converged in 11 iterations/)).toBeInTheDocument()
      expect(screen.queryByText(/\? CD iterations/)).not.toBeInTheDocument()
    })

    it("renders scenario-expanded input size separately from raw source rows", async () => {
      mockEstimateOptimiserSolve.mockResolvedValueOnce({
        total_rows: 10000000,
        quote_count: 100000,
        scenarios_per_quote_min: 20,
        scenarios_per_quote_max: 21,
        expanded_row_count: 2050000,
      })

      renderConfig(makeProps({
        config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
      }))

      expect(await screen.findByText("Quotes")).toBeInTheDocument()
      expect(screen.getByText("100,000")).toBeInTheDocument()
      expect(screen.getByText("Scenarios / quote")).toBeInTheDocument()
      expect(screen.getByText("20-21")).toBeInTheDocument()
      expect(screen.getByText("Total rows")).toBeInTheDocument()
      expect(screen.getByText("2,050,000")).toBeInTheDocument()
      expect(screen.queryByText("Source rows")).not.toBeInTheDocument()
      expect(screen.queryByText("10,000,000")).not.toBeInTheDocument()
    })

    it("shows non-convergence warning when the solve did not converge", () => {
      const notConverged = { ...convergedResult.originalResult, converged: false }
      const nonConverged = {
        ...convergedResult,
        result: notConverged,
        originalResult: notConverged,
      }
      useNodeResultsStore.setState({ solveResults: { opt_1: nonConverged } })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText(/Solver did not converge/)).toBeInTheDocument()
      expect(screen.getByText(/Did not converge/)).toBeInTheDocument()
    })

    it("describes the solve, not the frontier point the preview selected", () => {
      // A frontier solve opens on point 1, whose own bisection converged; the
      // solve itself ran out of iterations short of its bound.
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            ...convergedResult,
            result: makeSolveResult({ ...convergedResult.result, converged: true, iterations: 33 }),
            originalResult: makeSolveResult({
              ...convergedResult.originalResult,
              converged: false,
              iterations: 20,
              warning: "Solver did not converge. Consider increasing max_iter or relaxing tolerance.",
            }),
            selectedPointIndex: 0,
          },
        },
      })
      renderConfig(makeProps({
        config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
      }))

      expect(screen.getByText("Solver did not converge")).toBeInTheDocument()
      expect(screen.getByText("Solver did not converge. Consider increasing max_iter or relaxing tolerance."))
        .toBeInTheDocument()
      expect(screen.getByText(/Did not converge in 20 iterations/)).toBeInTheDocument()
      expect(screen.queryByText(/Converged in 33 iterations/)).not.toBeInTheDocument()
    })

    it("shows error when solveError exists in job", () => {
      useNodeResultsStore.setState({
        solveJobs: {
          opt_1: {
            jobId: "job_42",
            nodeId: "opt_1",
            nodeLabel: "Optimiser",
            progress: null,
            error: "Solver exploded",
            constraints: {},
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText("Optimisation failed")).toBeInTheDocument()
      expect(screen.getByText("Solver exploded")).toBeInTheDocument()
    })

    it("shows cached background failure after polling removes the active job", () => {
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            ...convergedResult,
            result: { ...convergedResult.result, converged: false },
            error: "Data error: Ratebook factor columns contain null values",
          },
        },
      })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText("Optimisation failed")).toBeInTheDocument()
      expect(screen.getByText("Data error: Ratebook factor columns contain null values")).toBeInTheDocument()
      expect(screen.queryByText(/Did not converge/)).not.toBeInTheDocument()
    })

  })

  // ═══════════════════════════════════════════════════════════════════
  // Progress
  // ═══════════════════════════════════════════════════════════════════

  describe("Progress", () => {
    withPane("solve")

    it("shows progress bar when solveProgress exists", () => {
      useNodeResultsStore.setState({
        solveJobs: {
          opt_1: {
            jobId: "job_42",
            nodeId: "opt_1",
            nodeLabel: "Optimiser",
            progress: {
              status: "running",
              progress: 0.45,
              message: "Iteration 9 of 20",
              elapsed_seconds: 12,
            },
            error: null,
            constraints: {},
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText("Iteration 9 of 20")).toBeInTheDocument()
      expect(screen.getByText("12 s")).toBeInTheDocument()
    })

    it("shows structured memory-pressure diagnostics during solve progress", () => {
      useNodeResultsStore.setState({
        solveJobs: {
          opt_1: {
            jobId: "job_42",
            nodeId: "opt_1",
            nodeLabel: "Optimiser",
            progress: {
              status: "running",
              progress: 0.45,
              message: "Building solve grid",
              elapsed_seconds: 12,
              execution_metrics: makeExecutionMetricsFixture({ profile: "optimiser_setup" }),
            },
            error: null,
            constraints: {},
            configHash: "abc",
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig(makeProps({
        config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
      }))

      expect(screen.getByText("Optimiser reached 75% of its memory allowance.")).toBeInTheDocument()
      expect(screen.getByText("Memory used: 1.7 KB; limit: 2.9 KB")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Staleness
  // ═══════════════════════════════════════════════════════════════════

  describe("Staleness", () => {
    withPane("solve")

    it("shows staleness indicator when config hash changed after solve", () => {
      // The cachedResult has configHash "abc", but current config will hash differently
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: makeSolveResult({
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            }),
            jobId: "job_42",
            configHash: "definitely_stale_hash",
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Optimiser",
            originalResult: makeSolveResult({
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            }),
            frontier: null,
            selectedPointIndex: null,
          },
        },
      })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText("Config changed since last solve")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Re-run" })).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Result type / Efficient Frontier
  // ═══════════════════════════════════════════════════════════════════

  describe("Result type / Efficient Frontier", () => {
    withPane("constraints")

    it("does not show result type selector when no constraints are configured", () => {
      renderConfig(makeProps())
      expect(screen.queryByTestId("constraint-card")).not.toBeInTheDocument()
      expect(screen.queryByText("Result type")).not.toBeInTheDocument()
      expect(screen.queryByText("Efficient frontier")).not.toBeInTheDocument()
    })

    it("does not show orphan frontier settings when frontier is stale-enabled without constraints", () => {
      renderConfig(makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: {},
          frontier_enabled: true,
        },
      }))
      expect(screen.queryByTestId("constraint-card")).not.toBeInTheDocument()
      expect(screen.queryByText("Result type")).not.toBeInTheDocument()
      expect(screen.queryByText("Efficient frontier")).not.toBeInTheDocument()
      expect(screen.queryByText("Min value")).not.toBeInTheDocument()
      expect(screen.queryByText("Max value")).not.toBeInTheDocument()
      expect(screen.queryByText("Steps")).not.toBeInTheDocument()
    })

    it("shows point/frontier choice inside constraints when constraints are configured", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          }))
      expect(screen.getByText("Result type")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Individual point" })).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Efficient frontier" })).toBeInTheDocument()
      const card = screen.getByTestId("constraint-card")
      expect(within(card).getByTestId("constraint-row")).toBeInTheDocument()
      const bound = within(card).getByTestId("constraint-bound-row")
      expect(within(bound).getByText("Maximum")).toBeInTheDocument()
      expect(within(bound).getByDisplayValue("1.05")).toBeInTheDocument()
      expect(screen.queryByText("Min value")).not.toBeInTheDocument()
      expect(screen.queryByText("Max value")).not.toBeInTheDocument()
      expect(screen.queryByText("Steps")).not.toBeInTheDocument()
    })

    it("keeps each constraint's bound or frontier range inside that constraint's card", () => {
      const config = {
        _nodeId: "opt_1",
        mode: "online",
        objective: "premium",
        constraints: { loss_ratio: { max: 1.05 }, volume: { min: 900 } },
        frontier_ranges: { loss_ratio: { min: 0.9, max: 1.2 }, volume: { min: 800, max: 1000 } },
      }
      const view = renderConfig(makeProps({ config }))
      const [lossCard, volumeCard] = screen.getAllByTestId("constraint-card")
      expect(within(lossCard).getByRole("combobox", { name: "loss_ratio constraint column" })).toBeInTheDocument()
      expect(within(lossCard).getByLabelText("loss_ratio constraint value")).toHaveValue(1.05)
      expect(within(volumeCard).getByRole("combobox", { name: "volume constraint column" })).toBeInTheDocument()
      expect(within(volumeCard).getByLabelText("volume constraint value")).toHaveValue(900)
      view.unmount()

      renderConfig(makeProps({ config: { ...config, frontier_enabled: true } }))
      const [lossRangeCard, volumeRangeCard] = screen.getAllByTestId("constraint-card")
      expect(within(lossRangeCard).getByLabelText("loss_ratio min value")).toHaveValue(0.9)
      expect(within(lossRangeCard).getByLabelText("loss_ratio max value")).toHaveValue(1.2)
      expect(within(lossRangeCard).queryByLabelText("loss_ratio constraint value")).not.toBeInTheDocument()
      expect(within(volumeRangeCard).getByLabelText("volume min value")).toHaveValue(800)
      expect(within(volumeRangeCard).getByLabelText("volume max value")).toHaveValue(1000)
      // Steps and Auto range belong to the whole frontier, not to one card.
      expect(within(lossRangeCard).queryByText("Steps")).not.toBeInTheDocument()
      expect(within(screen.getByTestId("frontier-settings")).getByText("Steps per constraint")).toBeInTheDocument()
    })

    it("shows point/frontier choice inside ratebook constraints", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "ratebook",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
              factor_columns: [["age_band"]],
            },
          }))
      expect(screen.getByText("Result type")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Individual point" })).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Efficient frontier" })).toBeInTheDocument()
      const bound = within(screen.getByTestId("constraint-card")).getByTestId("constraint-bound-row")
      expect(within(bound).getByText("Maximum")).toBeInTheDocument()
      expect(within(bound).getByDisplayValue("1.05")).toBeInTheDocument()
      expect(screen.queryByText("Min value")).not.toBeInTheDocument()
    })

    it("shows frontier settings for ratebook efficient frontier", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "ratebook",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
              factor_columns: [["age_band"]],
              frontier_enabled: true,
            },
          }))
      const card = screen.getByTestId("constraint-card")
      expect(within(card).queryByTestId("constraint-bound-row")).not.toBeInTheDocument()
      expect(within(card).queryByText("Maximum")).not.toBeInTheDocument()
      expect(within(card).queryByDisplayValue("1.05")).not.toBeInTheDocument()
      expect(within(card).getByText("Min value")).toBeInTheDocument()
      expect(within(card).getByText("Max value")).toBeInTheDocument()
      expect(screen.queryByText("Min multiplier")).not.toBeInTheDocument()
      expect(screen.queryByText("Max multiplier")).not.toBeInTheDocument()
      expect(within(screen.getByTestId("frontier-settings")).getByText("Steps per constraint")).toBeInTheDocument()
    })

    it("selecting efficient frontier updates frontier_enabled", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      })
      renderConfig(props)
      fireEvent.click(screen.getByRole("button", { name: "Efficient frontier" }))
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("frontier_enabled", true)
    })

    it("selecting individual point updates frontier_enabled", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)
      fireEvent.click(screen.getByRole("button", { name: "Individual point" }))
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("frontier_enabled", false)
    })

    it("highlights missing frontier range values instead of rendering defaults", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
              frontier_enabled: true,
            },
          }))
      expect(screen.getAllByTestId("constraint-card")).toHaveLength(1)
      const card = screen.getByTestId("constraint-card")
      expect(within(card).getByTestId("constraint-row")).toBeInTheDocument()
      expect(within(card).getByText("Min value")).toBeInTheDocument()
      expect(within(card).getByText("Max value")).toBeInTheDocument()
      expect(screen.queryByText("Min multiplier")).not.toBeInTheDocument()
      expect(screen.queryByText("Max multiplier")).not.toBeInTheDocument()
      const minInput = within(card).getByLabelText("loss_ratio min value") as HTMLInputElement
      const maxInput = within(card).getByLabelText("loss_ratio max value") as HTMLInputElement
      expect(minInput.value).toBe("")
      expect(maxInput.value).toBe("")
      expect(minInput).toHaveAttribute("aria-invalid", "true")
      expect(maxInput).toHaveAttribute("aria-invalid", "true")
      expect(screen.queryByDisplayValue("0.8")).not.toBeInTheDocument()
      expect(screen.queryByDisplayValue("1.1")).not.toBeInTheDocument()
      const frontierSettings = screen.getByTestId("frontier-settings")
      expect(within(frontierSettings).getByText("Steps per constraint")).toBeInTheDocument()
      expect(within(frontierSettings).getByDisplayValue("15")).toBeInTheDocument()
      expect(within(card).queryByTestId("constraint-bound-row")).not.toBeInTheDocument()
      expect(within(card).queryByText("Maximum")).not.toBeInTheDocument()
      expect(within(card).queryByDisplayValue("1.05")).not.toBeInTheDocument()
    })

    it("renders per-constraint frontier range values from config", () => {
      renderConfig(makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
              frontier_enabled: true,
              frontier_ranges: { loss_ratio: { min: 11, max: 39 } },
            },
          }))
      expect(screen.getByDisplayValue("11")).toBeInTheDocument()
      expect(screen.getByDisplayValue("39")).toBeInTheDocument()
    })

    it("auto range populates efficient-frontier values from scenario envelope", async () => {
      mockStartOptimiserFrontierAutoRange.mockResolvedValue({
        status: "started",
        job_id: "range-job-1",
        error: null,
      })
      mockGetOptimiserFrontierAutoRangeStatus.mockResolvedValue({
        status: "completed",
        progress: 1,
        message: "Completed",
        elapsed_seconds: 1.2,
        result: {
          status: "ok",
          ranges: { loss_ratio: { min: 11, max: 39 } },
          method: "scenario_envelope",
          warning: null,
        },
      })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      await waitFor(() => {
        expect(mockStartOptimiserFrontierAutoRange).toHaveBeenCalledWith({
          graph: { nodes: [], edges: [], preamble: "" },
          node_id: "opt_1",
          signal: expect.any(AbortSignal),
        })
        expect(mockGetOptimiserFrontierAutoRangeStatus).toHaveBeenCalledWith(
          "range-job-1",
          { signal: expect.any(AbortSignal) },
        )
      })
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith({
        frontier_ranges: { loss_ratio: { min: 11, max: 39 } },
      })
    })

    it("lets a running auto-range job be restarted and supersedes the old request", async () => {
      let firstStatusSignal: AbortSignal | undefined
      mockCancelOptimiserFrontierAutoRange.mockResolvedValue(undefined)
      mockStartOptimiserFrontierAutoRange
        .mockResolvedValueOnce({ status: "started", job_id: "range-job-1", error: null })
        .mockResolvedValueOnce({ status: "started", job_id: "range-job-2", error: null })
      mockGetOptimiserFrontierAutoRangeStatus
        .mockImplementationOnce((_jobId, options: { signal?: AbortSignal }) => {
          firstStatusSignal = options.signal
          return new Promise(() => {})
        })
        .mockResolvedValueOnce({
          status: "completed",
          progress: 1,
          message: "Completed",
          elapsed_seconds: 0.5,
          result: {
            status: "ok",
            ranges: { loss_ratio: { min: 10, max: 40 } },
            method: "scenario_envelope",
            warning: null,
          },
        })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))
      const restart = await screen.findByRole("button", { name: "Restart auto range" })
      expect(restart).toBeEnabled()
      fireEvent.click(restart)

      await waitFor(() => {
        expect(mockStartOptimiserFrontierAutoRange).toHaveBeenCalledTimes(2)
        expect(mockCancelOptimiserFrontierAutoRange).toHaveBeenCalledWith("range-job-1")
        expect(props.componentProps.onUpdate).toHaveBeenCalledWith({
          frontier_ranges: { loss_ratio: { min: 10, max: 40 } },
        })
      })
      expect(firstStatusSignal?.aborted).toBe(true)
    })

    it("auto range surfaces contract-error status messages", async () => {
      mockStartOptimiserFrontierAutoRange.mockResolvedValue({
        status: "started",
        job_id: "range-job-1",
        error: null,
      })
      mockGetOptimiserFrontierAutoRangeStatus.mockResolvedValue({
        status: "contract_error",
        progress: 1,
        message: "Fan-in projection contract does not cover columns required by the node.",
        elapsed_seconds: 1.2,
        result: null,
        terminal_reason: "contract_error",
        error_code: "contract_error",
        http_status_code: 422,
        execution_metrics: makeExecutionMetricsFixture({
          profile: "auto_range",
          status: "running",
          terminal_reason: null,
        }),
      })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      expect(await screen.findByText(
        "Fan-in projection contract does not cover columns required by the node.",
      )).toBeInTheDocument()
      expect(screen.queryByText("Auto-range reached 75% of its memory allowance.")).not.toBeInTheDocument()
      expect(screen.queryByText("Technical details")).not.toBeInTheDocument()
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
    })

    it("auto range falls back to error_detail when status message is empty", async () => {
      mockStartOptimiserFrontierAutoRange.mockResolvedValue({
        status: "started",
        job_id: "range-job-1",
        error: null,
      })
      mockGetOptimiserFrontierAutoRangeStatus.mockResolvedValue({
        status: "contract_error",
        progress: 1,
        message: "",
        elapsed_seconds: 1.2,
        result: null,
        terminal_reason: "contract_error",
        error_code: "contract_error",
        http_status_code: 400,
        error_detail: "Configured optimiser data_input 'optimiser_input' did not produce data.",
      })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      expect(await screen.findByText(
        "Configured optimiser data_input 'optimiser_input' did not produce data.",
      )).toBeInTheDocument()
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
    })

    it("auto range derives memory-limited messages from execution metrics", async () => {
      mockStartOptimiserFrontierAutoRange.mockResolvedValue({
        status: "started",
        job_id: "range-job-1",
        error: null,
      })
      mockGetOptimiserFrontierAutoRangeStatus.mockResolvedValue({
        status: "memory_limited",
        progress: 1,
        message: "Stopped",
        elapsed_seconds: 1.2,
        result: null,
        terminal_reason: "memory_limited",
        error_code: "memory_limited",
        http_status_code: 507,
        execution_metrics: makeExecutionMetricsFixture({ profile: "auto_range" }),
      })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      expect(await screen.findByText(
        "Auto range failed: auto-range reached 75% of its memory allowance. Memory used: 1.7 KB; limit: 2.9 KB.",
      )).toBeInTheDocument()
      expect(screen.getByText("Technical details")).toBeInTheDocument()
      expect(screen.getByText("During: Collecting results")).toBeInTheDocument()
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
    })

    it("auto range uses execution metrics for memory-limited failures", async () => {
      mockStartOptimiserFrontierAutoRange.mockResolvedValue({
        status: "started",
        job_id: "range-job-1",
        error: null,
      })
      mockGetOptimiserFrontierAutoRangeStatus.mockResolvedValue({
        status: "memory_limited",
        progress: 1,
        message: "Auto-range exceeded its memory budget (rss_exceeds_memory_limit).",
        elapsed_seconds: 1.2,
        result: null,
        terminal_reason: "memory_limited",
        error_code: "memory_limit",
        http_status_code: 507,
        execution_metrics: makeExecutionMetricsFixture({ profile: "auto_range", terminal_reason: "memory_limited" }),
      })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      expect(await screen.findByText(
        "Auto range failed: auto-range reached 75% of its memory allowance. Memory used: 1.7 KB; limit: 2.9 KB.",
      )).toBeInTheDocument()
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
    })

    it("auto range preserves structured metrics from admission failures before a job starts", async () => {
      const executionMetrics = makeExecutionMetricsFixture({
        profile: "auto_range",
        terminal_reason: null,
      })
      mockStartOptimiserFrontierAutoRange.mockRejectedValue(Object.assign(new Error("HTTP 507"), {
        name: "ApiError",
        status: 507,
        detail: JSON.stringify({
          message: "Auto-range exceeded its memory budget (rss_exceeds_memory_limit).",
          error_code: "memory_limit",
          reason: "rss_exceeds_memory_limit",
          execution_metrics: executionMetrics,
        }),
        rawDetail: {
          message: "Auto-range exceeded its memory budget (rss_exceeds_memory_limit).",
          error_code: "memory_limit",
          reason: "rss_exceeds_memory_limit",
          execution_metrics: executionMetrics,
        },
      }))
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      expect(await screen.findByText(
        "Auto range failed: auto-range reached 75% of its memory allowance. Memory used: 1.7 KB; limit: 2.9 KB.",
      )).toBeInTheDocument()
      expect(screen.getByText("Technical details")).toBeInTheDocument()
      expect(screen.getByText("During: Collecting results")).toBeInTheDocument()
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
    })

    it("auto range does not render raw object error details", async () => {
      mockStartOptimiserFrontierAutoRange.mockResolvedValue({
        status: "started",
        job_id: "range-job-1",
        error: null,
      })
      mockGetOptimiserFrontierAutoRangeStatus.mockResolvedValue({
        status: "contract_error",
        progress: 1,
        message: "",
        elapsed_seconds: 1.2,
        result: null,
        terminal_reason: "contract_error",
        error_code: "contract_error",
        http_status_code: 422,
        error_detail: { raw: "developer-only", nested: { stack: "trace" } },
      })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 35 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)

      fireEvent.click(screen.getByRole("button", { name: "Auto range" }))

      expect(await screen.findByText("Auto range failed (contract_error)")).toBeInTheDocument()
      expect(screen.queryByText(/developer-only/)).not.toBeInTheDocument()
      expect(screen.queryByText(/"raw"/)).not.toBeInTheDocument()
    })

    it("changing a frontier minimum preserves an unset maximum", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)
      const input = screen.getByLabelText("loss_ratio min value")
      fireEvent.change(input, { target: { value: "0.75" } })
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
      fireEvent.blur(input)
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith({
        frontier_ranges: { loss_ratio: { min: 0.75 } },
      })
    })

    it("changing a frontier maximum preserves an unset minimum", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)
      const input = screen.getByLabelText("loss_ratio max value")
      fireEvent.change(input, { target: { value: "1.25" } })
      fireEvent.keyDown(input, { key: "Enter" })
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith({
        frontier_ranges: { loss_ratio: { max: 1.25 } },
      })
    })

    it("changing frontier_steps calls onUpdate", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
          frontier_enabled: true,
        },
      })
      renderConfig(props)
      const input = screen.getByDisplayValue("15")
      fireEvent.change(input, { target: { value: "20" } })
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
      fireEvent.blur(input)
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("frontier_steps", 20)
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // MLflow section
  // ═════════════════════════════════════════════════════════════════

  describe("Publish section", () => {
    withPane("export")

    const APPLY_NODE: SimpleNode = {
      id: "apply_1",
      data: { label: "Apply Rates", description: "", nodeType: "optimiserApply", config: {} },
    }
    const OPTIMISER_NODE: SimpleNode = {
      id: "opt_1",
      data: { label: "My Optimiser", description: "", nodeType: "optimiser", config: {} },
    }

    function seedSolve(overrides: Record<string, unknown> = {}) {
      const config = { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {} }
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: makeSolveResult(),
            originalResult: makeSolveResult(),
            jobId: "job_pub",
            configHash: hashConfig(config),
            source: "live",
            structuralVersion: useGraphStore.getState().structuralVersion,
            constraints: {},
            nodeLabel: "My Optimiser",
            frontier: null,
            selectedPointIndex: null,
            ...overrides,
          },
        },
      })
      return config
    }

    it("explains there is nothing to publish before any solve", () => {
      renderConfig(makeProps())
      expect(screen.getByText(/Nothing to publish yet/)).toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "Save to file" })).not.toBeInTheDocument()
    })

    it("saves the solved result to the default path and offers it to an Apply node", async () => {
      const config = seedSolve()
      mockSaveOptimiser.mockResolvedValue({
        status: "ok",
        path: "C:/proj/output/optimiser_My_Optimiser_opt_1.json",
        apply_path: "output/optimiser_My_Optimiser_opt_1.json",
        message: "Saved",
      })
      const onUpdateNodeConfig = vi.fn(() => ({ ok: true as const }))
      renderConfig(makeProps({
        config,
        onUpdateNodeConfig,
        allNodes: [...DEFAULT_GRAPH_NODES, OPTIMISER_NODE, APPLY_NODE],
      }))

      fireEvent.click(screen.getByRole("button", { name: "Save to file" }))
      await waitFor(() => expect(mockSaveOptimiser).toHaveBeenCalledWith({
        job_id: "job_pub",
        output_path: "output/optimiser_My_Optimiser_opt_1.json",
        point_index: undefined,
        version: "",
        overwrite: false,
        stale: false,
      }))
      const receipt = await screen.findByTestId("optimiser-save-receipt")
      expect(receipt).toHaveTextContent("Saved the solved result to output/optimiser_My_Optimiser_opt_1.json")

      fireEvent.change(within(receipt).getByRole("combobox", { name: "Apply Optimisation node" }), { target: { value: "apply_1" } })
      fireEvent.click(within(receipt).getByRole("button", { name: "Use in Apply node" }))
      expect(onUpdateNodeConfig).toHaveBeenCalledWith("apply_1", {
        sourceType: "file",
        artifact_path: "output/optimiser_My_Optimiser_opt_1.json",
      })
      expect(receipt).toHaveTextContent("Apply Rates now loads output/optimiser_My_Optimiser_opt_1.json.")
    })

    it("asks before replacing an existing file and retries with overwrite", async () => {
      const config = seedSolve()
      const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client")
      mockSaveOptimiser
        .mockRejectedValueOnce(new ApiError("HTTP 409", 409, undefined, undefined, {
          error_code: "optimiser_result_exists",
          message: "output/q3.json already exists.",
        }))
        .mockResolvedValueOnce({ status: "ok", path: "C:/proj/output/q3.json", apply_path: "output/q3.json", message: "" })
      renderConfig(makeProps({ config: { ...config, result_export_path: "output/q3.json" } }))

      fireEvent.click(screen.getByRole("button", { name: "Save to file" }))
      expect(await screen.findByText("output/q3.json already exists.")).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Replace existing file" }))
      await waitFor(() => expect(mockSaveOptimiser).toHaveBeenLastCalledWith(expect.objectContaining({
        output_path: "output/q3.json",
        overwrite: true,
      })))
      expect(await screen.findByTestId("optimiser-save-receipt")).toHaveTextContent("output/q3.json")
    })

    it("binds Replace to the refused file, not whatever the path field says later", async () => {
      const config = seedSolve()
      const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client")
      mockSaveOptimiser
        .mockRejectedValueOnce(new ApiError("HTTP 409", 409, undefined, undefined, {
          error_code: "optimiser_result_exists",
          message: "Optimiser result already exists: output/a.json",
        }))
        .mockResolvedValueOnce({ status: "ok", path: "C:/proj/output/a.json", apply_path: "output/a.json", message: "" })
      renderStatefulConfig(makeProps({ config: { ...config, result_export_path: "output/a.json" } }))

      fireEvent.click(screen.getByRole("button", { name: "Save to file" }))
      expect(await screen.findByText("Optimiser result already exists: output/a.json")).toBeInTheDocument()

      const pathField = screen.getByLabelText("File path")
      fireEvent.change(pathField, { target: { value: "output/b.json" } })
      fireEvent.blur(pathField)
      expect(screen.queryByRole("button", { name: "Replace existing file" })).not.toBeInTheDocument()

      fireEvent.change(pathField, { target: { value: "output/a.json" } })
      fireEvent.blur(pathField)
      fireEvent.click(screen.getByRole("button", { name: "Replace existing file" }))
      await waitFor(() => expect(mockSaveOptimiser).toHaveBeenLastCalledWith(expect.objectContaining({
        output_path: "output/a.json",
        overwrite: true,
      })))
    })

    it("loads a ratebook frontier point's factor tables for the CSV", async () => {
      const ratebook = makeSolveResult({ mode: "ratebook", factor_tables: {}, combined_factor_bounds: { min: 0.9, max: 1.1 } })
      const summary = (total_objective: number) => ({
        total_objective, constraints: {}, lambdas: {}, converged: true, iterations: null,
        cd_iterations: null, clamp_rate: null, history: null, adjustments: null,
        factor_tables: null, diagnostics_errors: [],
      })
      const config = seedSolve({
        result: ratebook,
        originalResult: ratebook,
        selectedPointIndex: 0,
        frontier: {
          points: [{ total_objective: 10 }],
          point_summaries: [summary(10)],
          n_points: 1,
          points_returned: 1,
          constraint_names: [],
          points_limit: 2000,
          points_truncated: false,
        },
      })
      mockSelectFrontierPoint.mockResolvedValue({
        ...summary(10),
        status: "ok",
        point_index: 0,
        frontier_generation: 0,
        baseline_objective: 0,
        baseline_constraints: {},
        factor_tables: { age_band: [{ __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 }] },
        error: null,
      })
      renderConfig(makeProps({ config }))

      expect(screen.queryByRole("button", { name: "Download factor tables (CSV)" })).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Load factor tables for CSV" }))
      await waitFor(() => expect(mockSelectFrontierPoint).toHaveBeenCalledWith({
        job_id: "job_pub",
        point_index: 0,
        include_ratebook_tables: true,
      }))
      expect(await screen.findByRole("button", { name: "Download factor tables (CSV)" })).toBeInTheDocument()
    })

    function seedRatebookFrontier() {
      const ratebook = makeSolveResult({ mode: "ratebook", factor_tables: {}, combined_factor_bounds: { min: 0.9, max: 1.1 } })
      const summary = (total_objective: number) => ({
        total_objective, constraints: {}, lambdas: {}, converged: true, iterations: null,
        cd_iterations: null, clamp_rate: null, history: null, adjustments: null,
        factor_tables: null, diagnostics_errors: [],
      })
      const config = seedSolve({
        result: ratebook,
        originalResult: ratebook,
        selectedPointIndex: 0,
        frontier: {
          points: [{ total_objective: 10 }],
          point_summaries: [summary(10)],
          n_points: 1,
          points_returned: 1,
          constraint_names: [],
          points_limit: 2000,
          points_truncated: false,
        },
      })
      const reply = {
        ...summary(10),
        status: "ok",
        point_index: 0,
        frontier_generation: 0,
        baseline_objective: 0,
        baseline_constraints: {},
        factor_tables: { age_band: [{ __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 }] },
        error: null,
      }
      let resolveReply: (value: unknown) => void = () => {}
      mockSelectFrontierPoint.mockReturnValue(new Promise((resolve) => { resolveReply = resolve }))
      return { config, deliver: () => act(async () => { resolveReply(reply) }) }
    }

    it("keeps the chosen target when a factor-table reply arrives after the choice changed", async () => {
      const { config, deliver } = seedRatebookFrontier()
      renderConfig(makeProps({ config }))
      fireEvent.click(screen.getByRole("button", { name: "Load factor tables for CSV" }))
      fireEvent.change(screen.getByRole("combobox", { name: "Result to publish" }), { target: { value: "" } })

      await deliver()
      const cached = useNodeResultsStore.getState().solveResults.opt_1
      expect(cached?.selectedPointIndex).toBeNull()
      expect(cached?.frontier?.point_summaries[0].factor_tables).toEqual({
        age_band: [{ __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 }],
      })
    })

    it("drops a factor-table reply for a job the node has moved past", async () => {
      const { config, deliver } = seedRatebookFrontier()
      renderConfig(makeProps({ config }))
      fireEvent.click(screen.getByRole("button", { name: "Load factor tables for CSV" }))
      act(() => {
        const previous = useNodeResultsStore.getState().solveResults.opt_1
        if (!previous || previous.result === null) throw new Error("The seeded solve has no result")
        useNodeResultsStore.setState({
          solveResults: { opt_1: { ...previous, jobId: "job_newer", selectedPointIndex: 0 } },
        })
      })

      await deliver()
      const cached = useNodeResultsStore.getState().solveResults.opt_1
      expect(cached?.jobId).toBe("job_newer")
      expect(cached?.frontier?.point_summaries[0].factor_tables).toBeNull()
      expect(cached?.result?.factor_tables).toEqual({})
    })

    it("drops a factor-table reply from a frontier generation the node has recomputed past", async () => {
      const { config, deliver } = seedRatebookFrontier()
      renderConfig(makeProps({ config }))
      fireEvent.click(screen.getByRole("button", { name: "Load factor tables for CSV" }))
      // The recompute keeps the job and point 0 is a different point now.
      act(() => {
        const previous = useNodeResultsStore.getState().solveResults.opt_1
        if (!previous || previous.result === null) throw new Error("The seeded solve has no result")
        const recomputed = { ...previous.originalResult, frontier_generation: 1 }
        useNodeResultsStore.setState({
          solveResults: {
            opt_1: {
              ...previous,
              result: recomputed,
              originalResult: recomputed,
              frontier: previous.frontier && { ...previous.frontier, frontier_generation: 1 },
            },
          },
        })
      })

      await deliver()
      const cached = useNodeResultsStore.getState().solveResults.opt_1
      expect(cached?.originalResult?.frontier_generation).toBe(1)
      expect(cached?.frontier?.point_summaries[0].factor_tables).toBeNull()
      expect(cached?.result?.factor_tables).toEqual({})
    })

    it("reports a factor-table reply from a generation the result does not show", async () => {
      const { config } = seedRatebookFrontier()
      mockSelectFrontierPoint.mockResolvedValue({
        total_objective: 10, constraints: {}, lambdas: {}, converged: true, iterations: null,
        cd_iterations: null, clamp_rate: null, history: null, adjustments: null, diagnostics_errors: [],
        status: "ok",
        point_index: 0,
        frontier_generation: 3,
        baseline_objective: 0,
        baseline_constraints: {},
        factor_tables: { age_band: [{ __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 }] },
        error: null,
      })
      renderConfig(makeProps({ config }))
      fireEvent.click(screen.getByRole("button", { name: "Load factor tables for CSV" }))

      expect(await screen.findByText(
        /The server answered for frontier generation 3, but this result shows generation 0\./,
      )).toBeInTheDocument()
      const cached = useNodeResultsStore.getState().solveResults.opt_1
      expect(cached?.frontier?.point_summaries[0].factor_tables).toBeNull()
    })

    it("publishes the chosen frontier point, the same selection the preview shows", async () => {
      const config = seedSolve({
        frontier: {
          points: [{ total_objective: 10 }, { total_objective: 20 }],
          point_summaries: [10, 20].map((total_objective) => ({
            total_objective,
            constraints: {},
            lambdas: {},
            converged: true,
            iterations: null,
            cd_iterations: null,
            clamp_rate: null,
            history: null,
            adjustments: null,
            factor_tables: null,
          })),
          n_points: 2,
          points_returned: 2,
          constraint_names: [],
          points_limit: 2000,
          points_truncated: false,
        },
      })
      mockLogOptimiserToMlflow.mockResolvedValue({ status: "ok", backend: "local", experiment_name: "My Optimiser", run_id: "run_7", run_url: null, tracking_uri: "" })
      renderConfig(makeProps({ config }))

      fireEvent.change(screen.getByRole("combobox", { name: "Result to publish" }), { target: { value: "1" } })
      expect(useNodeResultsStore.getState().solveResults.opt_1?.selectedPointIndex).toBe(1)
      fireEvent.click(screen.getByRole("button", { name: "Log to MLflow" }))
      await waitFor(() => expect(mockLogOptimiserToMlflow).toHaveBeenCalledWith(expect.objectContaining({
        job_id: "job_pub",
        point_index: 1,
        stale: false,
      })))
      expect(await screen.findByTestId("optimiser-log-receipt")).toHaveTextContent("Logged frontier point 2 to My Optimiser: run run_7")
    })

    it("labels publishing an outdated result and records it on the request", async () => {
      const config = seedSolve()
      mockSaveOptimiser.mockResolvedValue({ status: "ok", path: "p", apply_path: "p", message: "" })
      renderConfig(makeProps({ config: { ...config, objective: "loss_ratio" } }))

      expect(screen.getByText(/has changed since this result was solved/)).toBeInTheDocument()
      fireEvent.click(screen.getByRole("button", { name: "Save outdated result" }))
      await waitFor(() => expect(mockSaveOptimiser).toHaveBeenCalledWith(expect.objectContaining({ stale: true })))
      expect(screen.getByRole("button", { name: "Log outdated result" })).toBeInTheDocument()
    })

    it("logs to the node's own destination and experiment, read at click time", async () => {
      const config = seedSolve()
      mockLogOptimiserToMlflow.mockResolvedValue({ status: "ok", backend: "local", experiment_name: "e", run_id: "r", run_url: null, tracking_uri: "" })
      const made = makeProps({ config: { ...config, mlflow_destination: "local", mlflow_experiment: "/Shared/pricing/opt" } })
      const view = renderConfig(made)
      fireEvent.click(screen.getByRole("button", { name: "Log to MLflow" }))
      await waitFor(() => expect(mockLogOptimiserToMlflow).toHaveBeenLastCalledWith(expect.objectContaining({
        destination: "local",
        experiment_name: "/Shared/pricing/opt",
      })))

      // Back to Local folder with no experiment: the next log follows the config.
      view.rerender(
        <GraphProvider allNodes={made.graph.allNodes} edges={made.graph.edges}>
          <PaneBoundConfig {...made.componentProps} config={config} />
        </GraphProvider>,
      )
      fireEvent.click(screen.getByRole("button", { name: "Log to MLflow" }))
      await waitFor(() => expect(mockLogOptimiserToMlflow).toHaveBeenLastCalledWith(expect.objectContaining({
        destination: "",
        experiment_name: null,
      })))
    })

    it("shows the server's MLflow failure and offers the connection test", async () => {
      const config = seedSolve()
      const { ApiError } = await vi.importActual<typeof import("../../api/client")>("../../api/client")
      mockLogOptimiserToMlflow.mockRejectedValueOnce(new ApiError("HTTP 502", 502, undefined, undefined, {
        error_code: "mlflow_connectivity",
        message: "Could not reach the MLflow tracking server, so the run was not logged.",
      }))
      renderConfig(makeProps({ config }))
      fireEvent.click(screen.getByRole("button", { name: "Log to MLflow" }))

      expect(await screen.findByText(
        "MLflow log failed: Could not reach the MLflow tracking server, so the run was not logged.",
      )).toBeInTheDocument()
      expect(screen.queryByText(/ApiError|HTTP 502/)).toBeNull()
      expect(screen.getByRole("button", { name: "Test connection in MLflow settings" })).toBeInTheDocument()
    })

    it("shows the server's detail when a save fails", async () => {
      const config = seedSolve()
      mockSaveOptimiser.mockRejectedValueOnce({ message: "HTTP 422", detail: "The selected point cannot be saved." })
      renderConfig(makeProps({ config }))
      fireEvent.click(screen.getByRole("button", { name: "Save to file" }))
      expect(await screen.findByText("Save failed: The selected point cannot be saved.")).toBeInTheDocument()
      expect(screen.queryByText(/\[object Object\]/)).toBeNull()
    })

    it("disables Log with the node's own destination reason and a Configure link", () => {
      const config = seedSolve()
      setMlflowInventory({ destinations: [MLFLOW_SERVER_UNCONFIGURED, MLFLOW_LOCAL] })
      useUIStore.setState({ mlflowSettingsOpen: false })
      renderConfig(makeProps({ config: { ...config, mlflow_destination: "server" } }))

      expect(screen.getByRole("button", { name: "Log to MLflow" })).toBeDisabled()
      expect(screen.getAllByText(new RegExp(MLFLOW_SERVER_UNCONFIGURED.detail.slice(0, 30))).length).toBeGreaterThan(0)
      fireEvent.click(screen.getAllByRole("button", { name: "Configure MLflow" })[0])
      expect(useUIStore.getState().mlflowSettingsOpen).toBe(true)
    })

    it("keeps Log enabled when only another remote is unconfigured", () => {
      const config = seedSolve()
      setMlflowInventory({ destinations: [MLFLOW_SERVER_UNCONFIGURED, MLFLOW_LOCAL] })
      renderConfig(makeProps({ config: { ...config, mlflow_destination: "local" } }))
      expect(screen.getByRole("button", { name: "Log to MLflow" })).toBeEnabled()
    })

    it("offers the factor tables as CSV for a ratebook result and states its collar", () => {
      const ratebook = makeSolveResult({
        mode: "ratebook",
        factor_tables: { age_band: [{ __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 }] },
        combined_factor_bounds: { min: 0.8999999761581421, max: 1.100000023841858 },
      })
      const config = seedSolve({ result: ratebook, originalResult: ratebook })
      renderConfig(makeProps({ config }))
      expect(screen.getByRole("button", { name: "Download factor tables (CSV)" })).toBeInTheDocument()
      expect(screen.getByTestId("optimiser-combined-factor-collar")).toHaveTextContent(
        "Combined factor collar [0.8999999761581421, 1.100000023841858] — apply it in your rating engine.",
      )
    })

    it("refuses to offer a ratebook CSV without the solve's collar", () => {
      const ratebook = makeSolveResult({
        mode: "ratebook",
        factor_tables: { age_band: [{ __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 }] },
        combined_factor_bounds: null,
      })
      const config = seedSolve({ result: ratebook, originalResult: ratebook })
      renderConfig(makeProps({ config }))
      expect(screen.queryByRole("button", { name: "Download factor tables (CSV)" })).not.toBeInTheDocument()
      expect(screen.getByRole("alert")).toHaveTextContent("no combined factor collar")
    })

    it("states no collar for an online result", () => {
      const config = seedSolve()
      renderConfig(makeProps({ config }))
      expect(screen.queryByTestId("optimiser-combined-factor-collar")).not.toBeInTheDocument()
    })
  })

  describe("MLflow section", () => {
    withPane("export")

    const OPTIMISER_NODE: SimpleNode = {
      id: "opt_1",
      data: { label: "My Optimiser", description: "", nodeType: "optimiser", config: {} },
    }

    /** Render the Export pane with the optimiser node in the graph. */
    function renderMlflowSection(overrides: MakePropsOverrides = {}) {
      const made = makeProps({
        allNodes: [...DEFAULT_GRAPH_NODES, OPTIMISER_NODE],
        ...overrides,
      })
      renderConfig(made)
      return made.componentProps
    }

    /** Text anywhere in the panel EXCEPT inside a tooltip. */
    function proseText(pattern: RegExp) {
      return screen.queryByText(pattern, { ignore: "[role='tooltip'],script,style" })
    }

    /** Hover the icon's Tooltip wrapper; read the bubble the icon itself is described by. */
    function tooltipTextOf(ariaLabel: string): string {
      const icon = screen.getByLabelText(ariaLabel)
      fireEvent.mouseEnter(icon.parentElement!)
      return document.getElementById(icon.getAttribute("aria-describedby")!)!.textContent ?? ""
    }

    it("mounts the destination selector and drops the instruction prose", () => {
      renderMlflowSection()
      expect(screen.getByRole("radiogroup", { name: "MLflow destination" })).toBeTruthy()
      expect(proseText(/nothing is logged automatically/i)).toBeNull()
      expect(proseText(/toolbar/i)).toBeNull()
    })

    it("heads the always-open section with the manual-logging explainer", () => {
      renderMlflowSection()
      expect(screen.getByRole("heading", { name: "MLflow logging" })).toBeInTheDocument()
      expect(screen.queryByRole("button", { name: /MLflow logging/i })).not.toBeInTheDocument()
      expect(tooltipTextOf("About MLflow logging")).toMatch(/Nothing is logged automatically/)
    })

    it("names the Databricks default experiment path when the node chooses Databricks", () => {
      setMlflowInventory({
        destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
      })
      renderMlflowSection({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: {},
          mlflow_destination: "databricks",
        },
      })
      const help = tooltipTextOf("About the experiment path")
      expect(help).toContain("Leave blank to use /Shared/haute/My Optimiser.")
      expect(help).toContain("named group")
      expect(help).toContain("workspace folder path")
      expect(screen.getByLabelText("About the experiment path")).toHaveAccessibleDescription(
        /named group/,
      )
      expect(screen.getByLabelText("MLflow experiment path")).toHaveAttribute(
        "placeholder",
        "/Shared/haute/My Optimiser",
      )
    })

    it("names the bare node label when the node names no destination under the same inventory", () => {
      setMlflowInventory({
        destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
      })
      renderMlflowSection({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: {},
        },
      })
      const help = tooltipTextOf("About the experiment path")
      expect(help).toContain("Leave blank to use My Optimiser.")
      expect(help).toContain("named group")
      expect(help).toContain("workspace folder path")
      expect(screen.getByLabelText("MLflow experiment path")).toHaveAttribute(
        "placeholder",
        "My Optimiser",
      )
    })

    it("writes an explicit destination choice to the node config", () => {
      setMlflowInventory({
        destinations: [MLFLOW_DATABRICKS, MLFLOW_SERVER, MLFLOW_LOCAL],
      })
      const props = renderMlflowSection()
      fireEvent.click(screen.getByRole("radio", { name: /MLflow server/ }))
      expect(props.onUpdate).toHaveBeenCalledWith("mlflow_destination", "server")
    })
  })
})

// ═══════════════════════════════════════════════════════════════════
// Analysis columns (OPT-V09A)
// ═══════════════════════════════════════════════════════════════════

describe("OptimiserConfig analysis columns", () => {
  const DATA_COLUMNS = [
    { name: "premium", dtype: "Float64" },
    { name: "volume", dtype: "Float64" },
    { name: "quote_id", dtype: "String" },
    { name: "scenario_index", dtype: "Int64" },
    { name: "scenario_value", dtype: "Float64" },
    { name: "region", dtype: "String" },
  ]
  const REGION_COLUMNS = [
    { name: "quote_id", dtype: "String" },
    { name: "region", dtype: "String" },
    { name: "channel", dtype: "String" },
  ]
  const NODES: SimpleNode[] = [
    { id: "scored_node", data: { label: "scored", description: "", nodeType: "dataInput", config: {} } },
    { id: "regions_node", data: { label: "regions", description: "", nodeType: "dataInput", config: {} } },
    { id: "stray_node", data: { label: "stray", description: "", nodeType: "dataInput", config: {} } },
  ]
  const EDGES: SimpleEdge[] = [
    { id: "e-scored", source: "scored_node", target: "opt_1" },
    { id: "e-regions", source: "regions_node", target: "opt_1" },
  ]

  function analysisProps(config: Record<string, unknown> = {}) {
    return makeProps({
      config: {
        _nodeId: "opt_1",
        mode: "online",
        objective: "premium",
        constraints: {},
        data_input: "scored",
        ...config,
      },
      allNodes: NODES,
      edges: EDGES,
    })
  }

  function columnChoices(): string[] {
    const group = screen.getByRole("group", { name: "Analysis columns" })
    return within(group).getAllByRole("checkbox").map((box) => box.getAttribute("name") ?? "")
  }

  beforeEach(() => {
    mockUseDataInputColumns.mockImplementation((nodeId: string) =>
      nodeId === "regions_node" ? REGION_COLUMNS : DATA_COLUMNS,
    )
  })

  it("lists only the connected inputs, the data input first", () => {
    renderConfig(analysisProps())

    const select = screen.getByRole("combobox", { name: "Analysis input" }) as HTMLSelectElement
    expect(select).toHaveValue("")
    expect(Array.from(select.options).map((option) => [option.value, option.text])).toEqual([
      ["", "scored (Objectives & Constraints input)"],
      ["regions", "regions"],
    ])
    expect(screen.getByText(/used only to break results down/i)).toBeInTheDocument()
  })

  it("offers the chosen frame's columns, never the quote id", () => {
    const { unmount } = renderConfig(analysisProps())
    expect(columnChoices()).toEqual(["premium", "volume", "scenario_index", "scenario_value", "region"])
    unmount()

    renderConfig(analysisProps({ analysis_input: "regions" }))
    expect(columnChoices()).toEqual(["region", "channel"])
    expect(mockUseDataInputColumns).toHaveBeenCalledWith(
      "regions_node", expect.anything(), expect.anything(), undefined, undefined, expect.anything(),
    )
  })

  it("toggles a column into and out of the configuration", () => {
    const props = analysisProps({ analysis_columns: ["region"] })
    renderConfig(props)

    fireEvent.click(screen.getByRole("checkbox", { name: "volume" }))
    expect(props.componentProps.onUpdate).toHaveBeenCalledWith("analysis_columns", ["region", "volume"])
    fireEvent.click(screen.getByRole("checkbox", { name: "region" }))
    expect(props.componentProps.onUpdate).toHaveBeenCalledWith("analysis_columns", [])
  })

  it("switching the frame removes the columns the new frame does not have", async () => {
    const onUpdate = vi.fn()
    renderStatefulConfig(analysisProps({ analysis_columns: ["region", "premium"] }), onUpdate)

    fireEvent.change(screen.getByRole("combobox", { name: "Analysis input" }), {
      target: { value: "regions" },
    })

    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalledWith("analysis_columns", ["region"])
    })
    expect(onUpdate).toHaveBeenCalledWith("analysis_input", "regions")
    expect(screen.getByRole("checkbox", { name: "region" })).toBeChecked()
  })

  it("flags a configured column the frame lacks without rewriting the configuration", () => {
    const props = analysisProps({ analysis_input: "regions", analysis_columns: ["region", "segment"] })
    renderConfig(props)

    expect(screen.getByText(/“segment” is not a column of the analysis input/)).toBeInTheDocument()
    expect(props.componentProps.onUpdate).not.toHaveBeenCalledWith("analysis_columns", expect.anything())
    showPane("solve")
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Analysis column “segment” is not a column of the analysis input.",
    )
  })

  it("flags an analysis input that is no longer connected", () => {
    renderConfig(analysisProps({ analysis_input: "stray", analysis_columns: ["region"] }))

    expect(screen.getByRole("combobox", { name: "Analysis input" })).toHaveValue("stray")
    expect(screen.getByText("The configured analysis input is not connected.")).toBeInTheDocument()
    showPane("solve")
    expect(screen.getByRole("alert")).toHaveTextContent("The selected analysis input is not connected.")
  })

  it("disables further choices at twelve columns", () => {
    const many = Array.from({ length: 12 }, (_, index) => ({ name: `c${index}`, dtype: "String" }))
    mockUseDataInputColumns.mockImplementation(() => [...many, { name: "c12", dtype: "String" }])
    renderConfig(analysisProps({ analysis_columns: many.map((column) => column.name) }))

    expect(screen.getByRole("checkbox", { name: "c12" })).toBeDisabled()
    expect(screen.getByRole("checkbox", { name: "c0" })).toBeEnabled()
    expect(screen.getByText("12 of 12 chosen")).toBeInTheDocument()
  })

  it("marks the solve stale when the analysis columns change", () => {
    const config = { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {}, data_input: "scored" }
    useNodeResultsStore.setState({
      solveResults: {
        opt_1: {
          result: makeSolveResult(),
          originalResult: makeSolveResult(),
          jobId: "job_1",
          configHash: hashConfig(config),
          source: "live",
          structuralVersion: useGraphStore.getState().structuralVersion,
          constraints: {},
          nodeLabel: "Opt",
          frontier: null,
          selectedPointIndex: null,
        },
      },
    })
    renderConfig(analysisProps({ analysis_columns: ["region"] }))
    showPane("solve")
    expect(screen.getByText("Config changed since last solve")).toBeInTheDocument()
  })
})
