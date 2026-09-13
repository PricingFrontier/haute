import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { RegisteredModelPicker, ExperimentRunPicker } from "../MlflowModelPicker"
import type { MlflowBrowserState } from "../../../hooks/useMlflowBrowser"

afterEach(cleanup)

function makeMlflow(overrides: Partial<MlflowBrowserState> = {}): MlflowBrowserState {
  return {
    experiments: [],
    runs: [],
    models: [],
    modelVersions: [],
    modelVersionsFor: "",
    loadingModels: false,
    loadingExperiments: false,
    loadingRuns: false,
    loadingVersions: false,
    errorModels: null,
    errorExperiments: null,
    errorRuns: null,
    errorVersions: null,
    browseExpId: "",
    setBrowseExpId: vi.fn(),
    setRuns: vi.fn(),
    resetRunsGuard: vi.fn(),
    refreshModels: vi.fn(),
    refreshExperiments: vi.fn(),
    refreshRuns: vi.fn(),
    refreshVersions: vi.fn(),
    ...overrides,
  } as MlflowBrowserState
}

describe("RegisteredModelPicker", () => {
  it("renders Model Name label and select", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Model Name")).toBeInTheDocument()
    expect(screen.getByText("Select a model...")).toBeInTheDocument()
  })

  it("shows Loading... when models are loading", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow({ loadingModels: true })} />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("renders model options", () => {
    const mlflow = makeMlflow({
      models: [{ name: "my-model", latest_versions: [] }],
    })
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.getByText("my-model")).toBeInTheDocument()
  })

  it("selecting a model calls onUpdate with model name and version=latest", () => {
    const onUpdate = vi.fn()
    const mlflow = makeMlflow({
      models: [{ name: "my-model", latest_versions: [] }],
    })
    render(<RegisteredModelPicker config={{}} onUpdate={onUpdate} mlflow={mlflow} />)
    const select = screen.getAllByRole("combobox")[0]
    fireEvent.change(select, { target: { value: "my-model" } })
    expect(onUpdate).toHaveBeenCalledWith({ registered_model: "my-model", version: "latest" })
  })

  it("shows Version select when a model is selected", () => {
    render(<RegisteredModelPicker config={{ registered_model: "my-model" }} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Version")).toBeInTheDocument()
    expect(screen.getByText("latest")).toBeInTheDocument()
  })

  it("only shows versions loaded for the selected model", () => {
    const mlflow = makeMlflow({
      modelVersionsFor: "old-model",
      modelVersions: [{ version: "4", run_id: "r4", status: "READY", description: "" }],
    })
    render(<RegisteredModelPicker config={{ registered_model: "new-model" }} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.queryByText(/v4/)).not.toBeInTheDocument()
  })

  it("does not show Version select when no model is selected", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.queryByText("Version")).not.toBeInTheDocument()
  })

  describe("registered aliases", () => {
    const versions = [
      { version: "3", run_id: "r3", status: "READY", description: "", aliases: ["champion"] },
      { version: "2", run_id: "r2", status: "READY", description: "", aliases: [] },
    ]

    it("lists each alias with the version it targets beside latest and the versions", () => {
      const mlflow = makeMlflow({ modelVersionsFor: "freq", modelVersions: versions })
      render(<RegisteredModelPicker config={{ registered_model: "freq" }} onUpdate={vi.fn()} mlflow={mlflow} />)
      const versionSelect = screen.getAllByRole("combobox")[1]
      const labels = Array.from(versionSelect.querySelectorAll("option")).map((o) => o.textContent)
      expect(labels).toEqual(["latest", "@champion → v3", "v3 — READY", "v2 — READY"])
    })

    it("stores an alias instead of a version, and a version instead of an alias", () => {
      const onUpdate = vi.fn()
      const mlflow = makeMlflow({ modelVersionsFor: "freq", modelVersions: versions })
      const { rerender } = render(
        <RegisteredModelPicker config={{ registered_model: "freq", version: "2" }} onUpdate={onUpdate} mlflow={mlflow} />,
      )
      fireEvent.change(screen.getAllByRole("combobox")[1], { target: { value: "alias:champion" } })
      expect(onUpdate).toHaveBeenLastCalledWith({ alias: "champion", version: undefined })

      rerender(
        <RegisteredModelPicker config={{ registered_model: "freq", alias: "champion" }} onUpdate={onUpdate} mlflow={mlflow} />,
      )
      expect(screen.getAllByRole("combobox")[1]).toHaveValue("alias:champion")
      fireEvent.change(screen.getAllByRole("combobox")[1], { target: { value: "2" } })
      expect(onUpdate).toHaveBeenLastCalledWith({ version: "2", alias: undefined })
    })

    it("passes the version an alias targets to onVersionSelected", () => {
      const onVersionSelected = vi.fn(() => ({}))
      const mlflow = makeMlflow({ modelVersionsFor: "freq", modelVersions: versions })
      render(
        <RegisteredModelPicker
          config={{ registered_model: "freq" }}
          onUpdate={vi.fn()}
          mlflow={mlflow}
          onVersionSelected={onVersionSelected}
        />,
      )
      fireEvent.change(screen.getAllByRole("combobox")[1], { target: { value: "alias:champion" } })
      expect(onVersionSelected).toHaveBeenCalledWith(versions[0])
    })

    it("keeps showing a stored alias before the versions are loaded", () => {
      render(
        <RegisteredModelPicker config={{ registered_model: "freq", alias: "champion" }} onUpdate={vi.fn()} mlflow={makeMlflow()} />,
      )
      expect(screen.getAllByRole("combobox")[1]).toHaveValue("alias:champion")
      expect(screen.getByText("@champion")).toBeInTheDocument()
    })

    it("clears a stored alias when another model is chosen", () => {
      const onUpdate = vi.fn()
      const mlflow = makeMlflow({ models: [{ name: "sev", latest_versions: [] }] })
      render(
        <RegisteredModelPicker config={{ registered_model: "freq", alias: "champion" }} onUpdate={onUpdate} mlflow={mlflow} />,
      )
      fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "sev" } })
      expect(onUpdate).toHaveBeenCalledWith({ registered_model: "sev", version: "latest", alias: undefined })
      expect(Object.keys(onUpdate.mock.calls[0][0])).toContain("alias")
    })
  })

  it("shows error message when errorModels is set", () => {
    render(<RegisteredModelPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow({ errorModels: "Network error" })} />)
    expect(screen.getByText("Network error")).toBeInTheDocument()
  })
})

describe("ExperimentRunPicker", () => {
  it("renders Experiment label", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Experiment")).toBeInTheDocument()
  })

  it("renders experiment options", () => {
    const mlflow = makeMlflow({
      experiments: [{ experiment_id: "exp1", name: "My Experiment" }],
    })
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.getByText("My Experiment")).toBeInTheDocument()
  })

  it("shows Run ID text input", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.getByText("Run ID")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("e.g. a1b2c3d4e5f6...")).toBeInTheDocument()
  })

  it("shows Artifact Path when showArtifactPath is true", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} showArtifactPath />)
    expect(screen.getByText("Artifact Path")).toBeInTheDocument()
  })

  it("does not show Artifact Path by default", () => {
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={makeMlflow()} />)
    expect(screen.queryByText("Artifact Path")).not.toBeInTheDocument()
  })

  it("shows Run select when experiment is selected", () => {
    const mlflow = makeMlflow({ browseExpId: "exp1" })
    render(<ExperimentRunPicker config={{}} onUpdate={vi.fn()} mlflow={mlflow} />)
    expect(screen.getByText("Run")).toBeInTheDocument()
  })
})
