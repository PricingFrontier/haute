import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import useSettingsStore from "../../../stores/useSettingsStore"
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../../../api/types"

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
    aliases?: string[]
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

// Discovery is mocked at the client so one test can run the REAL browser hook
// and prove the editor browses its own destination; everything else in the
// client stays real.
const discovery = vi.hoisted(() => ({
  getExperiments: vi.fn(),
  getRuns: vi.fn(),
  getModels: vi.fn(),
  getModelVersions: vi.fn(),
}))

vi.mock("../../../api/client", async () => ({
  ...(await vi.importActual<Record<string, unknown>>("../../../api/client")),
  ...discovery,
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
  }
})

vi.mock("../CodeEditor", () => ({
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
}))

import ModelScoreEditor from "../ModelScoreEditor"
import { useMlflowBrowser } from "../../../hooks/useMlflowBrowser"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultProps = () => ({
  config: {} as Record<string, unknown>,
  onUpdate: vi.fn(),
  inputSources: [] as { sourceNodeId: string; name: string; sourceLabel: string; edgeId: string }[],
  accentColor: "#8b5cf6",
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
  vi.mocked(useMlflowBrowser).mockImplementation(() => mockMlflow)
}

function mlflowEntry(
  key: MlflowDestinationKey,
  over: Partial<MlflowDestinationEntry> = {},
): MlflowDestinationEntry {
  return {
    key,
    configured: true,
    destination: key === "local" ? "C:/proj/mlruns" : "http://mlflow.example:5000",
    config_source: key === "local" ? "default" : "toml",
    detail: "",
    probed: key !== "local",
    ok: key !== "local",
    category: "",
    ...over,
  }
}

/** A ready inventory, so the mounted selector never fetches one itself. */
function setMlflowInventory(): void {
  useSettingsStore.setState({
    mlflow: {
      status: "ready",
      installed: true,
      importable: true,
      destinations: [mlflowEntry("server"), mlflowEntry("local")],
      detail: "",
    },
  })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ModelScoreEditor", () => {
  beforeEach(() => {
    resetMlflow()
    setMlflowInventory()
    for (const fetcher of Object.values(discovery)) fetcher.mockReset()
  })
  afterEach(cleanup)

  // 1. Renders with default registered source type
  it("explains the selected model source in plain language", () => {
    const { unmount } = render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText(/named, versioned model in the registry/i)).toBeInTheDocument()
    unmount()

    render(<ModelScoreEditor {...defaultProps()} config={{ sourceType: "run" }} />)
    expect(screen.getByText(/pick one specific training run/i)).toBeInTheDocument()
  })

  it("shows an empty-state hint when no registered models exist", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(
      screen.getByText(/No registered models yet — haute logs training runs; your promotion process/i),
    ).toBeInTheDocument()
  })

  it("hides the empty-state hint when models exist", () => {
    mockMlflow.models = [
      { name: "motor-pricing", latest_versions: [] },
    ]
    try {
      render(<ModelScoreEditor {...defaultProps()} />)
      expect(screen.queryByText(/No registered models yet/i)).toBeNull()
    } finally {
      mockMlflow.models = []
    }
  })

  it("shows an empty-runs hint once an experiment is chosen and no runs match", () => {
    mockMlflow.browseExpId = "1"
    try {
      render(<ModelScoreEditor {...defaultProps()} config={{ sourceType: "run" }} />)
      expect(
        screen.getByText(/No finished runs with a model artifact in this experiment yet/i),
      ).toBeInTheDocument()
    } finally {
      mockMlflow.browseExpId = ""
    }
  })

  it("renders with default registered source type selected", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    const registeredBtn = screen.getByText("Registered Model")
    const runBtn = screen.getByText("Experiment Run")
    // Active button should have a visually distinct border from inactive button
    expect(registeredBtn.style.border).not.toBe(runBtn.style.border)
  })

  // 2. Source type toggle switches between registered and run
  it("calls onUpdate when toggling source type to run", () => {
    const { onUpdate } = defaultProps()
    render(<ModelScoreEditor config={{}} onUpdate={onUpdate} inputSources={[]} accentColor="#8b5cf6" />)
    fireEvent.click(screen.getByText("Experiment Run"))
    expect(onUpdate).toHaveBeenCalledWith("sourceType", "run")
  })

  // 3. Registered mode: shows model name dropdown
  it("shows model name dropdown in registered mode", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Model Name")).toBeInTheDocument()
    expect(screen.getByText("Select a model...")).toBeInTheDocument()
  })

  // 4. Registered mode: selecting a model calls onUpdate with registered_model and version
  it("selecting a model calls onUpdate with registered_model and version", () => {
    mockMlflow.models = [
      { name: "my-model", latest_versions: [{ version: "1", status: "READY", run_id: "abc" }] },
    ]
    const props = defaultProps()
    render(<ModelScoreEditor {...props} />)
    const modelSelect = screen.getByDisplayValue("Select a model...")
    fireEvent.change(modelSelect, { target: { value: "my-model" } })
    expect(props.onUpdate).toHaveBeenCalledWith({ registered_model: "my-model", version: "latest" })
  })

  // 5. Registered mode: shows version dropdown when model is selected
  it("shows version dropdown when a model is selected", () => {
    const props = defaultProps()
    props.config = { registered_model: "my-model" }
    mockMlflow.modelVersionsFor = "my-model"
    mockMlflow.modelVersions = [
      { version: "1", run_id: "r1", status: "READY", description: "first" },
      { version: "2", run_id: "r2", status: "READY", description: "" },
    ]
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Version")).toBeInTheDocument()
    expect(screen.getByText("latest")).toBeInTheDocument()
    expect(screen.getByText(/v1 — READY \(first\)/)).toBeInTheDocument()
    expect(screen.getByText(/v2 — READY/)).toBeInTheDocument()
  })

  // 6. Registered mode: version change calls onUpdate
  it("version change calls onUpdate", () => {
    mockMlflow.modelVersionsFor = "my-model"
    mockMlflow.modelVersions = [
      { version: "3", run_id: "r3", status: "READY", description: "" },
    ]
    const props = defaultProps()
    props.config = { registered_model: "my-model", version: "latest" }
    render(<ModelScoreEditor {...props} />)
    const versionSelect = screen.getByDisplayValue("latest")
    fireEvent.change(versionSelect, { target: { value: "3" } })
    expect(props.onUpdate).toHaveBeenCalledWith({ version: "3" })
  })

  // 7. Run mode: shows experiment dropdown
  it("shows experiment dropdown in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Experiment")).toBeInTheDocument()
    expect(screen.getByText("Select an experiment...")).toBeInTheDocument()
  })

  // 8. Run mode: experiment change calls onUpdate with experiment_id and name
  it("experiment change calls onUpdate with experiment_id and experiment_name", () => {
    mockMlflow.experiments = [
      { experiment_id: "exp-1", name: "My Experiment" },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const expSelect = screen.getByDisplayValue("Select an experiment...")
    fireEvent.change(expSelect, { target: { value: "exp-1" } })
    expect(mockMlflow.setBrowseExpId).toHaveBeenCalledWith("exp-1")
    expect(props.onUpdate).toHaveBeenCalledWith({
      experiment_id: "exp-1",
      experiment_name: "My Experiment",
    })
    expect(mockMlflow.setRuns).toHaveBeenCalledWith([])
    expect(mockMlflow.resetRunsGuard).toHaveBeenCalled()
    expect(mockMlflow.refreshRuns).toHaveBeenCalledWith("exp-1")
  })

  // 9. Run mode: shows Run ID and Artifact Path text inputs
  it("shows Run ID and Artifact Path text inputs in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Run ID")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")).toBeInTheDocument()
    expect(screen.getByText("Artifact Path")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. model.cbm")).toBeInTheDocument()
  })

  // 10. Run mode: Run ID text input calls onUpdate
  it("Run ID text input calls onUpdate", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const runIdInput = screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")
    fireEvent.change(runIdInput, { target: { value: "abc123" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(runIdInput)
    expect(props.onUpdate).toHaveBeenCalledWith("run_id", "abc123")
  })

  // 11. Task toggle between regression and classification
  it("toggles task between regression and classification", () => {
    const props = defaultProps()
    render(<ModelScoreEditor {...props} />)
    // Default is regression
    const taskSelect = screen.getByDisplayValue("Regression")
    fireEvent.change(taskSelect, { target: { value: "classification" } })
    expect(props.onUpdate).toHaveBeenCalledWith("task", "classification")
  })

  // 12. Classification task shows proba column note
  it("shows proba column note when task is classification", () => {
    const props = defaultProps()
    props.config = { task: "classification", output_column: "score" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("score_proba")).toBeInTheDocument()
    expect(screen.getByText(/Classification models also generate a/)).toBeInTheDocument()
  })

  // 13. Output column input calls onUpdate
  it("output column input calls onUpdate", () => {
    const props = defaultProps()
    render(<ModelScoreEditor {...props} />)
    const colInput = screen.getByDisplayValue("prediction")
    fireEvent.change(colInput, { target: { value: "my_score" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(colInput)
    expect(props.onUpdate).toHaveBeenCalledWith("output_column", "my_score")
  })

  // 17. Shows error messages when MLflow hooks have errors
  it("shows error messages when MLflow hooks have errors", () => {
    mockMlflow.errorModels = "Models endpoint unavailable"
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Models endpoint unavailable")).toBeInTheDocument()
  })

  it("shows version error when model is selected and errorVersions is set", () => {
    mockMlflow.errorVersions = "Versions fetch failed"
    const props = defaultProps()
    props.config = { registered_model: "my-model" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Versions fetch failed")).toBeInTheDocument()
  })

  it("shows experiment error in run mode", () => {
    mockMlflow.errorExperiments = "Experiment list unavailable"
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Experiment list unavailable")).toBeInTheDocument()
  })

  it("shows run error in run mode when experiment is selected", () => {
    mockMlflow.errorRuns = "Runs fetch failed"
    mockMlflow.browseExpId = "exp-1"
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.getByText("Runs fetch failed")).toBeInTheDocument()
  })

  // 18. InputSourcesBar renders with input sources
  it("renders InputSourcesBar with input sources", () => {
    const props = defaultProps()
    props.inputSources = [
      { sourceNodeId: "test-source", name: "df", sourceLabel: "data_source", edgeId: "e1" },
      { sourceNodeId: "test-source", name: "df2", sourceLabel: "other_source", edgeId: "e2" },
    ]
    render(<ModelScoreEditor {...props} />)
    const bar = screen.getByTestId("input-sources")
    expect(bar).toBeInTheDocument()
    expect(bar.textContent).toBe("2")
  })

  // Additional edge cases

  it("does not show proba note in regression mode", () => {
    const props = defaultProps()
    props.config = { task: "regression" }
    render(<ModelScoreEditor {...props} />)
    expect(screen.queryByText(/also generate a/)).not.toBeInTheDocument()
  })

  it("does not show version dropdown when no model is selected", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    // "Version" label should not appear when no model selected
    expect(screen.queryByText("Version")).not.toBeInTheDocument()
  })

  it("mounts the destination selector instead of a status badge", () => {
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByRole("radiogroup", { name: "MLflow destination" })).toBeInTheDocument()
    expect(screen.queryByTestId("mlflow-badge")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /mlflow status/i })).not.toBeInTheDocument()
  })

  it("browses the node's own destination", () => {
    const props = defaultProps()
    props.config = { mlflow_destination: "server" }
    render(<ModelScoreEditor {...props} />)
    expect(vi.mocked(useMlflowBrowser)).toHaveBeenCalledWith({
      destination: "server",
      initialExpId: "",
    })
    expect(screen.getByRole("radio", { name: /MLflow server/ })).toBeChecked()
  })

  it("lists only the chosen destination's registered models", async () => {
    const actual = await vi.importActual<typeof import("../../../hooks/useMlflowBrowser")>(
      "../../../hooks/useMlflowBrowser",
    )
    vi.mocked(useMlflowBrowser).mockImplementation(actual.useMlflowBrowser)
    discovery.getModels.mockResolvedValue([
      { name: "local-model", latest_versions: [] },
    ])
    const props = defaultProps()
    props.config = { mlflow_destination: "local" }
    render(<ModelScoreEditor {...props} />)

    fireEvent.focus(screen.getByDisplayValue("Select a model..."))

    await waitFor(() => {
      expect(discovery.getModels).toHaveBeenCalledWith("local")
    })
    expect(await screen.findByRole("option", { name: "local-model" })).toBeInTheDocument()
  })

  it("clears the selection and explains why when the destination changes", () => {
    const props = defaultProps()
    props.config = {
      mlflow_destination: "local",
      sourceType: "run",
      run_id: "run-abc",
      run_name: "best-run",
      experiment_id: "exp-1",
      experiment_name: "Experiment A",
      artifact_path: "model.cbm",
      registered_model: "old-model",
      version: "3",
    }
    render(<ModelScoreEditor {...props} />)

    fireEvent.click(screen.getByRole("radio", { name: /MLflow server/ }))

    expect(props.onUpdate).toHaveBeenCalledWith({
      mlflow_destination: "server",
      run_id: "",
      run_name: "",
      experiment_id: "",
      experiment_name: "",
      artifact_path: "",
      registered_model: "",
      version: "latest",
    })
    expect(screen.getByTestId("mlflow-selection-cleared")).toHaveTextContent(
      "Selection cleared — run and model identifiers are not portable across destinations.",
    )
  })

  it("leaves the config alone when the chosen destination is already selected", () => {
    const props = defaultProps()
    props.config = { mlflow_destination: "server" }
    render(<ModelScoreEditor {...props} />)

    fireEvent.click(screen.getByRole("radio", { name: /MLflow server/ }))

    expect(props.onUpdate).not.toHaveBeenCalled()
    expect(screen.queryByTestId("mlflow-selection-cleared")).not.toBeInTheDocument()
  })

  it("drops the cleared-selection note at the next pick", () => {
    mockMlflow.models = [
      { name: "model-a", latest_versions: [{ version: "1", status: "READY", run_id: "r1" }] },
    ]
    const props = defaultProps()
    props.config = { mlflow_destination: "local" }
    render(<ModelScoreEditor {...props} />)
    fireEvent.click(screen.getByRole("radio", { name: /MLflow server/ }))
    expect(screen.getByTestId("mlflow-selection-cleared")).toBeInTheDocument()

    fireEvent.change(screen.getByDisplayValue("Select a model..."), {
      target: { value: "model-a" },
    })

    expect(screen.queryByTestId("mlflow-selection-cleared")).not.toBeInTheDocument()
    expect(props.onUpdate).toHaveBeenLastCalledWith({
      registered_model: "model-a",
      version: "latest",
    })
  })

  it("shows loading text in model dropdown when loadingModels is true", () => {
    mockMlflow.loadingModels = true
    render(<ModelScoreEditor {...defaultProps()} />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("artifact path input calls onUpdate in run mode", () => {
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const artifactInput = screen.getByPlaceholderText("e.g. model.cbm")
    fireEvent.change(artifactInput, { target: { value: "model/best.cbm" } })
    expect(props.onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(artifactInput)
    expect(props.onUpdate).toHaveBeenCalledWith("artifact_path", "model/best.cbm")
  })

  it("run dropdown change calls onUpdate with run_id, run_name, and artifact_path", () => {
    mockMlflow.browseExpId = "exp-1"
    mockMlflow.runs = [
      { run_id: "run-abc", run_name: "best-run", metrics: { rmse: 0.123 }, artifacts: ["model.cbm"] },
    ]
    const props = defaultProps()
    props.config = { sourceType: "run" }
    render(<ModelScoreEditor {...props} />)
    const runSelect = screen.getByDisplayValue("Select a run...")
    fireEvent.change(runSelect, { target: { value: "run-abc" } })
    expect(props.onUpdate).toHaveBeenCalledWith({
      run_id: "run-abc",
      run_name: "best-run",
      artifact_path: "model.cbm",
    })
  })

  it("persisted model shows as option even when not in models list", () => {
    const props = defaultProps()
    props.config = { registered_model: "unlisted-model" }
    mockMlflow.models = [] // model not in the fetched list
    render(<ModelScoreEditor {...props} />)
    // Should show the persisted model as a fallback option
    const options = screen.getAllByRole("option")
    const values = options.map((o) => o.getAttribute("value"))
    expect(values).toContain("unlisted-model")
  })

  describe("task recorded by the training run", () => {
    it("takes the task from the chosen run and shows it read-only", () => {
      mockMlflow.browseExpId = "exp-1"
      mockMlflow.runs = [
        {
          run_id: "run-cls",
          run_name: "conversion",
          metrics: {},
          params: { task: "classification" },
          artifacts: ["model.cbm"],
        },
      ]
      const props = defaultProps()
      props.config = { sourceType: "run" }
      const { rerender } = render(<ModelScoreEditor {...props} />)
      fireEvent.change(screen.getByDisplayValue("Select a run..."), {
        target: { value: "run-cls" },
      })
      expect(props.onUpdate).toHaveBeenCalledWith({
        run_id: "run-cls",
        run_name: "conversion",
        artifact_path: "model.cbm",
        task: "classification",
      })

      rerender(
        <ModelScoreEditor
          {...props}
          config={{ sourceType: "run", run_id: "run-cls", task: "classification" }}
        />,
      )
      expect(screen.getByTestId("model-score-recorded-task")).toHaveTextContent("Classification")
      expect(screen.queryByDisplayValue("Regression")).toBeNull()
      expect(screen.getByText("Task recorded by the training run.")).toBeInTheDocument()
      expect(screen.queryByRole("alert")).toBeNull()
    })

    it("takes the task from the version the choice resolves to", () => {
      mockMlflow.modelVersionsFor = "freq"
      mockMlflow.modelVersions = [
        { version: "2", run_id: "r2", status: "READY", description: "", params: { task: "regression" } },
        { version: "1", run_id: "r1", status: "READY", description: "", params: { task: "classification" } },
      ]
      const props = defaultProps()
      props.config = { registered_model: "freq", version: "2" }
      render(<ModelScoreEditor {...props} />)
      fireEvent.change(screen.getByDisplayValue("v2 — READY"), { target: { value: "1" } })
      expect(props.onUpdate).toHaveBeenCalledWith({ version: "1", task: "classification" })
      fireEvent.change(screen.getByDisplayValue("v2 — READY"), { target: { value: "latest" } })
      expect(props.onUpdate).toHaveBeenCalledWith({ version: "latest", task: "regression" })
    })

    it("takes the task from the version a stored alias targets", () => {
      mockMlflow.modelVersionsFor = "freq"
      mockMlflow.modelVersions = [
        { version: "2", run_id: "r2", status: "READY", description: "", params: { task: "regression" }, aliases: [] },
        { version: "1", run_id: "r1", status: "READY", description: "", params: { task: "classification" }, aliases: ["champion"] },
      ]
      const props = defaultProps()
      props.config = { registered_model: "freq", alias: "champion", task: "classification" }
      render(<ModelScoreEditor {...props} />)
      expect(screen.getByTestId("model-score-recorded-task")).toHaveTextContent("Classification")
    })

    it("offers the recorded task when the stored task conflicts", () => {
      mockMlflow.browseExpId = "exp-1"
      mockMlflow.runs = [
        { run_id: "run-cls", run_name: "c", metrics: {}, params: { task: "classification" }, artifacts: [] },
      ]
      const props = defaultProps()
      props.config = { sourceType: "run", run_id: "run-cls", task: "regression" }
      render(<ModelScoreEditor {...props} />)
      expect(screen.getByRole("alert")).toHaveTextContent(
        "This node scores as regression, but the model was trained for classification.",
      )
      fireEvent.click(screen.getByRole("button", { name: "Use classification" }))
      expect(props.onUpdate).toHaveBeenCalledWith("task", "classification")
    })

    it("keeps the explicit task choice for a model without a recorded task", () => {
      mockMlflow.browseExpId = "exp-1"
      mockMlflow.runs = [
        { run_id: "run-foreign", run_name: "f", metrics: {}, params: {}, artifacts: ["model"] },
      ]
      const props = defaultProps()
      props.config = { sourceType: "run", run_id: "run-foreign", task: "classification" }
      render(<ModelScoreEditor {...props} />)
      expect(screen.getByDisplayValue("Classification")).toBeInTheDocument()
      expect(screen.queryByTestId("model-score-recorded-task")).toBeNull()
    })
  })
})
