import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import schema from "../../generated/api-contracts.schema.json"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import { REPAIR_OPTIONS_BATCH, useRepairOptions, type RepairTarget } from "../useRepairOptions"

const { fetchRepairOptions } = vi.hoisted(() => ({ fetchRepairOptions: vi.fn() }))

vi.mock("../../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api/client")>(),
  fetchRepairOptions,
}))

type RepairAsk = { sourceRevision: string; targets: RepairTarget[] }

const available = { available: true, code: null, reason: null }

const targets = (count: number): RepairTarget[] => Array.from({ length: count }, (_, index) => ({
  sourceFile: "modules/legacy.py", recoveryId: `node_${index}`,
}))

describe("useRepairOptions", () => {
  beforeEach(() => {
    fetchRepairOptions.mockReset()
    fetchRepairOptions.mockImplementation(async (args: RepairAsk) => ({
      source_revision: args.sourceRevision,
      nodes: args.targets.map((target) => ({
        target_source_file: target.sourceFile, target_recovery_id: target.recoveryId, recover: available, reset: available,
      })),
    }))
    useDocumentStatusStore.getState().reset()
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({
      load_status: "degraded", source_file: "main.py", source_revision: "revision-3",
    }))
  })

  it("asks in batches no larger than the server's answer allows", () => {
    expect(REPAIR_OPTIONS_BATCH).toBe(schema.$defs.PipelineRepairOptionsResponse.properties.nodes.maxItems)
  })

  it("answers every node of an inspector larger than one batch, each batch of the same revision", async () => {
    const asked = targets(REPAIR_OPTIONS_BATCH + 1)
    const { result } = renderHook(() => useRepairOptions(asked))

    await waitFor(() => expect(result.current.offersFor(asked.at(-1)!).recover).toEqual({ state: "available" }))
    expect(fetchRepairOptions.mock.calls.map(([args]) => (args as RepairAsk).targets.length)).toEqual([REPAIR_OPTIONS_BATCH, 1])
    expect(fetchRepairOptions.mock.calls.map(([args]) => (args as RepairAsk).sourceRevision)).toEqual(["revision-3", "revision-3"])
    expect(asked.every((target) => result.current.offersFor(target).reset.state === "available")).toBe(true)
  })

  it("admits nothing for any node when one batch fails", async () => {
    fetchRepairOptions.mockImplementationOnce(async (args: RepairAsk) => ({
      source_revision: args.sourceRevision,
      nodes: args.targets.map((target) => ({
        target_source_file: target.sourceFile, target_recovery_id: target.recoveryId, recover: available, reset: available,
      })),
    })).mockRejectedValueOnce(new Error("offline"))
    const asked = targets(REPAIR_OPTIONS_BATCH + 1)
    const { result } = renderHook(() => useRepairOptions(asked))

    await waitFor(() => expect(result.current.offersFor(asked[0]).recover).toEqual({ state: "failed", reason: "offline" }))
    expect(result.current.offersFor(asked.at(-1)!).reset).toEqual({ state: "failed", reason: "offline" })
  })
})
