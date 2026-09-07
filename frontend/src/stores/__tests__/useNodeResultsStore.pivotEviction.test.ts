import { beforeEach, describe, expect, it } from "vitest"

import type { ExplorePivotResult } from "../../api/types"
import useNodeResultsStore, {
  explorePivotResultKey,
  MAX_CACHED_EXPLORE_PIVOT_RESULTS,
  resetNodeResultsDerivedCaches,
} from "../useNodeResultsStore"

function makePivotResult(nodeId: string, pivotId: string): ExplorePivotResult {
  return {
    version: 1,
    node_id: nodeId,
    pivot_id: pivotId,
    source: "test-source",
    dataframe_cache_key: `df:${nodeId}`,
    calculation_key: `calc:${nodeId}:${pivotId}`,
    row_fields: ["category"],
    column_fields: [],
    values: [{ id: "val", field: "val", aggregation: "sum" }],
    row_paths: [],
    column_paths: [],
    cells: [],
    warnings: [],
    generated_at: 1,
    execution_metrics: null,
  }
}

function recordPivotResult(nodeId: string, pivotId: string): string {
  const store = useNodeResultsStore.getState()
  const key = explorePivotResultKey(nodeId, pivotId)
  store.startExplorePivotJob(
    key,
    `job-${nodeId}-${pivotId}`,
    nodeId,
    pivotId,
    `Node ${nodeId}`,
    `Pivot ${pivotId}`,
    `identity-${nodeId}-${pivotId}`,
    "test-source",
    0,
  )
  store.completeExplorePivotJob(key, makePivotResult(nodeId, pivotId))
  return key
}

describe("useNodeResultsStore pivot cache eviction", () => {
  beforeEach(() => {
    resetNodeResultsDerivedCaches()
    useNodeResultsStore.setState({
      pivotResults: {},
      pivotJobs: {},
      pivotStartClaims: {},
      pinnedPreviewNodeId: null,
    })
  })

  it("evicts unpinned keys in least-recent order and preserves all pinned node pivot entries when at bound", () => {
    const pinnedNodeId = "explore-pinned"
    useNodeResultsStore.getState().setPinnedPreviewNodeId(pinnedNodeId)

    const pinnedKeys: string[] = []
    for (let i = 0; i < 5; i += 1) {
      pinnedKeys.push(recordPivotResult(pinnedNodeId, `pinned-pivot-${i}`))
    }

    const unpinnedCount = MAX_CACHED_EXPLORE_PIVOT_RESULTS - pinnedKeys.length
    const unpinnedKeys: string[] = []
    for (let i = 0; i < unpinnedCount; i += 1) {
      unpinnedKeys.push(recordPivotResult(`unpinned-node-${i}`, `pivot-${i}`))
    }

    const stateAtBound = useNodeResultsStore.getState()
    expect(Object.keys(stateAtBound.pivotResults)).toHaveLength(MAX_CACHED_EXPLORE_PIVOT_RESULTS)
    for (const key of pinnedKeys) {
      expect(stateAtBound.pivotResults[key]).toBeDefined()
    }
    for (const key of unpinnedKeys) {
      expect(stateAtBound.pivotResults[key]).toBeDefined()
    }

    // Adding an unpinned entry when at bound must evict the least-recently-touched unpinned key.
    const extraKey1 = recordPivotResult("extra-node-1", "pivot-extra-1")
    const stateAfterOneExtra = useNodeResultsStore.getState()
    expect(Object.keys(stateAfterOneExtra.pivotResults)).toHaveLength(MAX_CACHED_EXPLORE_PIVOT_RESULTS)
    // Oldest unpinned key was unpinnedKeys[0]
    expect(stateAfterOneExtra.pivotResults[unpinnedKeys[0]]).toBeUndefined()
    expect(stateAfterOneExtra.pivotResults[extraKey1]).toBeDefined()
    for (const key of pinnedKeys) {
      expect(stateAfterOneExtra.pivotResults[key]).toBeDefined()
    }

    // Adding more unpinned entries continues evicting unpinned keys only, never pinned keys
    const extraKey2 = recordPivotResult("extra-node-2", "pivot-extra-2")
    const stateAfterTwoExtra = useNodeResultsStore.getState()
    expect(Object.keys(stateAfterTwoExtra.pivotResults)).toHaveLength(MAX_CACHED_EXPLORE_PIVOT_RESULTS)
    expect(stateAfterTwoExtra.pivotResults[unpinnedKeys[1]]).toBeUndefined()
    expect(stateAfterTwoExtra.pivotResults[extraKey2]).toBeDefined()
    for (const key of pinnedKeys) {
      expect(stateAfterTwoExtra.pivotResults[key]).toBeDefined()
    }
  })

  it("does not evict any entries when the pinned node's entries alone exceed the bound", () => {
    const pinnedNodeId = "explore-pinned"
    useNodeResultsStore.getState().setPinnedPreviewNodeId(pinnedNodeId)

    const totalPinnedEntries = MAX_CACHED_EXPLORE_PIVOT_RESULTS + 5
    const pinnedKeys: string[] = []
    for (let i = 0; i < totalPinnedEntries; i += 1) {
      pinnedKeys.push(recordPivotResult(pinnedNodeId, `pinned-pivot-${i}`))
    }

    const state = useNodeResultsStore.getState()
    expect(Object.keys(state.pivotResults)).toHaveLength(totalPinnedEntries)
    for (const key of pinnedKeys) {
      expect(state.pivotResults[key]).toBeDefined()
    }
  })
})
