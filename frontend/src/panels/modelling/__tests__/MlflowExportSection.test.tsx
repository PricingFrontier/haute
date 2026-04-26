import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react"
import { MlflowExportSection } from "../MlflowExportSection"

vi.mock("../../../api/client", () => ({
  logToMlflow: vi.fn(),
}))

vi.mock("../../../utils/configField", () => ({
  configField: vi.fn((_config: Record<string, unknown>, key: string, def: string) => _config[key] ?? def),
}))

import { logToMlflow } from "../../../api/client"
const mockLogToMlflow = vi.mocked(logToMlflow)

function makeProps(overrides: Partial<Parameters<typeof MlflowExportSection>[0]> = {}) {
  return {
    trainJobId: "job_123",
    mlflowBackend: { installed: true, backend: "databricks", host: "https://example.com" },
    config: {} as Record<string, unknown>,
    ...overrides,
  }
}

describe("MlflowExportSection", () => {
  beforeEach(() => {
    mockLogToMlflow.mockReset()
  })

  afterEach(cleanup)

  it("renders the log button with backend name", () => {
    render(<MlflowExportSection {...makeProps()} />)
    expect(screen.getByText(/Log to MLflow \(databricks\)/)).toBeInTheDocument()
  })

  it("clicking button calls logToMlflow", async () => {
    mockLogToMlflow.mockResolvedValue({ status: "ok", backend: "mlflow", experiment_name: "test_exp", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByText(/Log to MLflow/))
    await waitFor(() => expect(mockLogToMlflow).toHaveBeenCalledOnce())
  })

  it("shows success result after logging", async () => {
    mockLogToMlflow.mockResolvedValue({
      status: "ok",
      backend: "mlflow",
      experiment_name: "pricing_model",
      run_id: "run_abc",
      run_url: "https://example.com/run/abc",
      tracking_uri: "http://localhost:5000",
      error: null,
    })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByText(/Log to MLflow/))
    await waitFor(() => {
      expect(screen.getByText(/Logged to pricing_model/)).toBeInTheDocument()
      expect(screen.getByText("Open in Databricks")).toBeInTheDocument()
    })
  })

  it("shows error result on failure", async () => {
    mockLogToMlflow.mockResolvedValue({ status: "error", backend: "mlflow", experiment_name: "", run_id: null, run_url: null, tracking_uri: "", error: "Experiment not found" })
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByText(/Log to MLflow/))
    await waitFor(() => {
      expect(screen.getByText("Experiment not found")).toBeInTheDocument()
    })
  })

  it("shows error when logToMlflow throws", async () => {
    mockLogToMlflow.mockRejectedValue(new Error("Network error"))
    render(<MlflowExportSection {...makeProps()} />)
    fireEvent.click(screen.getByText(/Log to MLflow/))
    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument()
    })
  })

  it("calls onMlflowResult callback with result", async () => {
    const onResult = vi.fn()
    mockLogToMlflow.mockResolvedValue({ status: "ok", backend: "mlflow", experiment_name: "test", run_id: null, run_url: null, tracking_uri: "", error: null })
    render(<MlflowExportSection {...makeProps({ onMlflowResult: onResult })} />)
    fireEvent.click(screen.getByText(/Log to MLflow/))
    await waitFor(() => {
      expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ status: "ok" }))
    })
  })
})
