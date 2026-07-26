import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"

// ---------------------------------------------------------------------------
// Mocks — must be declared before importing the component under test
// ---------------------------------------------------------------------------

const mockMlflow = {
  experiments: [] as { experiment_id: string; name: string }[],
  runs: [] as {
    run_id: string
    run_name: string
    metrics: Record<string, number>
    params?: Record<string, string>
    artifacts: string[]
  }[],
  models: [] as { name: string; latest_versions: { version: string; status: string; run_id: string }[] }[],
  modelVersions: [] as {
    version: string
    run_id: string
    status: string
    description: string
    params?: Record<string, string>
  }[],
  modelVersionsFor: "",
  loadingExperiments: false,
  loadingRuns: false,
  loadingModels: false,
  loadingVersions: false,
  errorExperiments: "",
  errorRuns: "",
  errorModels: "",
  errorVersions: "",
  browseExpId: "",
  setBrowseExpId: vi.fn(),
  setRuns: vi.fn(),
  refreshExperiments: vi.fn(),
  refreshRuns: vi.fn(),
  refreshModels: vi.fn(),
  refreshVersions: vi.fn(),
  resetRunsGuard: vi.fn(),
}

vi.mock("../../../hooks/useMlflowBrowser", () => ({
  useMlflowBrowser: vi.fn(() => mockMlflow),
}))

vi.mock("../../../utils/configField", () => ({
  configField: (config: Record<string, unknown>, key: string, defaultVal: unknown) =>
    config[key] !== undefined ? config[key] : defaultVal,
}))

vi.mock("../_shared", async () => {
  const actual = await vi.importActual("../_shared")
  return {
    ...actual,
    InputSourcesBar: ({ inputSources }: { inputSources: unknown[] }) => (
      <div data-testid="input-sources">{inputSources.length}</div>
    ),
    MlflowStatusBadge: () => <div data-testid="mlflow-badge" />,
    CodeEditor: ({
      defaultValue,
      onChange,
    }: {
      defaultValue: string
      onChange: (v: string) => void
    }) => (
      <textarea
        data-testid="code-editor"
        defaultValue={defaultValue}
        onChange={(e) => onChange(e.target.value)}
      />
    ),
  }
})

import OptimiserApplyEditor from "../OptimiserApplyEditor"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultProps = () => ({
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  inputSources: [] as { sourceNodeId: string; name: string; sourceLabel: string; edgeId: string }[],
  accentColor: "var(--warning-strong)",
})

function resetMlflow() {
  mockMlflow.experiments = []
  mockMlflow.runs = []
  mockMlflow.models = []
  mockMlflow.modelVersions = []
  mockMlflow.modelVersionsFor = ""
  mockMlflow.loadingExperiments = false
  mockMlflow.loadingRuns = false
  mockMlflow.loadingModels = false
  mockMlflow.loadingVersions = false
  mockMlflow.errorExperiments = ""
  mockMlflow.errorRuns = ""
  mockMlflow.errorModels = ""
  mockMlflow.errorVersions = ""
  mockMlflow.browseExpId = ""
  mockMlflow.setBrowseExpId.mockClear()
  mockMlflow.setRuns.mockClear()
  mockMlflow.refreshExperiments.mockClear()
  mockMlflow.refreshRuns.mockClear()
  mockMlflow.refreshModels.mockClear()
  mockMlflow.refreshVersions.mockClear()
  mockMlflow.resetRunsGuard.mockClear()
}

// Mock global fetch for artifact metadata loading
const originalFetch = globalThis.fetch

function mockFetchResponse(data: unknown, ok = true, statusText = "OK") {
  ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
    ok,
    statusText,
    json: async () => data,
  })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("OptimiserApplyEditor", () => {
  beforeEach(() => {
    resetMlflow()
    globalThis.fetch = vi.fn()
  })

  afterEach(() => {
    cleanup()
    globalThis.fetch = originalFetch
  })

  // 1. Renders with default file source type
  it("renders with default file source type selected", () => {
    render(<OptimiserApplyEditor {...defaultProps()} />)
    const fileBtn = screen.getByText("File Path")
    const registeredBtn = screen.getByText("Registered")
    // Active button should have a visually distinct border from inactive button
    expect(fileBtn.style.border).not.toBe(registeredBtn.style.border)
  })

  // 2. Source type toggle among file/registered/run
  it("calls onUpdate when toggling source type to registered", () => {
    const props = defaultProps()
    render(<OptimiserApplyEditor {...props} />)
    fireEvent.click(screen.getByText("Registered"))
    expect(props.onUpdate).toHaveBeenCalledWith({ sourceType: "registered", optimiser_mode: "" })
  })

  it("calls onUpdate when toggling source type to run", () => {
    const props = defaultProps()
    render(<OptimiserApplyEditor {...props} />)
    fireEvent.click(screen.getByText("Experiment Run"))
    expect(props.onUpdate).toHaveBeenCalledWith({ sourceType: "run", optimiser_mode: "" })
  })

  // 3. File mode: shows artifact_path text input
  it("shows artifact_path text input in file mode", () => {
    render(<OptimiserApplyEditor {...defaultProps()} />)
    expect(screen.getByText("Artifact Path")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("artifacts/optimiser_v1.json")).toBeInTheDocument()
  })

  // 4. File mode: artifact_path change calls onUpdate
  it("artifact_path change calls onUpdate", () => {
    const props = defaultProps()
    render(<OptimiserApplyEditor {...props} />)
    const input = screen.getByPlaceholderText("artifacts/optimiser_v1.json")
    fireEvent.change(input, { target: { value: "output/result.json" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(props.onUpdate).toHaveBeenCalledWith("artifact_path", "output/result.json")
  })

  // 5. File mode: loads metadata when artifactPath is set
  it("loads metadata when artifactPath is set", async () => {
    const meta = {
      version: "v2",
      created_at: "2026-01-15T10:00:00Z",
      mode: "online",
      objective: "loss_ratio",
      lambdas: { age: 0.5 },
      constraints: {},
    }
    mockFetchResponse(meta)
    const props = defaultProps()
    props.config = { artifact_path: "artifacts/opt.json" }
    render(<OptimiserApplyEditor {...props} />)
    await waitFor(() => {
      expect(screen.getByText("Loaded Artifact")).toBeInTheDocument()
    })
    expect(screen.getByText("online")).toBeInTheDocument()
    expect(screen.getByText("v2")).toBeInTheDocument()
    expect(screen.getByText("loss_ratio")).toBeInTheDocument()
  })

  // 6. File mode: shows ArtifactMetaPanel with online mode lambdas
  it("shows lambdas in online mode ArtifactMetaPanel", async () => {
    const meta = {
      version: "v1",
      created_at: "2026-02-01T12:00:00Z",
      mode: "online",
      objective: "combined_loss",
      lambdas: { age_factor: 1.2345, region: 0.9876 },
      constraints: {},
    }
    mockFetchResponse(meta)
    const props = defaultProps()
    props.config = { artifact_path: "opt.json" }
    render(<OptimiserApplyEditor {...props} />)
    await waitFor(() => {
      expect(screen.getByText("Lambdas")).toBeInTheDocument()
    })
    expect(screen.getByText("age_factor")).toBeInTheDocument()
    expect(screen.getByText("1.2345")).toBeInTheDocument()
    expect(screen.getByText("region")).toBeInTheDocument()
    expect(screen.getByText("0.9876")).toBeInTheDocument()
  })

  // 7. File mode: shows ArtifactMetaPanel with ratebook mode factor_tables
  it("shows factor_tables in ratebook mode ArtifactMetaPanel", async () => {
    const meta = {
      version: "v3",
      created_at: "2026-03-01T09:00:00Z",
      mode: "ratebook",
      objective: "premium_vol",
      lambdas: {},
      constraints: {},
      factor_tables: {
        age_table: [{ level: "young" }, { level: "old" }],
        region_table: [{ level: "north" }, { level: "south" }, { level: "east" }],
      },
    }
    mockFetchResponse(meta)
    const props = defaultProps()
    props.config = { artifact_path: "ratebook.json" }
    render(<OptimiserApplyEditor {...props} />)
    await waitFor(() => {
      expect(screen.getByText("Factor Tables")).toBeInTheDocument()
    })
    expect(screen.getByText("age_table")).toBeInTheDocument()
    expect(screen.getByText("2 levels")).toBeInTheDocument()
    expect(screen.getByText("region_table")).toBeInTheDocument()
    expect(screen.getByText("3 levels")).toBeInTheDocument()
  })

  // 8. File mode: shows load error when fetch fails
  it("shows load error when fetch throws", async () => {
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("Network failure"))
    const props = defaultProps()
    props.config = { artifact_path: "bad_path.json" }
    render(<OptimiserApplyEditor {...props} />)
    await waitFor(() => {
      expect(screen.getByText("Could not load artifact file")).toBeInTheDocument()
    })
  })

  // 9. File mode: shows load error on non-ok response
  it("shows load error on non-ok HTTP response", async () => {
    mockFetchResponse(null, false, "Not Found")
    const props = defaultProps()
    props.config = { artifact_path: "missing.json" }
    render(<OptimiserApplyEditor {...props} />)
    await waitFor(() => {
      expect(screen.getByText("Could not load artifact file")).toBeInTheDocument()
    })
  })

  // 10. Registered mode: shows model dropdown and MlflowStatusBadge
  it("shows model dropdown in registered mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "registered" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Model Name")).toBeInTheDocument()
    expect(screen.getByText("Select a model...")).toBeInTheDocument()
    expect(screen.getByTestId("mlflow-badge")).toBeInTheDocument()
  })

  // 11. Registered mode: model selection calls onUpdate
  it("model selection calls onUpdate with registered_model and version", () => {
    mockMlflow.models = [
      { name: "opt-model", latest_versions: [{ version: "1", status: "READY", run_id: "r1" }] },
    ]
    const props = defaultProps()
    props.config = { sourceType: "registered" }
    render(<OptimiserApplyEditor {...props} />)
    const modelSelect = screen.getByDisplayValue("Select a model...")
    fireEvent.change(modelSelect, { target: { value: "opt-model" } })
    expect(props.onUpdate).toHaveBeenCalledWith({ registered_model: "opt-model", version: "latest" })
  })

  // 12. Registered mode: version dropdown appears when model selected
  it("shows version dropdown when model is selected in registered mode", () => {
    mockMlflow.modelVersionsFor = "opt-model"
    mockMlflow.modelVersions = [
      { version: "2", run_id: "r2", status: "READY", description: "stable" },
    ]
    const props = defaultProps()
    props.config = { sourceType: "registered", registered_model: "opt-model" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Version")).toBeInTheDocument()
    expect(screen.getByText(/v2 — READY \(stable\)/)).toBeInTheDocument()
  })

  // 13. Run mode: shows experiment/run dropdowns
  it("shows experiment dropdown in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Experiment")).toBeInTheDocument()
    expect(screen.getByText("Select an experiment...")).toBeInTheDocument()
  })

  // 14. Run mode: experiment change triggers cascading updates
  it("experiment change calls cascading updates in run mode", () => {
    mockMlflow.experiments = [
      { experiment_id: "exp-opt-1", name: "Optimiser Exp" },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<OptimiserApplyEditor {...props} />)
    const expSelect = screen.getByDisplayValue("Select an experiment...")
    fireEvent.change(expSelect, { target: { value: "exp-opt-1" } })
    expect(mockMlflow.setBrowseExpId).toHaveBeenCalledWith("exp-opt-1")
    expect(props.onUpdate).toHaveBeenCalledWith({
      experiment_id: "exp-opt-1",
      experiment_name: "Optimiser Exp",
    })
    expect(mockMlflow.setRuns).toHaveBeenCalledWith([])
    expect(mockMlflow.resetRunsGuard).toHaveBeenCalled()
    expect(mockMlflow.refreshRuns).toHaveBeenCalledWith("exp-opt-1")
  })

  // 15. Version column input present and calls onUpdate
  it("version column input is present and calls onUpdate", () => {
    const props = defaultProps()
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Version Column")).toBeInTheDocument()
    const colInput = screen.getByDisplayValue("__optimiser_version__")
    fireEvent.change(colInput, { target: { value: "my_version" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(colInput)
    expect(props.onUpdate).toHaveBeenCalledWith("version_column", "my_version")
  })

  it("uses configured optimised value column and calls onUpdate", () => {
    const props = defaultProps()
    props.config = { optimised_value_column: "selected_price_factor" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Optimised Value Column")).toBeInTheDocument()
    const input = screen.getByDisplayValue("selected_price_factor")
    fireEvent.change(input, { target: { value: "final_price_factor" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(input)
    expect(props.onUpdate).toHaveBeenCalledWith("optimised_value_column", "final_price_factor")
  })

  it("optimised value column input is present and calls onUpdate", () => {
    const props = defaultProps()
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Optimised Value Column")).toBeInTheDocument()
    const colInput = screen.getByPlaceholderText("optimised_value")
    expect(colInput).toHaveValue("")
    fireEvent.change(colInput, { target: { value: "selected_factor" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(colInput)
    expect(props.onUpdate).toHaveBeenCalledWith("optimised_value_column", "selected_factor")
  })

  // 16. MlflowStatusBadge hidden in file mode
  it("MlflowStatusBadge is hidden in file mode", () => {
    render(<OptimiserApplyEditor {...defaultProps()} />)
    expect(screen.queryByTestId("mlflow-badge")).not.toBeInTheDocument()
  })

  // 17. MlflowStatusBadge shown in registered mode
  it("MlflowStatusBadge is shown in registered mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "registered" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByTestId("mlflow-badge")).toBeInTheDocument()
  })

  it("MlflowStatusBadge is shown in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByTestId("mlflow-badge")).toBeInTheDocument()
  })

  // 18. Empty artifact_path clears meta and error
  it("empty artifact_path does not fetch and shows no meta panel", () => {
    const props = defaultProps()
    props.config = { artifact_path: "" }
    render(<OptimiserApplyEditor {...props} />)
    expect(globalThis.fetch).not.toHaveBeenCalled()
    expect(screen.queryByText("Loaded Artifact")).not.toBeInTheDocument()
  })

  // Additional edge cases

  it("does not show artifact_path input in registered mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "registered" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.queryByPlaceholderText("artifacts/optimiser_v1.json")).not.toBeInTheDocument()
  })

  it("shows Run ID text input in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Run ID")).toBeInTheDocument()
    const runIdInput = screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")
    fireEvent.change(runIdInput, { target: { value: "xyz789" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(runIdInput)
    expect(props.onUpdate).toHaveBeenCalledWith("run_id", "xyz789")
  })

  it("renders InputSourcesBar with input sources", () => {
    const props = defaultProps()
    props.inputSources = [
      { sourceNodeId: "scored-data", name: "df", sourceLabel: "scored_data", edgeId: "e1" },
    ]
    render(<OptimiserApplyEditor {...props} />)
    const bar = screen.getByTestId("input-sources")
    expect(bar).toBeInTheDocument()
    expect(bar.textContent).toBe("1")
  })

  it("shows ratebook input selector for ratebook file artifacts", async () => {
    const meta = {
      version: "v3",
      created_at: "2026-03-01T09:00:00Z",
      mode: "ratebook",
      objective: "premium_vol",
      lambdas: {},
      constraints: {},
      factor_tables: {
        age_table: [{ level: "young" }],
      },
    }
    mockFetchResponse(meta)
    const props = defaultProps()
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]
    props.config = { artifact_path: "ratebook.json", ratebook_input: "age-banding-node" }
    render(<OptimiserApplyEditor {...props} />)

    const select = await screen.findByLabelText("Ratebook Input")
    expect(select).toHaveValue("age-banding-node")
    fireEvent.change(select, { target: { value: "optimiser-input-node" } })
    expect(props.onUpdate).toHaveBeenCalledWith("ratebook_input", "optimiser-input-node")
  })

  it("hides ratebook input selector for online file artifacts", async () => {
    const meta = {
      version: "v1",
      created_at: "2026-02-01T12:00:00Z",
      mode: "online",
      objective: "combined_loss",
      lambdas: { age_factor: 1.2345 },
      constraints: {},
    }
    mockFetchResponse(meta)
    const props = defaultProps()
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]
    props.config = { artifact_path: "online.json", ratebook_input: "age-banding-node" }
    render(<OptimiserApplyEditor {...props} />)

    await screen.findByText("Loaded Artifact")
    expect(screen.queryByLabelText("Ratebook Input")).not.toBeInTheDocument()
  })

  it("hides ratebook input selector while replacement file artifact metadata is loading", async () => {
    const ratebookMeta = {
      version: "v3",
      created_at: "2026-03-01T09:00:00Z",
      mode: "ratebook",
      objective: "premium_vol",
      lambdas: {},
      constraints: {},
      factor_tables: {
        age_table: [{ level: "young" }],
      },
    }
    mockFetchResponse(ratebookMeta)
    const props = defaultProps()
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]
    props.config = { artifact_path: "ratebook.json", ratebook_input: "age-banding-node" }
    const { rerender } = render(<OptimiserApplyEditor {...props} />)

    expect(await screen.findByLabelText("Ratebook Input")).toBeInTheDocument()

    // Use a deferred promise so we can resolve it explicitly during cleanup
    // rather than leaving an unresolved Promise referenced by the component.
    let resolvePending: (value: Response) => void = () => {}
    const pending = new Promise<Response>((resolve) => {
      resolvePending = resolve
    })
    ;(globalThis.fetch as ReturnType<typeof vi.fn>).mockReturnValueOnce(pending)
    props.config = { artifact_path: "online.json", ratebook_input: "age-banding-node" }
    rerender(<OptimiserApplyEditor {...props} />)

    expect(screen.queryByLabelText("Ratebook Input")).not.toBeInTheDocument()

    resolvePending(new Response("{}", { status: 200 }))
  })

  it("shows stale ratebook input selection for ratebook file artifacts", async () => {
    const ratebookMeta = {
      version: "v3",
      created_at: "2026-03-01T09:00:00Z",
      mode: "ratebook",
      objective: "premium_vol",
      lambdas: {},
      constraints: {},
      factor_tables: {
        age_table: [{ level: "young" }],
      },
    }
    const props = defaultProps()
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
    ]
    props.config = { artifact_path: "ratebook.json", ratebook_input: "deleted-banding-node" }
    mockFetchResponse(ratebookMeta)
    render(<OptimiserApplyEditor {...props} />)

    const select = await screen.findByLabelText("Ratebook Input")
    expect(select).toHaveValue("deleted-banding-node")
    expect(screen.getByRole("option", { name: "Missing input (deleted-banding-node)" })).toBeInTheDocument()
    fireEvent.change(select, { target: { value: "" } })
    expect(props.onUpdate).toHaveBeenCalledWith("ratebook_input", "")
  })

  it("hides stale ratebook input selection for online file artifacts", async () => {
    const onlineMeta = {
      version: "v3",
      created_at: "2026-03-01T09:00:00Z",
      mode: "online",
      objective: "premium_vol",
      lambdas: {},
      constraints: {},
    }
    mockFetchResponse(onlineMeta)
    const props = defaultProps()
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
    ]
    props.config = { artifact_path: "online.json", ratebook_input: "deleted-banding-node" }
    render(<OptimiserApplyEditor {...props} />)

    await screen.findByText("Loaded Artifact")
    expect(screen.queryByRole("option", { name: "Missing input (deleted-banding-node)" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Ratebook Input")).not.toBeInTheDocument()
  })

  it("shows run dropdown when experiment is selected in run mode", () => {
    mockMlflow.browseExpId = "exp-1"
    mockMlflow.runs = [
      { run_id: "run-1", run_name: "opt-run", metrics: { converged: 1, total_objective: 3.14 }, artifacts: [] },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<OptimiserApplyEditor {...props} />)
    expect(screen.getByText("Run")).toBeInTheDocument()
    // The run option should contain the run name and mode
    const runOptions = screen.getAllByRole("option")
    const optionTexts = runOptions.map((o) => o.textContent)
    expect(optionTexts.some((t) => t?.includes("opt-run"))).toBe(true)
  })

  it("uses MLflow run mode params when deciding if selected runs are ratebook", () => {
    mockMlflow.browseExpId = "exp-1"
    mockMlflow.runs = [
      {
        run_id: "frontier-ratebook-run",
        run_name: "frontier ratebook opt",
        metrics: { converged: 1, total_objective: 3.14 },
        params: { mode: "ratebook" },
        artifacts: [],
      },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run", run_id: "frontier-ratebook-run" }
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]
    render(<OptimiserApplyEditor {...props} />)

    expect(screen.getByRole("option", { name: /frontier ratebook opt \[ratebook\]/ })).toBeInTheDocument()
    expect(screen.getByLabelText("Ratebook Input")).toBeInTheDocument()
  })

  it("clears stale ratebook input when the selected run is online", async () => {
    mockMlflow.browseExpId = "exp-1"
    mockMlflow.runs = [
      {
        run_id: "online-run",
        run_name: "online opt",
        metrics: { converged: 1, total_objective: 3.14 },
        params: { mode: "online" },
        artifacts: [],
      },
    ]
    const props = defaultProps()
    props.config = {
      sourceType: "run",
      run_id: "online-run",
      optimiser_mode: "ratebook",
      ratebook_input: "age-banding-node",
    }
    render(<OptimiserApplyEditor {...props} />)

    await waitFor(() => {
      expect(props.onUpdate).toHaveBeenCalledWith({
        optimiser_mode: "online",
        ratebook_input: "",
      })
    })
  })

  it("shows ratebook input selector for selected ratebook registered model versions", () => {
    mockMlflow.modelVersionsFor = "registered-opt"
    mockMlflow.modelVersions = [
      {
        version: "2",
        run_id: "registered-ratebook-run",
        status: "READY",
        description: "ratebook",
        params: { mode: "ratebook" },
      },
    ]
    const props = defaultProps()
    props.config = { sourceType: "registered", registered_model: "registered-opt", version: "2" }
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]
    render(<OptimiserApplyEditor {...props} />)

    expect(screen.getByRole("option", { name: /v2/ })).toBeInTheDocument()
    expect(screen.getByLabelText("Ratebook Input")).toBeInTheDocument()
  })

  it("hides ratebook input selector for selected online registered model versions", () => {
    mockMlflow.modelVersionsFor = "registered-opt"
    mockMlflow.modelVersions = [
      {
        version: "3",
        run_id: "registered-online-run",
        status: "READY",
        description: "online",
        params: { mode: "online" },
      },
    ]
    const props = defaultProps()
    props.config = {
      sourceType: "registered",
      registered_model: "registered-opt",
      version: "3",
      ratebook_input: "age-banding-node",
    }
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]
    render(<OptimiserApplyEditor {...props} />)

    expect(screen.getByRole("option", { name: /v3/ })).toBeInTheDocument()
    expect(screen.queryByLabelText("Ratebook Input")).not.toBeInTheDocument()
  })

  it("ignores registered model versions loaded for a different model", () => {
    mockMlflow.modelVersionsFor = "old-ratebook-model"
    mockMlflow.modelVersions = [
      {
        version: "2",
        run_id: "old-ratebook-run",
        status: "READY",
        description: "ratebook",
        params: { mode: "ratebook" },
      },
    ]
    const props = defaultProps()
    props.config = {
      sourceType: "registered",
      registered_model: "new-online-model",
      version: "2",
      ratebook_input: "age-banding-node",
    }
    props.inputSources = [
      {
        sourceNodeId: "optimiser-input-node",
        name: "optimiser_input",
        sourceLabel: "optimiser_input",
        edgeId: "e1",
      },
      {
        sourceNodeId: "age-banding-node",
        name: "age_veh_banding",
        sourceLabel: "Age banding",
        edgeId: "e2",
      },
    ]

    render(<OptimiserApplyEditor {...props} />)

    expect(screen.queryByLabelText("Ratebook Input")).not.toBeInTheDocument()
  })

  it("persisted model shows as fallback option in registered mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "registered", registered_model: "old-model" }
    mockMlflow.models = []
    render(<OptimiserApplyEditor {...props} />)
    const options = screen.getAllByRole("option")
    const values = options.map((o) => o.getAttribute("value"))
    expect(values).toContain("old-model")
  })

  it("version dropdown change calls onUpdate in registered mode", () => {
    mockMlflow.modelVersionsFor = "my-opt"
    mockMlflow.modelVersions = [
      { version: "5", run_id: "r5", status: "READY", description: "" },
    ]
    const props = defaultProps()
    props.config = { sourceType: "registered", registered_model: "my-opt", version: "latest" }
    render(<OptimiserApplyEditor {...props} />)
    const versionSelect = screen.getByDisplayValue("latest")
    fireEvent.change(versionSelect, { target: { value: "5" } })
    expect(props.onUpdate).toHaveBeenCalledWith("version", "5")
  })

  it("shows description text below artifact path input", () => {
    render(<OptimiserApplyEditor {...defaultProps()} />)
    expect(
      screen.getByText(/Path to saved optimiser result/),
    ).toBeInTheDocument()
  })

  it("shows description text below version column input", () => {
    render(<OptimiserApplyEditor {...defaultProps()} />)
    expect(
      screen.getByText(/Column added to output for monitoring/),
    ).toBeInTheDocument()
  })

  it("shows description text below optimised value column input", () => {
    render(<OptimiserApplyEditor {...defaultProps()} />)
    expect(
      screen.getByText(/Column containing the selected optimiser value/),
    ).toBeInTheDocument()
  })
})
