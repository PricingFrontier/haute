/**
 * MlflowExportSection — manual "Log run to MLflow" with truthful
 * availability: a line naming the NODE's destination, disabled-with-reason
 * plus a Configure MLflow link when that destination cannot accept a log,
 * `destination` always on the request, and a success surface of just the run
 * ID (plus the run link a Databricks/server backend returns). Per
 * specs/frontend-modelling-optimiser-ui.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { MlflowExportSection } from "../MlflowExportSection"
import useSettingsStore from "../../../stores/useSettingsStore"
import useUIStore from "../../../stores/useUIStore"
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../../../api/types"

vi.mock("../../../api/client", async (importOriginal) => ({
  ApiError: (await importOriginal<typeof import("../../../api/client")>()).ApiError,
  logToMlflow: vi.fn(),
}))

import { ApiError, logToMlflow } from "../../../api/client"
const mockLogToMlflow = vi.mocked(logToMlflow)

type MlflowSlice = ReturnType<typeof useSettingsStore.getState>["mlflow"]

function entry(
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

const DATABRICKS = entry("databricks", {
  destination: "https://adb.example.net",
  probed: true,
  ok: true,
})
const SERVER_UNCONFIGURED = entry("server", {
  configured: false,
  config_source: "",
  detail: "Set [mlflow] tracking_uri in haute.toml.",
})
const LOCAL = entry("local", {
  destination: "C:/proj/mlruns",
  config_source: "default",
})

function setInventory(over: Partial<MlflowSlice> = {}): void {
  useSettingsStore.setState({
    mlflow: {
      status: "ready",
      installed: true,
      importable: true,
      destinations: [DATABRICKS, SERVER_UNCONFIGURED, LOCAL],
      detail: "",
      ...over,
    },
  })
}

function makeProps(overrides: Partial<Parameters<typeof MlflowExportSection>[0]> = {}) {
  return {
    trainJobId: "job_123",
    config: {} as Record<string, unknown>,
    ...overrides,
  }
}

function logButton(): HTMLElement {
  return screen.getByRole("button", { name: /log run to mlflow/i })
}

describe("MlflowExportSection", () => {
  beforeEach(() => {
    mockLogToMlflow.mockReset()
    setInventory()
    useUIStore.setState({ mlflowSettingsOpen: false })
  })

  afterEach(cleanup)

  // ── Destination line ───────────────────────────────────────────

  it("renders the local log action without repeating the destination", () => {
    render(<MlflowExportSection {...makeProps()} />)
    expect(logButton()).toBeEnabled()
    expect(screen.queryByTestId("mlflow-export-destination")).toBeNull()
    expect(screen.queryByText(/adb\.example\.net/)).toBeNull()
  })

  it("disables the action without an exportable job and omits the destination", () => {
    render(<MlflowExportSection {...makeProps({ trainJobId: null })} />)
    expect(logButton()).toBeDisabled()
    fireEvent.click(logButton())
    expect(mockLogToMlflow).not.toHaveBeenCalled()
    expect(screen.queryByTestId("mlflow-export-destination")).toBeNull()
    expect(screen.queryByRole("button", { name: "Configure MLflow" })).toBeNull()
  })

  it("omits repeated remote destination details", () => {
    render(<MlflowExportSection {...makeProps({ config: { mlflow_destination: "databricks" } })} />)
    expect(screen.queryByTestId("mlflow-export-destination")).toBeNull()
  })

  it("disables with the reason when the node's own destination is unconfigured", () => {
    // The local folder is configured, but this node asked for the server —
    // which is not, so logging cannot silently divert.
    render(<MlflowExportSection {...makeProps({ config: { mlflow_destination: "server" } })} />)
    expect(logButton()).toBeDisabled()
    expect(screen.getByTestId("mlflow-export-destination")).toHaveTextContent(
      "Set [mlflow] tracking_uri in haute.toml.",
    )
  })

  it("stays enabled on the local folder when a remote is unconfigured", () => {
    render(<MlflowExportSection {...makeProps({ config: { mlflow_destination: "" } })} />)
    expect(logButton()).toBeEnabled()
    expect(screen.queryByTestId("mlflow-export-destination")).toBeNull()
  })

  it("disables the action with the reason and a Configure link when the package is missing", () => {
    setInventory({
      status: "error",
      installed: false,
      importable: false,
      destinations: [],
      detail: "MLflow package is not installed. Install it with: pip install mlflow",
    })
    render(<MlflowExportSection {...makeProps()} />)
    expect(logButton()).toBeDisabled()
    expect(screen.getByText(/not installed/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /configure mlflow/i }))
    expect(useUIStore.getState().mlflowSettingsOpen).toBe(true)
  })

  it("offers no Configure link while the inventory is still loading", () => {
    setInventory({ status: "pending", installed: null, importable: null, destinations: [] })
    render(<MlflowExportSection {...makeProps()} />)
    expect(logButton()).toBeDisabled()
    expect(screen.getByTestId("mlflow-export-destination")).toHaveTextContent("Checking MLflow…")
    expect(screen.queryByRole("button", { name: /configure mlflow/i })).toBeNull()
  })

  it("never mentions the toolbar", () => {
    render(<MlflowExportSection {...makeProps({ config: { mlflow_destination: "server" } })} />)
    expect(screen.queryByText(/toolbar/i)).toBeNull()
  })

  // ── Log request ────────────────────────────────────────────────

  it("sends the node's explicit destination with the log request", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "ok", backend: "local", experiment_name: "freq", run_id: "r1", run_url: null, tracking_uri: "file:///C:/proj/mlruns", error: null })
    render(<MlflowExportSection {...makeProps({ config: { mlflow_destination: "databricks" } })} />)
    fireEvent.click(logButton())
    await waitFor(() =>
      expect(mockLogToMlflow).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: "job_123", destination: "databricks" }),
      ),
    )
  })

  it("never sends a registry name: haute logs candidate runs only", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "ok", backend: "local", experiment_name: "freq", run_id: "r1", run_url: null, tracking_uri: "file:///C:/proj/mlruns", error: null })
    render(<MlflowExportSection {...makeProps({ config: { mlflow_experiment: "freq" } })} />)
    fireEvent.click(logButton())
    await waitFor(() => expect(mockLogToMlflow).toHaveBeenCalledTimes(1))
    expect(Object.keys(mockLogToMlflow.mock.calls[0][0]).sort()).toEqual(
      ["destination", "experiment_name", "job_id", "operation_id"],
    )
  })

  it("sends an empty destination for the local folder", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "ok", backend: "databricks", experiment_name: "e", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    await waitFor(() =>
      expect(mockLogToMlflow).toHaveBeenCalledWith(
        expect.objectContaining({ destination: "" }),
      ),
    )
  })

  it("follows a config flip back to the local folder after the first log", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "ok", backend: "local", experiment_name: "freq", run_id: "r1", run_url: null, tracking_uri: "file:///C:/proj/mlruns", error: null })
    const { rerender } = render(
      <MlflowExportSection {...makeProps({ config: { mlflow_destination: "databricks" } })} />,
    )
    fireEvent.click(logButton())
    await waitFor(() =>
      expect(mockLogToMlflow).toHaveBeenLastCalledWith(
        expect.objectContaining({ destination: "databricks" }),
      ),
    )

    rerender(<MlflowExportSection {...makeProps({ config: {} })} />)
    fireEvent.click(logButton())
    await waitFor(() =>
      expect(mockLogToMlflow).toHaveBeenLastCalledWith(
        expect.objectContaining({ destination: "" }),
      ),
    )
  })

  it("clicking the action logs the job", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "ok", backend: "databricks", experiment_name: "test_exp", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    await waitFor(() => expect(mockLogToMlflow).toHaveBeenCalledOnce())
  })

  // ── Success surface: the run ID, plus the run link a remote returns ──

  function expectOnlyTheRunId(runId: string) {
    const success = screen.getByTestId("mlflow-log-success")
    expect(success.firstChild).toHaveTextContent(`Run ID: ${runId}`)
    expect(screen.queryByText(/Logged to/)).toBeNull()
    expect(screen.queryByText(/Score this run/)).toBeNull()
    expect(screen.queryByText(/mlflow ui/)).toBeNull()
    expect(screen.queryByText(/run link unavailable/i)).toBeNull()
    expect(screen.queryByRole("combobox", { name: "Terminal" })).toBeNull()
    expect(screen.queryByRole("button", { name: /copy/i })).toBeNull()
  }

  it("shows just the run ID for a local log", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null,
      status: "ok",
      backend: "local",
      experiment_name: "freq",
      run_id: "d9d8727d5b2942c89330bbc32202c19b",
      run_url: null,
      tracking_uri: "file:///C:/proj/mlruns",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())

    await screen.findByTestId("mlflow-log-success")
    expectOnlyTheRunId("d9d8727d5b2942c89330bbc32202c19b")
    expect(screen.getByTestId("mlflow-log-success")).toHaveTextContent(
      /^Run ID: d9d8727d5b2942c89330bbc32202c19b$/,
    )
  })

  it("shows the run ID and the Databricks run link", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null,
      status: "ok",
      backend: "databricks",
      experiment_name: "pricing_model",
      run_id: "run_abc",
      run_url: "https://example.com/run/abc",
      tracking_uri: "databricks",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())

    expect(await screen.findByRole("link", { name: "Open in Databricks" })).toHaveAttribute(
      "href",
      "https://example.com/run/abc",
    )
    expectOnlyTheRunId("run_abc")
  })

  it("shows the run ID and a generic run link for a server backend", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null,
      status: "ok",
      backend: "server",
      experiment_name: "pricing_model",
      run_id: "run_abc",
      run_url: "http://localhost:5000/#/experiments/1/runs/run_abc",
      tracking_uri: "http://localhost:5000",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())

    expect(await screen.findByRole("link", { name: "Open run" })).toBeInTheDocument()
    expectOnlyTheRunId("run_abc")
  })

  it("shows just the run ID for a remote log without a run link", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null,
      status: "ok",
      backend: "server",
      experiment_name: "freq",
      run_id: "run_remote_1",
      run_url: null,
      tracking_uri: "http://mlflow.example.com:5000",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())

    await screen.findByTestId("mlflow-log-success")
    expectOnlyTheRunId("run_remote_1")
    expect(screen.queryByRole("link")).toBeNull()
  })

  it("shows error result on failure", async () => {
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "error", backend: "databricks", experiment_name: "", run_id: null, run_url: null, tracking_uri: "", error: "Experiment not found" })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    await waitFor(() => {
      expect(screen.getByText("Experiment not found")).toBeInTheDocument()
    })
  })

  it("shows error when logToMlflow throws", async () => {
    mockLogToMlflow.mockRejectedValue(new Error("Network error"))
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  it("shows the server's message for a rejected log, never the HTTP status", async () => {
    mockLogToMlflow.mockRejectedValue(
      new ApiError("HTTP 400", 400, "Set [mlflow] tracking_uri in haute.toml.", undefined,
        "Set [mlflow] tracking_uri in haute.toml."),
    )
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Set [mlflow] tracking_uri in haute.toml.",
    )
    expect(screen.queryByText(/ApiError|HTTP 400/)).toBeNull()
    expect(screen.queryByRole("button", { name: /test connection/i })).toBeNull()
  })

  it("offers MLflow settings when the tracking server cannot be reached", async () => {
    mockLogToMlflow.mockRejectedValue(
      new ApiError("HTTP 502", 502, undefined, undefined, {
        error_code: "mlflow_connectivity",
        message: "Could not reach the MLflow tracking server, so the run was not logged.",
      }),
    )
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the MLflow tracking server, so the run was not logged.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Test connection in MLflow settings" }))
    expect(useUIStore.getState().mlflowSettingsOpen).toBe(true)
  })

  it("retries a lost response with the same operation so no duplicate run is created", async () => {
    const onLogAttempted = vi.fn()
    mockLogToMlflow.mockRejectedValueOnce(new TypeError("Failed to fetch"))
    render(<MlflowExportSection {...makeProps({ onLogAttempted })} />)
    fireEvent.click(logButton())
    const retry = await screen.findByRole("button", { name: "Retry" })
    const firstOperation = mockLogToMlflow.mock.calls[0][0].operation_id
    expect(onLogAttempted).toHaveBeenCalledTimes(1)

    mockLogToMlflow.mockResolvedValueOnce({ operation_id: null, logged_at: null, status: "ok", backend: "local", experiment_name: "freq", run_id: "r1", run_url: null, tracking_uri: "file:///C:/proj/mlruns", error: null })
    fireEvent.click(retry)
    await waitFor(() => expect(mockLogToMlflow).toHaveBeenCalledTimes(2))
    expect(mockLogToMlflow.mock.calls[1][0].operation_id).toBe(firstOperation)
    expect(onLogAttempted).toHaveBeenCalledTimes(2)
  })

  it("offers no retry when the server refused the log", async () => {
    mockLogToMlflow.mockRejectedValue(new ApiError("HTTP 400", 400, "Unknown destination.", undefined, "Unknown destination."))
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown destination.")
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
  })

  it("offers no settings link for a permission refusal", async () => {
    mockLogToMlflow.mockRejectedValue(
      new ApiError("HTTP 502", 502, undefined, undefined, {
        error_code: "mlflow_permission",
        message: "MLflow denied permission to log the run.",
      }),
    )
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(logButton())
    expect(await screen.findByRole("alert")).toHaveTextContent("MLflow denied permission")
    expect(screen.queryByRole("button", { name: /test connection/i })).toBeNull()
  })

  it("calls onMlflowResult callback with result", async () => {
    const onResult = vi.fn()
    mockLogToMlflow.mockResolvedValue({ operation_id: null, logged_at: null, status: "ok", backend: "databricks", experiment_name: "test", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps({ onMlflowResult: onResult })} />)
    fireEvent.click(logButton())
    await waitFor(() => {
      expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ status: "ok" }))
    })
  })
})
