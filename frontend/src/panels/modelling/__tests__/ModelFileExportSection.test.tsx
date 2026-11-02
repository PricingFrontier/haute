/**
 * ModelFileExportSection — a destination check answers for the path and the
 * model format it was asked about, so a check for another format is never
 * shown against the current one.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen } from "@testing-library/react"
import type { ModelSaveDestinationResponse } from "../../../api/types"
import { ModelFileExportSection } from "../ModelFileExportSection"

const mockResolve = vi.hoisted(() => vi.fn())
vi.mock("../../../api/client", async (importOriginal) => ({
  ApiError: (await importOriginal<typeof import("../../../api/client")>()).ApiError,
  resolveModelSaveDestination: (...args: unknown[]) => mockResolve(...args),
  saveTrainedModel: vi.fn(),
}))

function held() {
  let resolve!: (response: ModelSaveDestinationResponse) => void
  mockResolve.mockImplementationOnce(() => new Promise<ModelSaveDestinationResponse>((done) => { resolve = done }))
  return (response: ModelSaveDestinationResponse) => act(async () => resolve(response))
}

const CONFIG = { model_export_path: "models/model.cbm" }
const MISMATCH = "The destination extension does not match the model format (.ubj)."

describe("ModelFileExportSection", () => {
  afterEach(cleanup)

  it("re-checks the destination when the model format changes under the same path", async () => {
    const catboostCheck = held()
    const { rerender } = render(
      <ModelFileExportSection trainJobId="job_1" algorithm="catboost" config={CONFIG} onUpdate={vi.fn()} />,
    )
    await catboostCheck({ path: "models/model.cbm", suffix_mismatch: false })
    expect(screen.queryByText(MISMATCH)).toBeNull()

    const xgboostCheck = held()
    rerender(<ModelFileExportSection trainJobId="job_1" algorithm="xgboost" config={CONFIG} onUpdate={vi.fn()} />)
    expect(mockResolve).toHaveBeenLastCalledWith(
      { output_path: "models/model.cbm", algorithm: "xgboost" },
      { signal: expect.any(AbortSignal) },
    )
    // CatBoost's answer says nothing about an XGBoost file.
    await xgboostCheck({ path: "models/model.cbm", suffix_mismatch: true })
    expect(screen.getByRole("alert")).toHaveTextContent(MISMATCH)
    expect(screen.getByRole("button", { name: /Save model to file/ })).toBeDisabled()
  })

  it("shows no mismatch from the previous format while the new format's check is pending", async () => {
    const catboostCheck = held()
    const { rerender } = render(
      <ModelFileExportSection trainJobId="job_1" algorithm="catboost" config={{ model_export_path: "models/model.ubj" }} onUpdate={vi.fn()} />,
    )
    await catboostCheck({ path: "models/model.ubj", suffix_mismatch: true })
    expect(screen.getByRole("alert")).toHaveTextContent("does not match the model format (.cbm)")

    held()
    rerender(<ModelFileExportSection trainJobId="job_1" algorithm="xgboost" config={{ model_export_path: "models/model.ubj" }} onUpdate={vi.fn()} />)
    expect(screen.queryByRole("alert")).toBeNull()
  })
})
