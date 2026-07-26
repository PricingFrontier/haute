import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import { useEffect, useRef, useState } from "react"
import OptimiserConfig from "../OptimiserConfig"
import { GraphProvider } from "../GraphContext"
import useNodeResultsStore, { hashConfig } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { SimpleNode, SimpleEdge } from "../editors"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

// ── Mock API client ──
const mockSolveOptimiser = vi.fn()
const mockEstimateOptimiserSolve = vi.fn()
const mockStartOptimiserFrontierAutoRange = vi.fn()
const mockGetOptimiserFrontierAutoRangeStatus = vi.fn()
const mockCancelOptimiserFrontierAutoRange = vi.fn()

vi.mock("../../api/client", () => ({
  solveOptimiser: (...args: unknown[]) => mockSolveOptimiser(...args),
  estimateOptimiserSolve: (...args: unknown[]) => mockEstimateOptimiserSolve(...args),
  startOptimiserFrontierAutoRange: (...args: unknown[]) => mockStartOptimiserFrontierAutoRange(...args),
  getOptimiserFrontierAutoRangeStatus: (...args: unknown[]) => mockGetOptimiserFrontierAutoRangeStatus(...args),
  cancelOptimiserFrontierAutoRange: (...args: unknown[]) => mockCancelOptimiserFrontierAutoRange(...args),
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

// ── Default props ──
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
    upstreamColumns: [
      { name: "premium", dtype: "Float64" },
      { name: "loss_ratio", dtype: "Float64" },
      { name: "volume", dtype: "Float64" },
    ],
    ...componentOverrides,
  }
  return {
    componentProps,
    graph: { allNodes, edges, submodels, preamble },
  }
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
      <OptimiserConfig {...made.componentProps} />
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
        <OptimiserConfig
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
beforeEach(() => {
  useNodeResultsStore.setState({
    solveJobs: {},
    solveResults: {},
  })
  useSettingsStore.setState({
    openSections: {},
  })
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
    it("shows input node selector with connected nodes", () => {
      renderConfig(makeProps())
      // The dropdown should contain the connected node option
      expect(screen.getByText("Data Input")).toBeInTheDocument()
    })

    it("shows 'No inputs connected' when no edges exist", () => {
      renderConfig(makeProps({ edges: [] }))
      expect(screen.getByText(/No inputs connected/)).toBeInTheDocument()
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
          data_input: "input_1",
          objective: "expected_margin",
          constraints: {},
        },
        upstreamColumns: [{ name: "expected_margin", dtype: "Float64" }],
      })

      renderConfig(props)

      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "input_1",
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

    it("prefers the configured data-input node columns over other upstream columns", () => {
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
          data_input: "input_1",
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
        "input_1",
        props.graph.allNodes,
        props.graph.edges,
        undefined,
        undefined,
        {
          enabled: false,
          fallbackColumns: [{ name: "expected_margin", dtype: "Float64" }],
        },
      )
      expect(screen.getByText(/expected_margin \(Float64\)/)).toBeInTheDocument()
      expect(screen.queryByText(/rating_factor_only \(Utf8\)/)).not.toBeInTheDocument()
    })

    it("defers data-input column fetches while the selected preview is loading", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          data_input: "input_1",
          objective: "expected_margin",
          constraints: {},
        },
        upstreamColumns: [],
        deferColumnFetch: true,
      } as unknown as MakePropsOverrides)

      renderConfig(props)

      expect(mockUseDataInputColumns).toHaveBeenCalledWith(
        "input_1",
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

    it("warns for zero-level outputs while keeping healthy factor controls", () => {
      vi.mocked(classifyBandingNode).mockReturnValue({
        levels: { healthy_band: ["Yes"] }, configuredOutputs: ["healthy_band", "empty_band"],
        zeroLevelOutputs: ["empty_band"], zeroLevelIssues: [{ outputColumn: "empty_band" }],
      })
      renderConfig(makeProps({
        config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {}, banding_source: "banding_1" },
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
      // "My Banding" appears in the select option; use getAllByText since
      // banding factor buttons may also render the label
      expect(screen.getAllByText("My Banding").length).toBeGreaterThanOrEqual(1)
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
          banding_source: "banding_1",
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
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Optimise/ })).not.toBeDisabled()
      })
      expect(screen.getByText("Rating Factors (3 selected)")).toBeInTheDocument()
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
          banding_source: "banding_1",
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
        target: { value: "banding_2" },
      })

      await waitFor(() => {
        expect(screen.getByText("new_factor")).toBeInTheDocument()
      })
      expect(onUpdate).toHaveBeenCalledTimes(1)
      expect(onUpdate).toHaveBeenCalledWith({
        banding_source: "banding_2",
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
          banding_source: "banding_1",
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

      expect(screen.getByRole("button", { name: /Optimise/ })).toBeDisabled()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Column Mappings
  // ═══════════════════════════════════════════════════════════════════

  describe("Column Mappings", () => {
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
    it("shows Constraints (0) with Add button when no constraints", () => {
      renderConfig(makeProps())
      expect(screen.getByText(/Constraints \(0\)/)).toBeInTheDocument()
      expect(screen.getByText("Add")).toBeInTheDocument()
      expect(screen.queryByTestId("constraint-settings-card")).not.toBeInTheDocument()
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
      const settingsCard = screen.getByTestId("constraint-settings-card")
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
  // Advanced section
  // ═══════════════════════════════════════════════════════════════════

  describe("Advanced section", () => {
    it("is collapsed by default", () => {
      renderConfig(makeProps())
      // Advanced button exists
      expect(screen.getByText("Advanced")).toBeInTheDocument()
      // chunk_size should NOT be visible when collapsed
      expect(screen.queryByText("Chunk size")).not.toBeInTheDocument()
    })

    it("toggles open on click", () => {
      // Pre-set section as open since toggleSection flips the boolean
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      renderConfig(makeProps())
      expect(screen.getByText("Chunk size")).toBeInTheDocument()
      expect(screen.getByText("Record history")).toBeInTheDocument()
    })

    it("shows chunk_size and record_history in advanced", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      renderConfig(makeProps())
      expect(screen.getByDisplayValue("500000")).toBeInTheDocument()
      expect(screen.getByText("Off")).toBeInTheDocument()
    })

    it("ratebook mode shows CD iterations and CD tolerance in advanced", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      renderConfig(makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } }))
      expect(screen.getByText("CD iterations")).toBeInTheDocument()
      expect(screen.getByText("CD tolerance")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solve action
  // ═══════════════════════════════════════════════════════════════════

  describe("Solve action", () => {
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
        expect.objectContaining({ node_id: "opt_1", streamingChunkSize: expect.any(Number) }),
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
      expect(mockHandleConstraintValueChange).toHaveBeenCalledWith("loss_ratio", "max", 0.95)
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
  // Advanced section extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Advanced section extended", () => {
    it("clicking Advanced toggles the section in the settings store", () => {
      renderConfig(makeProps())
      fireEvent.click(screen.getByText("Advanced"))
      const state = useSettingsStore.getState()
      expect(state.openSections["optimiser.advanced"]).toBe(true)
    })

    it("clicking Advanced again collapses the section", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      renderConfig(makeProps())
      fireEvent.click(screen.getByText("Advanced"))
      const state = useSettingsStore.getState()
      expect(state.openSections["optimiser.advanced"]).toBe(false)
    })

    it("changing chunk_size calls onUpdate", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      const props = makeProps()
      renderConfig(props)
      const input = screen.getByDisplayValue("500000")
      fireEvent.change(input, { target: { value: "100000" } })
      expect(props.componentProps.onUpdate).not.toHaveBeenCalled()
      fireEvent.blur(input)
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("chunk_size", 100000)
    })

    it("toggling record_history calls onUpdate", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      const props = makeProps()
      renderConfig(props)
      fireEvent.click(screen.getByText("Off"))
      expect(props.componentProps.onUpdate).toHaveBeenCalledWith("record_history", true)
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Mode toggle extended
  // ═══════════════════════════════════════════════════════════════════

  describe("Mode toggle extended", () => {
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
    it("does not show staleness indicator when config hash matches", () => {
      const cfg = { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: {} }
      const matchingHash = hashConfig(cfg as Record<string, unknown>)
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: {
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            },
            jobId: "job_42",
            configHash: matchingHash,
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Optimiser",
            originalResult: {
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            },
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
            result: {
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            },
            jobId: "job_42",
            configHash: "definitely_stale_hash",
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Optimiser",
            originalResult: {
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            },
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
    const convergedResult = {
      result: {
        total_objective: 1000,
        baseline_objective: 900,
        constraints: { loss_ratio: 0.65 },
        baseline_constraints: { loss_ratio: 0.6 },
        lambdas: { loss_ratio: 0.005 },
        converged: true,
        iterations: 15,
        n_quotes: 5000,
        n_steps: 3,
      },
      jobId: "job_42",
      configHash: "",
      constraints: { loss_ratio: { max: 1.05 } },
      nodeLabel: "Optimiser",
      originalResult: {
        total_objective: 1000,
        baseline_objective: 900,
        constraints: { loss_ratio: 0.65 },
        baseline_constraints: { loss_ratio: 0.6 },
        lambdas: { loss_ratio: 0.005 },
        converged: true,
        iterations: 15,
        n_quotes: 5000,
        n_steps: 3,
      },
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
            result: {
              ...convergedResult.result,
              mode: "ratebook",
              iterations: 11,
              cd_iterations: null,
            },
            originalResult: {
              ...convergedResult.originalResult,
              mode: "ratebook",
              iterations: 11,
              cd_iterations: null,
            },
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

    it("shows non-convergence warning when solveResult.converged is false", () => {
      const nonConverged = {
        ...convergedResult,
        result: { ...convergedResult.result, converged: false },
      }
      useNodeResultsStore.setState({ solveResults: { opt_1: nonConverged } })
      renderConfig(makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          }))
      expect(screen.getByText(/Solver did not converge/)).toBeInTheDocument()
      expect(screen.getByText(/Did not converge/)).toBeInTheDocument()
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
      expect(screen.getByText("12s")).toBeInTheDocument()
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

      expect(screen.getByText("Memory pressure reached 75% of the optimiser budget.")).toBeInTheDocument()
      expect(screen.getByText("RSS 1.7 KB of 2.9 KB limit")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Staleness
  // ═══════════════════════════════════════════════════════════════════

  describe("Staleness", () => {
    it("shows staleness indicator when config hash changed after solve", () => {
      // The cachedResult has configHash "abc", but current config will hash differently
      useNodeResultsStore.setState({
        solveResults: {
          opt_1: {
            result: {
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            },
            jobId: "job_42",
            configHash: "definitely_stale_hash",
            source: "live",
            structuralVersion: 0,
            constraints: {},
            nodeLabel: "Optimiser",
            originalResult: {
              total_objective: 1000,
              baseline_objective: 900,
              constraints: {},
              baseline_constraints: {},
              lambdas: {},
              converged: true,
              iterations: 5,
            },
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
    it("does not show result type selector when no constraints are configured", () => {
      renderConfig(makeProps())
      expect(screen.queryByTestId("constraint-settings-card")).not.toBeInTheDocument()
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
      expect(screen.queryByTestId("constraint-settings-card")).not.toBeInTheDocument()
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
      expect(screen.getAllByTestId("constraint-settings-card")).toHaveLength(1)
      const settingsCard = screen.getByTestId("constraint-settings-card")
      expect(within(settingsCard).getByTestId("constraint-row")).toBeInTheDocument()
      expect(within(settingsCard).getByText("Result type")).toBeInTheDocument()
      expect(within(settingsCard).getByRole("button", { name: "Individual point" })).toBeInTheDocument()
      expect(within(settingsCard).getByRole("button", { name: "Efficient frontier" })).toBeInTheDocument()
      const pointSettings = within(settingsCard).getByTestId("individual-point-settings")
      expect(within(pointSettings).queryByText("Individual point settings")).not.toBeInTheDocument()
      expect(within(pointSettings).queryByText("loss_ratio")).not.toBeInTheDocument()
      expect(within(pointSettings).getByText("Maximum")).toBeInTheDocument()
      expect(within(pointSettings).getByDisplayValue("1.05")).toBeInTheDocument()
      expect(screen.queryByText("Min value")).not.toBeInTheDocument()
      expect(screen.queryByText("Max value")).not.toBeInTheDocument()
      expect(screen.queryByText("Steps")).not.toBeInTheDocument()
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
      const settingsCard = screen.getByTestId("constraint-settings-card")
      expect(within(settingsCard).getByText("Result type")).toBeInTheDocument()
      expect(within(settingsCard).getByRole("button", { name: "Individual point" })).toBeInTheDocument()
      expect(within(settingsCard).getByRole("button", { name: "Efficient frontier" })).toBeInTheDocument()
      const pointSettings = within(settingsCard).getByTestId("individual-point-settings")
      expect(within(pointSettings).queryByText("Individual point settings")).not.toBeInTheDocument()
      expect(within(pointSettings).queryByText("loss_ratio")).not.toBeInTheDocument()
      expect(within(pointSettings).getByText("Maximum")).toBeInTheDocument()
      expect(within(pointSettings).getByDisplayValue("1.05")).toBeInTheDocument()
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
      const settingsCard = screen.getByTestId("constraint-settings-card")
      expect(within(settingsCard).queryByTestId("individual-point-settings")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByText("Maximum")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByDisplayValue("1.05")).not.toBeInTheDocument()
      expect(within(settingsCard).getByText("Min value")).toBeInTheDocument()
      expect(within(settingsCard).getByText("Max value")).toBeInTheDocument()
      expect(within(settingsCard).queryByText("Min multiplier")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByText("Max multiplier")).not.toBeInTheDocument()
      expect(within(settingsCard).getByText("Steps")).toBeInTheDocument()
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
      expect(screen.getAllByTestId("constraint-settings-card")).toHaveLength(1)
      const settingsCard = screen.getByTestId("constraint-settings-card")
      expect(within(settingsCard).getByTestId("constraint-row")).toBeInTheDocument()
      expect(within(settingsCard).getByText("Min value")).toBeInTheDocument()
      expect(within(settingsCard).getByText("Max value")).toBeInTheDocument()
      expect(within(settingsCard).queryByText("Min multiplier")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByText("Max multiplier")).not.toBeInTheDocument()
      expect(within(settingsCard).getByText("Steps")).toBeInTheDocument()
      const minInput = within(settingsCard).getByLabelText("loss_ratio min value") as HTMLInputElement
      const maxInput = within(settingsCard).getByLabelText("loss_ratio max value") as HTMLInputElement
      expect(minInput.value).toBe("")
      expect(maxInput.value).toBe("")
      expect(minInput).toHaveAttribute("aria-invalid", "true")
      expect(maxInput).toHaveAttribute("aria-invalid", "true")
      expect(within(settingsCard).queryByDisplayValue("0.8")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByDisplayValue("1.1")).not.toBeInTheDocument()
      expect(within(settingsCard).getByDisplayValue("15")).toBeInTheDocument()
      expect(within(settingsCard).queryByTestId("individual-point-settings")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByTestId("constraint-bound-settings")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByText("Maximum")).not.toBeInTheDocument()
      expect(within(settingsCard).queryByDisplayValue("1.05")).not.toBeInTheDocument()
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
          streamingChunkSize: expect.any(Number),
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
      expect(screen.queryByText("Memory pressure reached 75% of the auto-range budget.")).not.toBeInTheDocument()
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
        "Auto range failed: memory pressure reached 75% of the auto-range budget. RSS 1.7 KB of 2.9 KB limit.",
      )).toBeInTheDocument()
      expect(screen.getByText("Technical details")).toBeInTheDocument()
      expect(screen.getByText("Stage collect")).toBeInTheDocument()
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
        "Auto range failed: memory pressure reached 75% of the auto-range budget. RSS 1.7 KB of 2.9 KB limit.",
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
        "Auto range failed: memory pressure reached 75% of the auto-range budget. RSS 1.7 KB of 2.9 KB limit.",
      )).toBeInTheDocument()
      expect(screen.getByText("Technical details")).toBeInTheDocument()
      expect(screen.getByText("Stage collect")).toBeInTheDocument()
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
})
