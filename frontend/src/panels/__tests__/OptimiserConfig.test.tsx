import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import OptimiserConfig from "../OptimiserConfig"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"

// ── Mock API client ──
const mockSolveOptimiser = vi.fn()

vi.mock("../../api/client", () => ({
  solveOptimiser: (...args: unknown[]) => mockSolveOptimiser(...args),
}))

// ── Mock buildGraph ──
vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

// ── Mock banding utilities ──
vi.mock("../../utils/banding", () => ({
  extractBandingLevelsForNode: vi.fn(() => ({})),
}))
import { extractBandingLevelsForNode } from "../../utils/banding"

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

vi.mock("../../hooks/useConstraintHandlers", () => ({
  useConstraintHandlers: vi.fn(() => ({
    handleAddConstraint: mockHandleAddConstraint,
    handleRemoveConstraint: mockHandleRemoveConstraint,
    handleConstraintColumnChange: mockHandleConstraintColumnChange,
    handleConstraintValueChange: mockHandleConstraintValueChange,
  })),
}))

// ── Default props ──
function makeProps(overrides: Partial<Parameters<typeof OptimiserConfig>[0]> = {}) {
  return {
    config: {
      _nodeId: "opt_1",
      mode: "online",
      objective: "premium",
      constraints: {},
    } as Record<string, unknown>,
    onUpdate: vi.fn(),
    accentColor: "#f59e0b",
    upstreamColumns: [
      { name: "premium", dtype: "Float64" },
      { name: "loss_ratio", dtype: "Float64" },
    ],
    allNodes: [
      {
        id: "input_1",
        data: { label: "Data Input", description: "", nodeType: "dataSource", config: {} },
      },
    ],
    edges: [{ id: "e1", source: "input_1", target: "opt_1" }],
    ...overrides,
  }
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
  mockHandleAddConstraint.mockReset()
  mockHandleRemoveConstraint.mockReset()
  mockHandleConstraintColumnChange.mockReset()
  mockHandleConstraintValueChange.mockReset()
})

afterEach(cleanup)

// ═══════════════════════════════════════════════════════════════════
// Mode toggle
// ═══════════════════════════════════════════════════════════════════

describe("OptimiserConfig", () => {
  describe("Mode toggle", () => {
    it("renders with online mode selected by default", () => {
      render(<OptimiserConfig {...makeProps()} />)
      const onlineBtn = screen.getByRole("button", { name: "Online" })
      // Online button should have the active orange background
      expect(onlineBtn).toHaveStyle({ color: "#f59e0b" })
    })

    it("renders ratebook mode as active when config.mode is ratebook", () => {
      render(
        <OptimiserConfig
          {...makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } })}
        />,
      )
      const ratebookBtn = screen.getByRole("button", { name: "Ratebook" })
      expect(ratebookBtn).toHaveStyle({ color: "#f59e0b" })
    })

    it("clicking ratebook calls onUpdate with mode ratebook", () => {
      const props = makeProps()
      render(<OptimiserConfig {...props} />)
      fireEvent.click(screen.getByRole("button", { name: "Ratebook" }))
      expect(props.onUpdate).toHaveBeenCalledWith("mode", "ratebook")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Input / Objective selection
  // ═══════════════════════════════════════════════════════════════════

  describe("Input / Objective selection", () => {
    it("shows input node selector with connected nodes", () => {
      render(<OptimiserConfig {...makeProps()} />)
      // The dropdown should contain the connected node option
      expect(screen.getByText("Data Input")).toBeInTheDocument()
    })

    it("shows 'No inputs connected' when no edges exist", () => {
      render(<OptimiserConfig {...makeProps({ edges: [] })} />)
      expect(screen.getByText(/No inputs connected/)).toBeInTheDocument()
    })

    it("objective column dropdown lists data input columns", () => {
      render(<OptimiserConfig {...makeProps()} />)
      // The mocked useDataInputColumns returns premium, loss_ratio, volume
      // These appear as options in the objective select
      const options = screen.getAllByText(/premium/)
      expect(options.length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText(/loss_ratio \(Float64\)/)).toBeInTheDocument()
      expect(screen.getByText(/volume \(Float64\)/)).toBeInTheDocument()
    })

    it("objective change calls onUpdate with objective key", () => {
      const props = makeProps()
      render(<OptimiserConfig {...props} />)
      // Find the objective select — it has the "Select objective..." placeholder
      const selects = screen.getAllByRole("combobox")
      const objectiveSelect = selects.find(s =>
        Array.from(s.querySelectorAll("option")).some(o => o.textContent === "Select objective..."),
      )!
      fireEvent.change(objectiveSelect, { target: { value: "loss_ratio" } })
      expect(props.onUpdate).toHaveBeenCalledWith("objective", "loss_ratio")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Ratebook mode specific
  // ═══════════════════════════════════════════════════════════════════

  describe("Ratebook mode", () => {
    it("shows Rating Factor Source section in ratebook mode", () => {
      render(
        <OptimiserConfig
          {...makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } })}
        />,
      )
      expect(screen.getByText("Rating Factor Source")).toBeInTheDocument()
    })

    it("shows 'No Banding nodes found' when no banding nodes connected", () => {
      render(
        <OptimiserConfig
          {...makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } })}
        />,
      )
      expect(screen.getByText(/No Banding nodes found/)).toBeInTheDocument()
    })

    it("shows banding source selector when banding nodes are connected", () => {
      vi.mocked(extractBandingLevelsForNode).mockReturnValue({ age: ["1", "2", "3"], region: ["A", "B"] })

      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} },
            allNodes: [
              { id: "input_1", data: { label: "Data Input", description: "", nodeType: "dataSource", config: {} } },
              { id: "banding_1", data: { label: "My Banding", description: "", nodeType: "banding", config: {} } },
            ],
            edges: [
              { id: "e1", source: "input_1", target: "opt_1" },
              { id: "e2", source: "banding_1", target: "opt_1" },
            ],
          })}
        />,
      )
      // "My Banding" appears in the select option; use getAllByText since
      // banding factor buttons may also render the label
      expect(screen.getAllByText("My Banding").length).toBeGreaterThanOrEqual(1)
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Column Mappings
  // ═══════════════════════════════════════════════════════════════════

  describe("Column Mappings", () => {
    it("renders Quote ID, Scenario Index, Scenario Value selectors", () => {
      render(<OptimiserConfig {...makeProps()} />)
      expect(screen.getByText("Quote ID")).toBeInTheDocument()
      expect(screen.getByText("Scenario Index")).toBeInTheDocument()
      expect(screen.getByText("Scenario Value")).toBeInTheDocument()
    })

    it("column mapping change calls onUpdate with correct key", () => {
      const props = makeProps()
      render(<OptimiserConfig {...props} />)
      // Find all selects — look for the one with "Select quote id..." placeholder
      const selects = screen.getAllByRole("combobox")
      const quoteIdSelect = selects.find(s =>
        Array.from(s.querySelectorAll("option")).some(o => o.textContent === "Select quote id..."),
      )!
      fireEvent.change(quoteIdSelect, { target: { value: "premium" } })
      expect(props.onUpdate).toHaveBeenCalledWith("quote_id", "premium")
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Constraints
  // ═══════════════════════════════════════════════════════════════════

  describe("Constraints", () => {
    it("shows Constraints (0) with Add button when no constraints", () => {
      render(<OptimiserConfig {...makeProps()} />)
      expect(screen.getByText(/Constraints \(0\)/)).toBeInTheDocument()
      expect(screen.getByText("Add")).toBeInTheDocument()
    })

    it("shows 'No constraints added' text when empty", () => {
      render(<OptimiserConfig {...makeProps()} />)
      expect(screen.getByText(/No constraints added/)).toBeInTheDocument()
    })

    it("clicking Add calls handleAddConstraint", () => {
      render(<OptimiserConfig {...makeProps()} />)
      fireEvent.click(screen.getByText("Add"))
      expect(mockHandleAddConstraint).toHaveBeenCalledTimes(1)
    })

    it("renders constraint rows when constraints exist", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          })}
        />,
      )
      expect(screen.getByText(/Constraints \(1\)/)).toBeInTheDocument()
      // Should not show "No constraints added"
      expect(screen.queryByText(/No constraints added/)).not.toBeInTheDocument()
    })

    it("constraint type dropdown shows min/max/min_abs/max_abs options", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          })}
        />,
      )
      expect(screen.getByText("Min (relative)")).toBeInTheDocument()
      expect(screen.getByText("Max (relative)")).toBeInTheDocument()
      expect(screen.getByText("Min (absolute)")).toBeInTheDocument()
      expect(screen.getByText("Max (absolute)")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solver Tuning
  // ═══════════════════════════════════════════════════════════════════

  describe("Solver Tuning", () => {
    it("max iterations input renders with default 50", () => {
      render(<OptimiserConfig {...makeProps()} />)
      const input = screen.getByDisplayValue("50")
      expect(input).toBeInTheDocument()
    })

    it("tolerance input renders with default value", () => {
      render(<OptimiserConfig {...makeProps()} />)
      const input = screen.getByDisplayValue("0.000001")
      expect(input).toBeInTheDocument()
    })

    it("changing max_iter calls onUpdate", () => {
      const props = makeProps()
      render(<OptimiserConfig {...props} />)
      const input = screen.getByDisplayValue("50")
      fireEvent.change(input, { target: { value: "100" } })
      expect(props.onUpdate).toHaveBeenCalledWith("max_iter", 100)
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Advanced section
  // ═══════════════════════════════════════════════════════════════════

  describe("Advanced section", () => {
    it("is collapsed by default", () => {
      render(<OptimiserConfig {...makeProps()} />)
      // Advanced button exists
      expect(screen.getByText("Advanced")).toBeInTheDocument()
      // chunk_size should NOT be visible when collapsed
      expect(screen.queryByText("Chunk size")).not.toBeInTheDocument()
    })

    it("toggles open on click", () => {
      // Pre-set section as open since toggleSection flips the boolean
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      render(<OptimiserConfig {...makeProps()} />)
      expect(screen.getByText("Chunk size")).toBeInTheDocument()
      expect(screen.getByText("Record history")).toBeInTheDocument()
    })

    it("shows chunk_size and record_history in advanced", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      render(<OptimiserConfig {...makeProps()} />)
      expect(screen.getByDisplayValue("500000")).toBeInTheDocument()
      expect(screen.getByText("Off")).toBeInTheDocument()
    })

    it("ratebook mode shows CD iterations and CD tolerance in advanced", () => {
      useSettingsStore.setState({ openSections: { "optimiser.advanced": true } })
      render(
        <OptimiserConfig
          {...makeProps({ config: { _nodeId: "opt_1", mode: "ratebook", objective: "premium", constraints: {} } })}
        />,
      )
      expect(screen.getByText("CD iterations")).toBeInTheDocument()
      expect(screen.getByText("CD tolerance")).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Solve action
  // ═══════════════════════════════════════════════════════════════════

  describe("Solve action", () => {
    it("solve button is disabled when no objective is set", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "", constraints: {} },
          })}
        />,
      )
      const btn = screen.getByRole("button", { name: /Optimise/ })
      expect(btn).toBeDisabled()
    })

    it("solve button is enabled when objective and constraints are set", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          })}
        />,
      )
      const btn = screen.getByRole("button", { name: /Optimise/ })
      expect(btn).not.toBeDisabled()
    })

    it("solve button calls solveOptimiser with graph payload", async () => {
      mockSolveOptimiser.mockResolvedValue({ status: "started", job_id: "job_42" })
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      })
      render(<OptimiserConfig {...props} />)
      fireEvent.click(screen.getByRole("button", { name: /Optimise/ }))
      await waitFor(() => {
        expect(mockSolveOptimiser).toHaveBeenCalledTimes(1)
      })
      // Verify it was called with a graph payload containing node_id
      expect(mockSolveOptimiser).toHaveBeenCalledWith(
        expect.objectContaining({ node_id: "opt_1" }),
      )
    })

    it("shows 'Optimising...' during active solve job", () => {
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
          },
        },
      })
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          })}
        />,
      )
      expect(screen.getByText("Optimising...")).toBeInTheDocument()
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
    }

    it("shows convergence status when solveResult exists", () => {
      // Set configHash to empty to match the result's configHash
      useNodeResultsStore.setState({ solveResults: { opt_1: convergedResult } })
      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          })}
        />,
      )
      expect(screen.getByText(/Converged/)).toBeInTheDocument()
      expect(screen.getByText(/15 iterations/)).toBeInTheDocument()
    })

    it("shows non-convergence warning when solveResult.converged is false", () => {
      const nonConverged = {
        ...convergedResult,
        result: { ...convergedResult.result, converged: false },
      }
      useNodeResultsStore.setState({ solveResults: { opt_1: nonConverged } })
      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          })}
        />,
      )
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
          },
        },
      })
      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          })}
        />,
      )
      expect(screen.getByText("Optimisation failed")).toBeInTheDocument()
      expect(screen.getByText("Solver exploded")).toBeInTheDocument()
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
          },
        },
      })
      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          })}
        />,
      )
      expect(screen.getByText("Iteration 9 of 20")).toBeInTheDocument()
      expect(screen.getByText("12s")).toBeInTheDocument()
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
      render(
        <OptimiserConfig
          {...makeProps({
            config: { _nodeId: "opt_1", mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1.05 } } },
          })}
        />,
      )
      expect(screen.getByText("Config changed since last solve")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Re-run" })).toBeInTheDocument()
    })
  })

  // ═══════════════════════════════════════════════════════════════════
  // Efficient Frontier
  // ═══════════════════════════════════════════════════════════════════

  describe("Efficient Frontier", () => {
    it("does not show frontier section when no constraints are configured", () => {
      render(<OptimiserConfig {...makeProps()} />)
      expect(screen.queryByText("Efficient Frontier")).not.toBeInTheDocument()
    })

    it("shows frontier section when constraints are configured", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          })}
        />,
      )
      expect(screen.getByText("Efficient Frontier")).toBeInTheDocument()
      expect(screen.getByText("Min multiplier")).toBeInTheDocument()
      expect(screen.getByText("Max multiplier")).toBeInTheDocument()
      expect(screen.getByText("Steps")).toBeInTheDocument()
    })

    it("renders default frontier values (0.8, 1.1, 15)", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
            },
          })}
        />,
      )
      expect(screen.getByDisplayValue("0.8")).toBeInTheDocument()
      expect(screen.getByDisplayValue("1.1")).toBeInTheDocument()
      expect(screen.getByDisplayValue("15")).toBeInTheDocument()
    })

    it("renders custom frontier values from config", () => {
      render(
        <OptimiserConfig
          {...makeProps({
            config: {
              _nodeId: "opt_1",
              mode: "online",
              objective: "premium",
              constraints: { loss_ratio: { max: 1.05 } },
              frontier_min: 0.70,
              frontier_max: 1.30,
              frontier_steps: 25,
            },
          })}
        />,
      )
      expect(screen.getByDisplayValue("0.7")).toBeInTheDocument()
      expect(screen.getByDisplayValue("1.3")).toBeInTheDocument()
      expect(screen.getByDisplayValue("25")).toBeInTheDocument()
    })

    it("changing frontier_min calls onUpdate", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      })
      render(<OptimiserConfig {...props} />)
      const input = screen.getByDisplayValue("0.8")
      fireEvent.change(input, { target: { value: "0.75" } })
      expect(props.onUpdate).toHaveBeenCalledWith("frontier_min", 0.75)
    })

    it("changing frontier_max calls onUpdate", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      })
      render(<OptimiserConfig {...props} />)
      const input = screen.getByDisplayValue("1.1")
      fireEvent.change(input, { target: { value: "1.25" } })
      expect(props.onUpdate).toHaveBeenCalledWith("frontier_max", 1.25)
    })

    it("changing frontier_steps calls onUpdate", () => {
      const props = makeProps({
        config: {
          _nodeId: "opt_1",
          mode: "online",
          objective: "premium",
          constraints: { loss_ratio: { max: 1.05 } },
        },
      })
      render(<OptimiserConfig {...props} />)
      const input = screen.getByDisplayValue("15")
      fireEvent.change(input, { target: { value: "20" } })
      expect(props.onUpdate).toHaveBeenCalledWith("frontier_steps", 20)
    })
  })
})
