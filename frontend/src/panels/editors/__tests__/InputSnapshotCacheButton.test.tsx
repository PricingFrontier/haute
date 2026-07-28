import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../../api/client", () => ({
  ApiError: class ApiError extends Error {},
  buildInputCache: vi.fn(),
  cancelInputCacheJob: vi.fn(),
  clearInputCache: vi.fn(),
  getInputCacheJob: vi.fn(),
  getInputCacheStatus: vi.fn(),
}))

import {
  buildInputCache,
  cancelInputCacheJob,
  clearInputCache,
  getInputCacheJob,
  getInputCacheStatus,
} from "../../../api/client"
import InputSnapshotCacheButton from "../_InputSnapshotCacheButton"

const config = {
  inputType: "file",
  cacheMode: "snapshot",
  format: "csv",
  mode: "scan",
  path: "quotes.csv",
}
const missing = {
  schema_version: 1 as const,
  identity_digest: "snapshot",
  state: "missing" as const,
  freshness: "unknown" as const,
  generation: null,
}
const ready = {
  schema_version: 1 as const,
  identity_digest: "snapshot",
  state: "ready" as const,
  freshness: "fresh" as const,
  generation: {
    generation_id: "generation",
    row_count: 3,
    column_count: 2,
    size_bytes: 128,
    created_at: 1,
    build_class: "bounded" as const,
    columns: {},
  },
}

function renderButton(props: Partial<ComponentProps<typeof InputSnapshotCacheButton>> = {}) {
  return render(<InputSnapshotCacheButton config={config} admittedEager={false} requiredReady {...props} />)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getInputCacheStatus).mockResolvedValue(missing)
  vi.mocked(buildInputCache).mockResolvedValue({
    schema_version: 1,
    job_id: "job-1",
    identity_digest: "snapshot",
    status: "running",
    joined: false,
  })
  vi.mocked(getInputCacheJob).mockResolvedValue({
    schema_version: 1,
    job_id: "job-1",
    identity_digest: "snapshot",
    status: "completed",
    terminal_reason: null,
    message: "Snapshot ready.",
    refresh: false,
    build_class: "bounded",
    progress: { phase: "completed", rows: 3, batches: 1, bytes: 128, elapsed_seconds: 0 },
    snapshot: ready,
    error_code: null,
  })
})

afterEach(cleanup)

describe("InputSnapshotCacheButton", () => {
  it("shows the missing-state hint and builds a snapshot to completion", async () => {
    renderButton()
    expect(await screen.findByRole("button", { name: "Cache as Parquet" })).toBeInTheDocument()
    expect(screen.getByText(/No cache yet/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Cache as Parquet" }))

    await waitFor(() =>
      expect(buildInputCache).toHaveBeenCalledWith({
        schema_version: 1,
        config,
        refresh: false,
        profile: "lazy_sink",
      }),
    )
    expect(await screen.findByRole("button", { name: "Refresh Cache" })).toBeInTheDocument()
    expect(screen.getByText("3 rows")).toBeInTheDocument()
  })

  it("uses the admitted-eager profile", async () => {
    renderButton({ admittedEager: true })
    fireEvent.click(await screen.findByRole("button", { name: "Cache as Parquet" }))
    await waitFor(() => expect(buildInputCache).toHaveBeenCalledWith(expect.objectContaining({ profile: "preview_eager" })))
  })

  it("does not query or build until the snapshot configuration is ready", () => {
    renderButton({ requiredReady: false })
    expect(screen.getByRole("button", { name: "Cache as Parquet" })).toBeDisabled()
    expect(getInputCacheStatus).not.toHaveBeenCalled()
    expect(buildInputCache).not.toHaveBeenCalled()
  })

  it("shows stale readiness, cancels an active build, and clears a ready snapshot", async () => {
    vi.mocked(getInputCacheStatus).mockResolvedValueOnce({ ...ready, freshness: "stale" })
    let finishJob: ((value: Awaited<ReturnType<typeof getInputCacheJob>>) => void) | undefined
    vi.mocked(getInputCacheJob).mockImplementationOnce(
      () => new Promise((resolve) => { finishJob = resolve }),
    )
    vi.mocked(cancelInputCacheJob).mockResolvedValue({
      schema_version: 1,
      job_id: "job-1",
      cancellation_requested: true,
      status: "running",
    })
    vi.mocked(clearInputCache).mockResolvedValue(missing)
    renderButton()
    expect(await screen.findByText("Source changed since cache — Refresh to update.")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Refresh Cache" }))
    fireEvent.click(await screen.findByRole("button", { name: /Cancel/ }))
    await waitFor(() => expect(cancelInputCacheJob).toHaveBeenCalledWith("job-1"))
    finishJob?.({
      schema_version: 1, job_id: "job-1", identity_digest: "snapshot", status: "cancelled",
      terminal_reason: "cancelled", message: "Cancelled", refresh: true, build_class: "bounded",
      progress: { phase: "cancelled", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 }, snapshot: null, error_code: null,
    })

    await screen.findByRole("alert")

    fireEvent.click(screen.getByTitle("Delete cached data"))
    await waitFor(() => expect(clearInputCache).toHaveBeenCalledWith({ schema_version: 1, config }))
  })

  it("keeps a newer resource job cancellable when an older job finishes", async () => {
    const nextConfig = { ...config, path: "renewals.csv" }
    let finishFirst: ((value: Awaited<ReturnType<typeof getInputCacheJob>>) => void) | undefined
    let finishSecond: ((value: Awaited<ReturnType<typeof getInputCacheJob>>) => void) | undefined
    vi.mocked(buildInputCache)
      .mockResolvedValueOnce({
        schema_version: 1, job_id: "job-1", identity_digest: "snapshot-1",
        status: "running", joined: false,
      })
      .mockResolvedValueOnce({
        schema_version: 1, job_id: "job-2", identity_digest: "snapshot-2",
        status: "running", joined: false,
      })
    vi.mocked(getInputCacheJob).mockImplementation((jobId) => (
      new Promise((resolve) => {
        if (jobId === "job-1") finishFirst = resolve
        else finishSecond = resolve
      })
    ))
    vi.mocked(cancelInputCacheJob).mockResolvedValue({
      schema_version: 1,
      job_id: "job-2",
      cancellation_requested: true,
      status: "running",
    })

    const { rerender } = renderButton()
    fireEvent.click(await screen.findByRole("button", { name: "Cache as Parquet" }))
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalledWith("job-1"))

    rerender(
      <InputSnapshotCacheButton
        config={nextConfig}
        admittedEager={false}
        requiredReady
      />,
    )
    fireEvent.click(await screen.findByRole("button", { name: "Cache as Parquet" }))
    await waitFor(() => expect(getInputCacheJob).toHaveBeenCalledWith("job-2"))

    await act(async () => {
      finishFirst?.({
        schema_version: 1, job_id: "job-1", identity_digest: "snapshot-1",
        status: "cancelled", terminal_reason: "cancelled",
        message: "Cache build cancelled", refresh: false, build_class: "bounded",
        progress: { phase: "cancelled", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 },
        snapshot: null, error_code: null,
      })
      await Promise.resolve()
    })

    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }))
    expect(cancelInputCacheJob).toHaveBeenCalledWith("job-2")

    await act(async () => {
      finishSecond?.({
        schema_version: 1, job_id: "job-2", identity_digest: "snapshot-2",
        status: "cancelled", terminal_reason: "cancelled",
        message: "Cache build cancelled", refresh: false, build_class: "bounded",
        progress: { phase: "cancelled", rows: 0, batches: 0, bytes: 0, elapsed_seconds: 0 },
        snapshot: null, error_code: null,
      })
      await Promise.resolve()
    })
  })
})
