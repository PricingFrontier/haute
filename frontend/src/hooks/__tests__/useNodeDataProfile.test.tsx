/**
 * The profile of the data a consumer reads, asked for by the consumer's node in its
 * own occurrence: a Stop pressed for one occurrence's child leaves another's alone.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"

import type { NodeDataProfileResponse } from "../../api/types"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useNodeDataStore from "../../stores/useNodeDataStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { SimpleNode } from "../../panels/editors/_shared"
import { makeIdentifiedNode } from "../../test-utils/factories"
import { COPY, OWNER, loadTwoOccurrences, onCanvas, openOccurrence } from "../../testSupport/twoOccurrencesFixture"
import type { NodeDataCache } from "../useNodeDataCache"
import useNodeDataProfile from "../useNodeDataProfile"

vi.mock("../../api/client", () => ({
  getNodeDataProfile: vi.fn(),
  cancelNodeData: vi.fn(),
}))

import { getNodeDataProfile } from "../../api/client"

/** A consumer's current data, under the slot of its own occurrence. */
function currentData(slotKey: string): NodeDataCache {
  return {
    point: { slot_key: slotKey, data_version: "gen-1" },
    availability: "current",
    dataVersion: "gen-1",
  } as unknown as NodeDataCache
}

describe("useNodeDataProfile in each occurrence of a definition", () => {
  beforeEach(() => {
    vi.mocked(getNodeDataProfile).mockReset()
    useNodeDataStore.getState().reset()
    useSettingsStore.setState({ activeSource: "live", sources: ["live"] })
    useDocumentStatusStore.setState({
      sourceFile: "main.py",
      executionGeneration: 1,
      loadStatus: "ready",
      capabilities: { can_execute: true } as never,
      graphSynchronized: true,
    })
    loadTwoOccurrences([makeIdentifiedNode("explore", "explore")])
  })

  afterEach(() => {
    cleanup()
    useGraphStore.getState().resetForTests()
  })

  it("asks for the copy's profile after the owner's was stopped", async () => {
    act(() => openOccurrence(OWNER))
    vi.mocked(getNodeDataProfile).mockReturnValue(new Promise<NodeDataProfileResponse>(() => {}))
    const render = (cache: NodeDataCache) => ({
      node: onCanvas("explore") as unknown as SimpleNode,
      allNodes: [],
      edges: [],
      cache,
    })
    const { result, rerender } = renderHook((input: ReturnType<typeof render>) => useNodeDataProfile(input), {
      initialProps: render(currentData("owner-slot")),
    })
    await waitFor(() => expect(getNodeDataProfile).toHaveBeenCalledOnce())
    expect(vi.mocked(getNodeDataProfile).mock.calls[0][0].node_id).toBe("submodel_runtime/pricing/explore")
    await act(async () => result.current.cancel())

    // The same panel, now showing the copy's child and the data it reads.
    act(() => openOccurrence(COPY))
    rerender(render(currentData("copy-slot")))

    await waitFor(() => expect(getNodeDataProfile).toHaveBeenCalledTimes(2))
    expect(vi.mocked(getNodeDataProfile).mock.calls[1][0].node_id).toBe("submodel_runtime/pricing_2/explore")
  })
})
