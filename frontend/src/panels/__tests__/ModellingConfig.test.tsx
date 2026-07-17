import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react"
import ModellingConfig from "../ModellingConfig"
import { GraphProvider } from "../GraphContext"
import useNodeResultsStore, { hashConfig } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import useToastStore from "../../stores/useToastStore"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import type { SimpleNode, SimpleEdge } from "../editors"
import { makeExecutionMetricsFixture } from "../../testSupport/executionMetricsFixture"

// ── Mocks ────────────────────────────────────────────────────────

const mockTrainModel = vi.fn()
const mockEstimateTrainingRam = vi.fn()

vi.mock("../../api/client", () => ({
  trainModel: (...args: unknown[]) => mockTrainModel(...args),
  estimateTrainingRam: (...args: unknown[]) => mockEstimateTrainingRam(...args),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

// Mock child components that are already well-tested
vi.mock("../modelling/TrainingProgress", () => ({
  TrainingProgress: () => <div data-testid="training-progress" />,
}))

// ── Helpers ──────────────────────────────────────────────────────

const defaultColumns = [
  { name: "loss_ratio", dtype: "Float64" },
  { name: "age", dtype: "Int64" },
  { name: "region", dtype: "String" },
  { name: "exposure", dtype: "Float64" },
]

type ConfigOverrides = Partial<Parameters<typeof ModellingConfig>[0]> & {
  allNodes?: SimpleNode[]
  edges?: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

function defaultProps(overrides: ConfigOverrides = {}) {
  // Strip graph-context keys — they flow via `<GraphProvider>` in tests, not props.
  const { allNodes, edges, submodels, preamble, ...rest } = overrides
  void allNodes; void edges; void submodels; void preamble
  return {
    // loss_function is set by default: the Train button is gated on an
    // explicit training objective (backend rejects an unset one).
    config: {
      _nodeId: "node_1",
      target: "loss_ratio",
      task: "regression",
      algorithm: "catboost",
      loss_function: "RMSE",
    },
    onUpdate: vi.fn(),
    upstreamColumns: defaultColumns,
    ...rest,
  }
}

function renderConfig(overrides: ConfigOverrides = {}) {
  const { allNodes = [], edges = [], submodels, preamble } = overrides
  const props = defaultProps(overrides)
  const result = render(
    <GraphProvider allNodes={allNodes} edges={edges} submodels={submodels} preamble={preamble}>
      <ModellingConfig {...props} />
    </GraphProvider>,
  )
  return { ...result, props }
}

function makeTrainResult(overrides: Partial<TrainResult> = {}): TrainResult {
  return {
    status: "ok",
    metrics: { gini: 0.45, rmse: 0.12 },
    feature_importance: [
      { feature: "age", importance: 0.6 },
      { feature: "region", importance: 0.4 },
    ],
    model_path: "/models/catboost_model.cbm",
    train_rows: 8000,
    test_rows: 2000,
    ...overrides,
  }
}

// ── Setup / teardown ─────────────────────────────────────────────

beforeEach(() => {
  useNodeResultsStore.setState({
    trainJobs: {},
    trainResults: {},
  })
  useSettingsStore.setState({
    mlflow: {
      status: "pending",
      backend: "",
      host: "",
      installed: null,
      importable: null,
      trackingConfigured: null,
      detail: "",
    },
    openSections: {},
  })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
  mockTrainModel.mockReset()
  // Return a never-resolving promise by default so the useEffect doesn't cause
  // act() warnings from resolved promises after unmount.
  mockEstimateTrainingRam.mockReset().mockReturnValue(new Promise(() => {}))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ═════════════════════════════════════════════════════════════════
// Config rendering
// ═════════════════════════════════════════════════════════════════

describe("ModellingConfig", () => {
  describe("Config rendering", () => {
    it("renders target column dropdown with upstream columns", () => {
      renderConfig()
      // Target select: the label says "Target column", find the select within that section
      const targetLabel = screen.getByText("Target column")
      const targetSection = targetLabel.closest("div")!
      const targetSelect = targetSection.querySelector("select")!
      expect(targetSelect.value).toBe("loss_ratio")
      const options = within(targetSelect).getAllByRole("option")
      // 1 placeholder + 4 columns
      expect(options).toHaveLength(5)
      expect(options.map((o) => o.textContent)).toContain("loss_ratio (Float64)")
      expect(options.map((o) => o.textContent)).toContain("age (Int64)")
    })

    it("renders weight column dropdown with 'None' default", () => {
      renderConfig()
      // Weight defaults to "" which is the "None" option
      const weightSelect = screen.getAllByDisplayValue("None")[0]
      expect(weightSelect).toBeTruthy()
    })

    it("renders task toggle with regression active by default", () => {
      renderConfig()
      const regressionBtn = screen.getByRole("button", { name: "regression" })
      const classificationBtn = screen.getByRole("button", { name: "classification" })
      expect(regressionBtn).toBeTruthy()
      expect(classificationBtn).toBeTruthy()
    })

    it("switching task to classification calls onUpdate with new task, metrics, and clears loss", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "classification" }))
      // Should call onUpdate once with merged object
      expect(props.onUpdate).toHaveBeenCalledWith({
        task: "classification",
        metrics: ["auc", "logloss"],
        loss_function: null,
      })
    })

    it("feature count shows correct number (excludes target and weight)", () => {
      renderConfig()
      // 4 columns total. Target=loss_ratio excluded, weight="" so not excluded.
      // Feature columns: age, region, exposure = 3 of 4
      expect(screen.getByText(/3 of 4/)).toBeTruthy()
    })

    it("feature count adjusts when weight is set", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", weight: "exposure" },
      })
      // Target=loss_ratio, weight=exposure both excluded. Features: age, region = 2 of 4
      expect(screen.getByText(/2 of 4/)).toBeTruthy()
    })

    it("exclude column toggles work", () => {
      const { props } = renderConfig()
      // Expand features section first
      fireEvent.click(screen.getByRole("button", { name: /Features/ }))
      // Find the feature row span for "age" (not <option> elements)
      const ageSpan = screen.getAllByText("age").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(ageSpan.closest("div")!).getByRole("button", { name: "Exclude" }))
      expect(props.onUpdate).toHaveBeenCalledWith("exclude", ["age"])
    })

    it("excluded column re-includes on second click", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age"] },
      })
      // Expand features section first
      fireEvent.click(screen.getByRole("button", { name: /Features/ }))
      // Find the feature row span for "age" (not <option> elements)
      const ageSpan = screen.getAllByText("age").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(ageSpan.closest("div")!).getByRole("button", { name: "Include" }))
      // Should remove "age" from exclusion list
      expect(props.onUpdate).toHaveBeenCalledWith("exclude", [])
    })

    it("shows algorithm picker when algorithm is not set", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      expect(screen.getByText("Select Algorithm")).toBeTruthy()
      expect(screen.getByText("CatBoost")).toBeTruthy()
    })

    it("clicking CatBoost in picker sets algorithm and shows full config", () => {
      const { props } = renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      fireEvent.click(screen.getByText("CatBoost"))
      expect(props.onUpdate).toHaveBeenCalledWith("algorithm", "catboost")
    })

    it("loss function shows regression losses as toggle buttons for regression task", () => {
      renderConfig()
      // RMSE/MAE appear as both loss and metric buttons
      expect(screen.getAllByRole("button", { name: "RMSE" }).length).toBeGreaterThanOrEqual(1)
      expect(screen.getAllByRole("button", { name: "MAE" }).length).toBeGreaterThanOrEqual(1)
      // Poisson/Tweedie are loss-only
      expect(screen.getByRole("button", { name: "Poisson" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "Tweedie" })).toBeTruthy()
      // Should NOT contain classification losses
      expect(screen.queryByRole("button", { name: "CrossEntropy" })).toBeNull()
    })

    it("loss function shows classification losses when task=classification", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "classification", algorithm: "catboost" },
      })
      // Logloss appears as both loss and metric button
      expect(screen.getAllByRole("button", { name: "Logloss" }).length).toBeGreaterThanOrEqual(1)
      expect(screen.getByRole("button", { name: "CrossEntropy" })).toBeTruthy()
      // Should NOT contain regression-only losses
      expect(screen.queryByRole("button", { name: "Poisson" })).toBeNull()
    })

    it("Tweedie variance power slider only visible when loss_function=Tweedie", () => {
      // Without Tweedie: no slider
      const { unmount } = render(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig {...defaultProps()} />
        </GraphProvider>,
      )
      expect(screen.queryByText(/Variance power/)).toBeNull()
      unmount()

      // With Tweedie: slider visible
      render(
        <GraphProvider allNodes={[]} edges={[]}>
          <ModellingConfig
            {...defaultProps({
              config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", loss_function: "Tweedie" },
            })}
          />
        </GraphProvider>,
      )
      expect(screen.getByText(/Variance power/)).toBeTruthy()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Hyperparameter JSON editor
  // ═════════════════════════════════════════════════════════════════

  describe("Hyperparameter JSON editor", () => {
    it("renders Hyperparameters section with JSON textarea", () => {
      renderConfig()
      expect(screen.getByText("Hyperparameters")).toBeTruthy()
      // Should have a textarea with default params as JSON
      const textareas = document.querySelectorAll("textarea")
      expect(textareas.length).toBeGreaterThan(0)
    })

    it("textarea shows default params when config.params is empty", () => {
      renderConfig()
      const textarea = document.querySelector("textarea")!
      const parsed = JSON.parse(textarea.value)
      expect(parsed).toHaveProperty("iterations", 1000)
      expect(parsed).toHaveProperty("learning_rate", 0.05)
      expect(parsed).toHaveProperty("depth", 6)
    })

    it("textarea shows custom params from config", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, depth: 8 } },
      })
      const textarea = document.querySelector("textarea")!
      const parsed = JSON.parse(textarea.value)
      expect(parsed).toEqual({ iterations: 500, depth: 8 })
    })

    it("editing textarea and blurring commits params", () => {
      const { props } = renderConfig()
      const textarea = document.querySelector("textarea")!
      fireEvent.change(textarea, { target: { value: '{"iterations": 2000}' } })
      fireEvent.blur(textarea)
      expect(props.onUpdate).toHaveBeenCalledWith("params", { iterations: 2000 })
    })

    it("invalid JSON shows error and does not commit", () => {
      const { props } = renderConfig()
      const textarea = document.querySelector("textarea")!
      fireEvent.change(textarea, { target: { value: "{bad json" } })
      fireEvent.blur(textarea)
      // Error message text varies by JS engine — just check the border uses the danger token.
      expect(textarea.style.border).toContain("var(--danger)")
      // onUpdate should not have been called with params
      expect(props.onUpdate).not.toHaveBeenCalledWith("params", expect.anything())
    })

    it("strips task_type from JSON display when GPU is enabled", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, task_type: "GPU" } },
      })
      const textarea = document.querySelector("textarea")!
      const parsed = JSON.parse(textarea.value)
      expect(parsed).not.toHaveProperty("task_type")
      expect(parsed).toEqual({ iterations: 500 })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Split/Eval section
  // ═════════════════════════════════════════════════════════════════

  describe("Split/Eval section", () => {
    it("renders split strategy buttons (random, temporal, group)", () => {
      renderConfig()
      expect(screen.getByRole("button", { name: "random" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "temporal" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "group" })).toBeTruthy()
    })

    it("random split shows validation, holdout, and seed inputs", () => {
      renderConfig()
      expect(screen.getByText("Validation")).toBeTruthy()
      expect(screen.getByText("Holdout")).toBeTruthy()
      expect(screen.getByText("Seed")).toBeTruthy()
      expect(screen.getByDisplayValue("0.2")).toBeTruthy()
      expect(screen.getByDisplayValue("42")).toBeTruthy()
    })

    it("changing split strategy to temporal calls handleSplitUpdate", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "temporal" }))
      expect(props.onUpdate).toHaveBeenCalledWith("split", expect.objectContaining({ strategy: "temporal" }))
    })

    it("temporal split shows date column and cutoff date", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          split: { strategy: "temporal", validation_size: 0.2, seed: 42 },
        },
      })
      expect(screen.getByText("Date column")).toBeTruthy()
      expect(screen.getByText("Cutoff date")).toBeTruthy()
    })

    it("group split shows group column and test size", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          split: { strategy: "group", validation_size: 0.2, seed: 42 },
        },
      })
      expect(screen.getByText("Group column")).toBeTruthy()
    })

    it("metrics checkboxes for regression render correctly", () => {
      renderConfig()
      // Regression metrics (display labels)
      expect(screen.getByRole("button", { name: "Gini" })).toBeTruthy()
      expect(screen.getByRole("button", { name: "R²" })).toBeTruthy()
      // RMSE and MAE appear twice (loss function + metric) — check both exist
      expect(screen.getAllByRole("button", { name: "RMSE" }).length).toBeGreaterThanOrEqual(2)
      expect(screen.getAllByRole("button", { name: "MAE" }).length).toBeGreaterThanOrEqual(2)
    })

    it("clicking a metric button toggles it", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", metrics: ["gini", "rmse"] },
      })
      // Click "MSE" metric to add it (only appears once — not a loss function)
      fireEvent.click(screen.getByRole("button", { name: "MSE" }))
      expect(props.onUpdate).toHaveBeenCalledWith("metrics", ["gini", "rmse", "mse"])
    })

    it("clicking a selected metric removes it", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", metrics: ["gini", "rmse"] },
      })
      // Click "Gini" to remove it (only appears once — not a loss function)
      fireEvent.click(screen.getByRole("button", { name: "Gini" }))
      expect(props.onUpdate).toHaveBeenCalledWith("metrics", ["rmse"])
    })

    it("classification task shows classification metrics", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "classification", algorithm: "catboost" },
      })
      expect(screen.getByRole("button", { name: "AUC" })).toBeTruthy()
      // Logloss appears twice (loss function + metric)
      expect(screen.getAllByRole("button", { name: "Logloss" }).length).toBeGreaterThanOrEqual(2)
      // Regression-only metrics should NOT be visible
      expect(screen.queryByRole("button", { name: "Gini" })).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Training actions
  // ═════════════════════════════════════════════════════════════════

  describe("Training actions", () => {
    it("train button calls trainModel API with graph and node_id", async () => {
      mockTrainModel.mockResolvedValue({ status: "started", job_id: "job_1" })
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => expect(mockTrainModel).toHaveBeenCalledTimes(1))
      const callArgs = mockTrainModel.mock.calls[0][0]
      expect(callArgs).toEqual(
        expect.objectContaining({
          graph: expect.any(Object),
          node_id: "node_1",
        }),
      )
    })

    it("train button is disabled when no target is set", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "", task: "regression", algorithm: "catboost" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", true)
    })

    it("train button is gated until a loss function is selected (catboost)", () => {
      // The backend rejects an unset training objective (it would otherwise
      // silently train under CatBoost's RMSE default) — the UI must not
      // submit one.
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", true)
      expect(screen.getByText(/loss function required before training/)).toBeTruthy()
    })

    it("train button is gated until a family is selected (glm)", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "glm" },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", true)
      expect(screen.getByText(/distribution family required before training/)).toBeTruthy()
    })

    it("train button stays gated on an empty factor set until All features (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", true)
      expect(screen.getByText(/factor selection required before training/)).toBeTruthy()
    })

    it("train button is gated on Tweedie without a variance power (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "tweedie",
          all_factors: true,
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", true)
      expect(screen.getByText(/Tweedie variance power required before training/)).toBeTruthy()
    })

    it("train button is gated on elastic-net without an L1 ratio (glm)", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
          all_factors: true,
          regularization: "elastic_net",
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", true)
      expect(screen.getByText(/elastic-net L1 ratio required before training/)).toBeTruthy()
    })

    it("train button enables once the objective is explicit", () => {
      renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "glm",
          family: "poisson",
          all_factors: true,
        },
      })
      const trainBtn = screen.getByRole("button", { name: /Train Model/ })
      expect(trainBtn).toHaveProperty("disabled", false)
      expect(screen.queryByText(/before training/)).toBeNull()
    })

    it("train button shows 'Training...' when job is active", () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: null,
            error: null,
            configHash: "abc",
          },
        },
      })
      renderConfig()
      expect(screen.getByRole("button", { name: /Training\.\.\./ })).toBeTruthy()
      expect(screen.getByRole("button", { name: /Training\.\.\./ })).toHaveProperty("disabled", true)
    })

    it("stores error result when trainModel throws", async () => {
      mockTrainModel.mockRejectedValue(new Error("Network fail"))
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => {
        const store = useNodeResultsStore.getState()
        const cached = store.trainResults.node_1
        expect(cached).toBeTruthy()
        expect(cached.result.status).toBe("error")
        expect(cached.result.error).toBe("Error: Network fail")
      })
    })

    it("preserves structured execution metrics when trainModel admission fails", async () => {
      const executionMetrics = makeExecutionMetricsFixture({
        profile: "training_prep",
        status: "memory_limited",
        terminal_reason: "memory_limited",
      })
      mockTrainModel.mockRejectedValue(Object.assign(new Error("HTTP 507"), {
        name: "ApiError",
        status: 507,
        detail: JSON.stringify({
          message: "Training rejected by admission control",
          terminal_reason: "memory_limited",
          execution_metrics: executionMetrics,
        }),
        rawDetail: {
          message: "Training rejected by admission control",
          terminal_reason: "memory_limited",
          execution_metrics: executionMetrics,
        },
      }))

      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))

      await waitFor(() => {
        const cached = useNodeResultsStore.getState().trainResults.node_1
        expect(cached?.result.error).toBe("Training rejected by admission control")
        expect(cached?.terminalStatus?.status).toBe("memory_limited")
        expect(cached?.terminalStatus?.terminal_reason).toBe("memory_limited")
        expect(cached?.terminalStatus?.execution_metrics).toBe(executionMetrics)
      })
    })

    it("stores synchronous result when trainModel returns non-started status", async () => {
      const result = makeTrainResult()
      mockTrainModel.mockResolvedValue(result)
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Train Model/ }))
      await waitFor(() => {
        const store = useNodeResultsStore.getState()
        const cached = store.trainResults.node_1
        expect(cached).toBeTruthy()
        expect(cached.result.status).toBe("ok")
      })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Staleness indicator
  // ═════════════════════════════════════════════════════════════════

  describe("Staleness indicator", () => {
    it("shows staleness warning when config hash changed after training", () => {
      // Put a cached result with a different config hash
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: "old_hash_that_wont_match",
          },
        },
      })
      renderConfig()
      expect(screen.getByText("Config changed since last training")).toBeTruthy()
      expect(screen.getByRole("button", { name: "Re-train" })).toBeTruthy()
    })

    it("does not show staleness warning when config hash matches", () => {
      const config = { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost" }
      const hash = hashConfig(config)

      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: hash,
            source: "live",
            structuralVersion: 0,
          },
        },
      })
      renderConfig({ config })
      expect(screen.queryByText("Config changed since last training")).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Training results
  // ═════════════════════════════════════════════════════════════════

  describe("Training results", () => {
    it("shows training progress panel when trainJob has progress", () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: {
              status: "running",
              progress: 0.5,
              message: "Training...",
              iteration: 50,
              total_iterations: 100,
              train_loss: { rmse: 0.1 },
              elapsed_seconds: 10,
            },
            error: null,
            configHash: "abc",
          },
        },
      })
      renderConfig()
      expect(screen.getByTestId("training-progress")).toBeTruthy()
    })

    it("shows error message when trainResult.status === 'error'", () => {
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult({ status: "error", error: "OOM: out of memory" }),
            jobId: "job_1",
            configHash: "irrelevant",
          },
        },
      })
      renderConfig()
      expect(screen.getByText("Training failed")).toBeTruthy()
      expect(screen.getByText("OOM: out of memory")).toBeTruthy()
    })

    it("shows completion badge when trainResult is successful and not training", () => {
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: "irrelevant",
          },
        },
      })
      renderConfig()
      expect(screen.getByText(/Model trained — results in preview panel below/)).toBeTruthy()
    })

    it("does not show completion badge when training is active", () => {
      useNodeResultsStore.setState({
        trainJobs: {
          node_1: {
            jobId: "job_1",
            nodeId: "node_1",
            nodeLabel: "Model",
            progress: null,
            error: null,
            configHash: "abc",
          },
        },
        trainResults: {
          node_1: {
            result: makeTrainResult(),
            jobId: "job_1",
            configHash: "abc",
          },
        },
      })
      renderConfig()
      expect(screen.queryByText(/Model trained — results in preview panel below/)).toBeNull()
    })

    it("does not show completion badge for error results", () => {
      useNodeResultsStore.setState({
        trainResults: {
          node_1: {
            result: makeTrainResult({ status: "error", error: "fail" }),
            jobId: "job_1",
            configHash: "irrelevant",
          },
        },
      })
      renderConfig()
      expect(screen.queryByText(/Model trained — results in preview panel below/)).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // RAM estimate
  // ═════════════════════════════════════════════════════════════════

  describe("RAM estimate", () => {
    it("calls estimateTrainingRam on mount", () => {
      renderConfig()
      expect(mockEstimateTrainingRam).toHaveBeenCalledTimes(1)
      const callArgs = mockEstimateTrainingRam.mock.calls[0][0]
      expect(callArgs).toEqual(
        expect.objectContaining({
          graph: expect.any(Object),
          node_id: "node_1",
        }),
      )
    })

    it("shows loading state while estimating", () => {
      // The mock returns a never-resolving promise, so loading persists
      renderConfig()
      expect(screen.getByText("Estimating dataset size...")).toBeTruthy()
    })

    it("shows RAM estimate data when resolved", async () => {
      // bytes_per_row=700 → 700 * 100k * 1.0 / 1024² ≈ 67 MB
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 67,
        available_mb: 8192,
        bytes_per_row: 700,
        was_downsampled: false,
        warning: null,
        gpu_vram_estimated_mb: null,
        gpu_vram_available_mb: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Dataset fits in memory")).toBeTruthy()
        expect(screen.getByText("100,000")).toBeTruthy()
        expect(screen.getByText("67 MB")).toBeTruthy()
      })
    })

    it("shows downsample warning when was_downsampled is true", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 5000000,
        safe_row_limit: 1000000,
        estimated_mb: 2500,
        training_mb: 10000,
        available_mb: 8192,
        bytes_per_row: 500,
        was_downsampled: true,
        warning: null,
        gpu_vram_estimated_mb: null,
        gpu_vram_available_mb: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Will downsample")).toBeTruthy()
        expect(screen.getByText("1,000,000")).toBeTruthy()
      })
    })

    it("shows GPU VRAM info when gpu fields present", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 200,
        available_mb: 8192,
        bytes_per_row: 500,
        was_downsampled: false,
        gpu_vram_estimated_mb: 512,
        gpu_vram_available_mb: 8192,
        warning: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Est. GPU VRAM")).toBeTruthy()
        expect(screen.getByText("512 MB")).toBeTruthy()
      })
    })

    it("shows GPU warning when estimated VRAM exceeds available", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 200,
        available_mb: 8192,
        bytes_per_row: 500,
        was_downsampled: false,
        gpu_vram_estimated_mb: 12000,
        gpu_vram_available_mb: 8192,
        warning: null,
        gpu_warning: "GPU training needs 12000 MB but GPU has 8192 MB",
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText(/GPU training needs.*but GPU has/)).toBeTruthy()
      })
    })

    it("shows inline warning and toast when RAM estimate fails", async () => {
      mockEstimateTrainingRam.mockRejectedValue(new Error("Network error"))
      renderConfig()
      // Wait for loading to finish and error state to propagate
      await waitFor(() => {
        // The toast store should have received the warning
        const toasts = useToastStore.getState().toasts
        expect(toasts.some((t) => t.text.includes("RAM estimate failed"))).toBe(true)
      })
      // Inline warning is shown
      expect(screen.getByText(/RAM estimate unavailable/)).toBeTruthy()
      // Verify toast content
      const toasts = useToastStore.getState().toasts
      const ramToast = toasts.find((t) => t.text.includes("RAM estimate failed"))!
      expect(ramToast.type).toBe("warning")
      expect(ramToast.text).toContain("Network error")
    })

    it("does not show inline warning when estimate succeeds", async () => {
      mockEstimateTrainingRam.mockResolvedValue({
        total_rows: 100000,
        safe_row_limit: null,
        estimated_mb: 50,
        training_mb: 200,
        available_mb: 8192,
        bytes_per_row: 700,
        was_downsampled: false,
        warning: null,
        gpu_vram_estimated_mb: null,
        gpu_vram_available_mb: null,
        gpu_warning: null,
      })
      renderConfig()
      await waitFor(() => {
        expect(screen.getByText("Dataset fits in memory")).toBeTruthy()
      })
      expect(screen.queryByText(/RAM estimate unavailable/)).toBeNull()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Collapsible sections
  // ═════════════════════════════════════════════════════════════════

  describe("Collapsible sections", () => {
    it("MLflow Logging section is collapsed by default", () => {
      renderConfig()
      const mlflowBtn = screen.getByRole("button", { name: /MLflow Logging/ })
      expect(mlflowBtn).toBeTruthy()
      // Experiment path input should not be visible
      expect(screen.queryByPlaceholderText("/Shared/haute/experiment")).toBeNull()
    })

    it("clicking MLflow Logging toggle shows experiment inputs", () => {
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /MLflow Logging/ }))
      expect(screen.getByPlaceholderText("/Shared/haute/experiment")).toBeTruthy()
    })

    it("Monotonic Constraints section is collapsed by default (when columns exist)", () => {
      renderConfig()
      const monoBtn = screen.getByRole("button", { name: /Monotonic Constraints/ })
      expect(monoBtn).toBeTruthy()
    })

    it("clicking Monotonic Constraints shows numeric feature constraints", () => {
      renderConfig()
      fireEvent.click(screen.getByRole("button", { name: /Monotonic Constraints/ }))
      // Should show constraint controls for numeric features (age, exposure)
      // but not for string features (region) or target (loss_ratio)
      // After toggle, we should see "age" and "exposure" but not "region" or "loss_ratio"
      expect(screen.getByText("Set per-feature constraints (numeric features only)")).toBeTruthy()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Edge cases
  // ═════════════════════════════════════════════════════════════════

  describe("Edge cases", () => {
    it("renders without upstream columns", () => {
      renderConfig({ upstreamColumns: undefined })
      // Should not crash, feature count section still renders
      expect(screen.getByText(/Features/)).toBeTruthy()
    })

    it("renders with empty columns array", () => {
      renderConfig({ upstreamColumns: [] })
      expect(screen.getByText(/Features/)).toBeTruthy()
    })

    it("GPU toggle enables GPU training", () => {
      const { props } = renderConfig()
      const gpuCheckbox = screen.getByRole("checkbox")
      fireEvent.click(gpuCheckbox)
      expect(props.onUpdate).toHaveBeenCalledWith("params", expect.objectContaining({ task_type: "GPU" }))
    })

    it("GPU unchecked removes task_type from params", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", params: { iterations: 500, task_type: "GPU" } },
      })
      const gpuCheckbox = screen.getByRole("checkbox")
      fireEvent.click(gpuCheckbox)
      // Should commit params without task_type
      expect(props.onUpdate).toHaveBeenCalledWith("params", { iterations: 500 })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Algorithm picker — GLM option
  // ═════════════════════════════════════════════════════════════════

  describe("Algorithm picker", () => {
    it("shows both CatBoost and GLM options when algorithm is not set", () => {
      renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      expect(screen.getByText("CatBoost")).toBeTruthy()
      expect(screen.getByText("GLM")).toBeTruthy()
    })

    it("clicking GLM in picker sets algorithm to glm", () => {
      const { props } = renderConfig({ config: { _nodeId: "node_1", target: "loss_ratio", task: "regression" } })
      fireEvent.click(screen.getByText("GLM"))
      expect(props.onUpdate).toHaveBeenCalledWith("algorithm", "glm")
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Task switching updates metrics
  // ═════════════════════════════════════════════════════════════════

  describe("Task switching metrics", () => {
    it("switching to classification sets classification metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "classification" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ task: "classification", metrics: ["auc", "logloss"] }),
      )
    })

    it("switching back to regression sets regression metrics", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "classification", algorithm: "catboost", metrics: ["auc", "logloss"] },
      })
      fireEvent.click(screen.getByRole("button", { name: "regression" }))
      expect(props.onUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ task: "regression", metrics: ["gini", "rmse"] }),
      )
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Loss function selection (regression)
  // ═════════════════════════════════════════════════════════════════

  describe("Loss function selection", () => {
    it("clicking Poisson sets loss_function and objective-matched metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Poisson" }))
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "Poisson",
        metrics: ["gini", "poisson_deviance"],
      })
    })

    it("clicking Tweedie sets loss_function and objective-matched metrics", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "Tweedie" }))
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "Tweedie",
        metrics: ["gini", "tweedie_deviance"],
      })
    })

    it("clicking RMSE loss button sets loss_function to RMSE", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          loss_function: "MAE",
        },
      })
      const rmseButtons = screen.getAllByRole("button", { name: "RMSE" })
      // Click the first RMSE button (the loss function one)
      fireEvent.click(rmseButtons[0])
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "RMSE",
        metrics: ["gini", "rmse"],
      })
    })

    it("clicking the selected loss deselects it (null)", () => {
      const { props } = renderConfig()
      const rmseButtons = screen.getAllByRole("button", { name: "RMSE" })
      fireEvent.click(rmseButtons[0])
      expect(props.onUpdate).toHaveBeenCalledWith("loss_function", null)
    })

    it("clicking MAE loss button sets loss_function to MAE", () => {
      const { props } = renderConfig()
      const maeButtons = screen.getAllByRole("button", { name: "MAE" })
      fireEvent.click(maeButtons[0])
      expect(props.onUpdate).toHaveBeenCalledWith({
        loss_function: "MAE",
        metrics: ["gini", "rmse"],
      })
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Row limit input
  // ═════════════════════════════════════════════════════════════════

  describe("Row limit input", () => {
    it("renders row limit input with placeholder", () => {
      renderConfig()
      expect(screen.getByPlaceholderText("All rows")).toBeTruthy()
    })

    it("changing row limit calls onUpdate with parsed integer", () => {
      const { props } = renderConfig()
      const rowLimitInput = screen.getByPlaceholderText("All rows")
      fireEvent.change(rowLimitInput, { target: { value: "50000" } })
      expect(props.onUpdate).toHaveBeenCalledWith("row_limit", 50000)
    })

    it("clearing row limit calls onUpdate with null", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", row_limit: 50000 },
      })
      const rowLimitInput = screen.getByDisplayValue("50000")
      fireEvent.change(rowLimitInput, { target: { value: "" } })
      expect(props.onUpdate).toHaveBeenCalledWith("row_limit", null)
    })

    it("shows row count label when row limit is set", () => {
      renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", row_limit: 100000 },
      })
      expect(screen.getByText("100,000 rows")).toBeTruthy()
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Feature exclude/include updates config
  // ═════════════════════════════════════════════════════════════════

  describe("Feature exclude/include updates config", () => {
    it("excluding multiple columns accumulates in exclude array", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age"] },
      })
      fireEvent.click(screen.getByRole("button", { name: /Features/ }))
      const regionSpan = screen.getAllByText("region").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(regionSpan.closest("div")!).getByRole("button", { name: "Exclude" }))
      expect(props.onUpdate).toHaveBeenCalledWith("exclude", ["age", "region"])
    })

    it("including a column from exclude list removes only that column", () => {
      const { props } = renderConfig({
        config: { _nodeId: "node_1", target: "loss_ratio", task: "regression", algorithm: "catboost", exclude: ["age", "region"] },
      })
      fireEvent.click(screen.getByRole("button", { name: /Features/ }))
      const regionSpan = screen.getAllByText("region").find(el => el.tagName === "SPAN")!
      fireEvent.click(within(regionSpan.closest("div")!).getByRole("button", { name: "Include" }))
      expect(props.onUpdate).toHaveBeenCalledWith("exclude", ["age"])
    })
  })

  // ═════════════════════════════════════════════════════════════════
  // Split strategy buttons
  // ═════════════════════════════════════════════════════════════════

  describe("Split strategy selection", () => {
    it("clicking group split calls onUpdate with group strategy", () => {
      const { props } = renderConfig()
      fireEvent.click(screen.getByRole("button", { name: "group" }))
      expect(props.onUpdate).toHaveBeenCalledWith("split", expect.objectContaining({ strategy: "group" }))
    })

    it("clicking random split after temporal reverts strategy", () => {
      const { props } = renderConfig({
        config: {
          _nodeId: "node_1",
          target: "loss_ratio",
          task: "regression",
          algorithm: "catboost",
          split: { strategy: "temporal", validation_size: 0.2, seed: 42 },
        },
      })
      fireEvent.click(screen.getByRole("button", { name: "random" }))
      expect(props.onUpdate).toHaveBeenCalledWith("split", expect.objectContaining({ strategy: "random" }))
    })
  })
})
