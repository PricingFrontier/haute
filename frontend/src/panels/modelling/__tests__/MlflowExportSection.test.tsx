/**
 * MlflowExportSection — manual "Log run to MLflow" with truthful
 * availability: destination line when connected, disabled-with-reason plus
 * a Configure MLflow link when not, and per-backend success surfaces
 * (Databricks/server links; run ID + copyable `mlflow ui` command for
 * local). Per specs/frontend-modelling-optimiser-ui.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { MlflowExportSection } from "../MlflowExportSection"
import useSettingsStore from "../../../stores/useSettingsStore"
import useUIStore from "../../../stores/useUIStore"

vi.mock("../../../api/client", () => ({
  logToMlflow: vi.fn(),
}))

import { logToMlflow } from "../../../api/client"
const mockLogToMlflow = vi.mocked(logToMlflow)

const CONNECTED_DATABRICKS = {
  status: "connected" as const,
  mode: "databricks",
  destination: "https://adb.example.net",
  configSource: "env",
  installed: true,
  importable: true,
  configured: true,
  detail: "",
}

function makeProps(overrides: Partial<Parameters<typeof MlflowExportSection>[0]> = {}) {
  return {
    trainJobId: "job_123",
    config: {} as Record<string, unknown>,
    ...overrides,
  }
}

describe("MlflowExportSection", () => {
  beforeEach(() => {
    mockLogToMlflow.mockReset()
    useSettingsStore.setState({ mlflow: CONNECTED_DATABRICKS })
    useUIStore.setState({ mlflowSettingsOpen: false })
  })

  afterEach(cleanup)

  it("renders the log action with the destination beneath it", () => {
    render(<MlflowExportSection {...makeProps()} />)
    const button = screen.getByRole("button", { name: /log run to mlflow/i })
    expect(button).toBeEnabled()
    expect(screen.getByText(/Databricks — https:\/\/adb\.example\.net/)).toBeInTheDocument()
  })

  it("disables the action with the reason and a Configure link when MLflow is off", () => {
    useSettingsStore.setState({
      mlflow: {
        ...CONNECTED_DATABRICKS,
        status: "error",
        mode: "",
        destination: "",
        configured: false,
        detail: "MLflow package is not installed. Install it with: pip install mlflow",
      },
    })
    render(<MlflowExportSection {...makeProps()} />)
    expect(screen.getByRole("button", { name: /log run to mlflow/i })).toBeDisabled()
    expect(screen.getByText(/not installed/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /configure mlflow/i }))
    expect(useUIStore.getState().mlflowSettingsOpen).toBe(true)
  })

  it("clicking the action logs the job", async () => {
    mockLogToMlflow.mockResolvedValue({ status: "ok", backend: "databricks", experiment_name: "test_exp", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => expect(mockLogToMlflow).toHaveBeenCalledOnce())
  })

  it("shows the Databricks run link on success", async () => {
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "databricks",
      experiment_name: "pricing_model",
      run_id: "run_abc",
      run_url: "https://example.com/run/abc",
      tracking_uri: "databricks",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByText(/Logged to pricing_model/)).toBeInTheDocument()
      expect(screen.getByText("Open in Databricks")).toBeInTheDocument()
    })
  })

  it("shows a generic run link for a server backend", async () => {
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "server",
      experiment_name: "pricing_model",
      run_id: "run_abc",
      run_url: "http://localhost:5000/#/experiments/1/runs/run_abc",
      tracking_uri: "http://localhost:5000",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByText("Open run")).toBeInTheDocument()
    })
  })

  it("shows a copyable PowerShell command for local", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "local",
      experiment_name: "freq",
      run_id: "run_local_1",
      run_url: null,
      tracking_uri: "file:///C:/proj/mlruns",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))

    await waitFor(() => {
      expect(screen.getByText(/run_local_1/)).toBeInTheDocument()
    })
    fireEvent.change(screen.getByRole("combobox", { name: "Terminal" }), { target: { value: "powershell" } })
    const command = screen.getByText(/mlflow ui --backend-store-uri/)
    expect(command).toHaveTextContent("$env:MLFLOW_ALLOW_FILE_STORE='true'; mlflow ui --backend-store-uri 'file:///C:/proj/mlruns'")

    fireEvent.click(screen.getByRole("button", { name: /copy command/i }))
    expect(writeText).toHaveBeenCalledWith(
      "$env:MLFLOW_ALLOW_FILE_STORE='true'; mlflow ui --backend-store-uri 'file:///C:/proj/mlruns'",
    )
  })

  it("copies the bash/zsh command with a safely quoted URI", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
    mockLogToMlflow.mockResolvedValue({
      status: "ok", backend: "local", experiment_name: "freq", run_id: "run_local_bash",
      run_url: null, tracking_uri: "file:///tmp/o'hare/mlruns", error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => expect(screen.getByText(/run_local_bash/)).toBeInTheDocument())

    fireEvent.change(screen.getByRole("combobox", { name: "Terminal" }), { target: { value: "bash" } })
    const expected = "MLFLOW_ALLOW_FILE_STORE=true mlflow ui --backend-store-uri 'file:///tmp/o'\"'\"'hare/mlruns'"
    expect(screen.getByText(/mlflow ui --backend-store-uri/)).toHaveTextContent(expected)
    fireEvent.click(screen.getByRole("button", { name: /copy command/i }))
    expect(writeText).toHaveBeenCalledWith(expected)
  })

  it("keeps remote successes without a run link free of local-viewer instructions", async () => {
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "server",
      experiment_name: "freq",
      run_id: "run_remote_1",
      run_url: null,
      tracking_uri: "http://mlflow.example.com:5000",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))

    await waitFor(() => {
      expect(screen.getByText(/run_remote_1/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/mlflow ui --backend-store-uri/)).toBeNull()
    expect(screen.getByText(/run link unavailable/i)).toBeInTheDocument()
  })

  it("reports a copy failure when the clipboard is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true })
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "local",
      experiment_name: "freq",
      run_id: "run_local_2",
      run_url: null,
      tracking_uri: "file:///C:/proj/mlruns",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /copy command/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /copy command/i }))
    await waitFor(() => {
      expect(screen.getByText(/copy failed/i)).toBeInTheDocument()
    })
  })

  it("reports a copy failure when the clipboard write rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"))
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "local",
      experiment_name: "freq",
      run_id: "run_local_3",
      run_url: null,
      tracking_uri: "file:///C:/proj/mlruns",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /copy command/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /copy command/i }))
    await waitFor(() => {
      expect(screen.getByText(/copy failed/i)).toBeInTheDocument()
    })
  })

  it("confirms a successful copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "local",
      experiment_name: "freq",
      run_id: "run_local_4",
      run_url: null,
      tracking_uri: "file:///C:/proj/mlruns",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /copy command/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /copy command/i }))
    await waitFor(() => {
      expect(screen.getByText(/^copied$/i)).toBeInTheDocument()
    })
  })

  it("shows error result on failure", async () => {
    mockLogToMlflow.mockResolvedValue({ status: "error", backend: "databricks", experiment_name: "", run_id: null, run_url: null, tracking_uri: "", error: "Experiment not found" })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByText("Experiment not found")).toBeInTheDocument()
    })
  })

  it("shows error when logToMlflow throws", async () => {
    mockLogToMlflow.mockRejectedValue(new Error("Network error"))
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  it("calls onMlflowResult callback with result", async () => {
    const onResult = vi.fn()
    mockLogToMlflow.mockResolvedValue({ status: "ok", backend: "databricks", experiment_name: "test", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps({ onMlflowResult: onResult })} />)
    fireEvent.click(screen.getByRole("button", { name: /log run to mlflow/i }))
    await waitFor(() => {
      expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ status: "ok" }))
    })
  })
})
