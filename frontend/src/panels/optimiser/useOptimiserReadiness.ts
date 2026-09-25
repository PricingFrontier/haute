import { useMemo } from "react"
import { useDataInputColumns } from "../../hooks/useDataInputColumns"
import type { SimpleEdge, SimpleNode } from "../editors"
import { optimiserSolveReadiness, resolveOptimiserInputs } from "./solveReadiness"

type Column = { name: string; dtype: string }

const EMPTY_COLUMNS: Column[] = []

type UseOptimiserReadinessArgs = {
  nodeId: string
  config: Record<string, unknown>
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  /**
   * Columns already known for the data input. They stand in for a fetch only
   * with exactly one resolvable input: a multi-input union would mix data and
   * factor-table fields.
   */
  fallbackColumns: Column[]
  /** Whether the input's columns may be fetched when neither they nor a cache are known. */
  fetchColumns: boolean
}

/**
 * The optimiser's resolved inputs, data-input columns and solve readiness, for
 * every surface that can start a solve. Columns come from one path — the known
 * fallback, else the source-aware column cache and preview fetch — so the Solve
 * pane and the result preview's Re-run judge the same columns.
 */
export function useOptimiserReadiness({
  nodeId,
  config,
  allNodes,
  edges,
  submodels,
  fallbackColumns,
  fetchColumns,
}: UseOptimiserReadinessArgs) {
  const inputs = useMemo(
    () => resolveOptimiserInputs(nodeId, config, allNodes, edges, submodels),
    [nodeId, config, allNodes, edges, submodels],
  )
  // Preview the optimiser itself so execution follows its exact selected edge.
  const fallbackDataInputColumns = inputs.hasResolvableDataInput && inputs.inputNodes.length === 1
    ? fallbackColumns
    : EMPTY_COLUMNS
  const hasFallbackColumns = fallbackDataInputColumns.length > 0
  const fetchedDataInputColumns = useDataInputColumns(
    inputs.hasResolvableDataInput ? nodeId : "",
    allNodes,
    edges,
    submodels,
    undefined,
    { enabled: !hasFallbackColumns && fetchColumns, fallbackColumns: fallbackDataInputColumns },
  )
  const dataInputColumns = hasFallbackColumns ? fallbackDataInputColumns : fetchedDataInputColumns
  const readiness = optimiserSolveReadiness(config, inputs, dataInputColumns)
  return { inputs, dataInputColumns, ...readiness }
}
