import { useMemo } from "react"

import { getRatingLevels } from "../../../api/client"
import type { RatingLevelsResponse } from "../../../api/types"
import type { NodeDataCache } from "../../../hooks/useNodeDataCache"
import type { SimpleEdge, SimpleNode } from "../_shared"
import useWholeDataAnswer, { type WholeDataBasis } from "../shared/useWholeDataAnswer"

/** Where the levels on screen came from. */
export type RatingLevelsBasis = WholeDataBasis

export interface RatingLevelsState {
  /** The shared cache of the data this node reads, for the header control. */
  cache: NodeDataCache
  /** Levels by column, empty until the whole dataset has answered for them. */
  levels: Record<string, string[]>
  /** The rows those levels were read from, for the editor to say so. */
  totalRows: number
  loading: boolean
  basis: RatingLevelsBasis
  error: string | null
}

export interface UseRatingLevelsInput {
  node: SimpleNode | null
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  /**
   * The raw factor columns the tables rate on. Banded outputs are not among
   * them: their levels come from the banding config, which is the authority on
   * what a band is called before any data is read.
   */
  columns: string[]
}

const NO_LEVELS: Record<string, string[]> = {}

/** Whole-dataset levels for the raw factor columns a Rating Step rates on. */
export default function useRatingLevels({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  columns,
}: UseRatingLevelsInput): RatingLevelsState {
  // What the request is about: the columns, once each, in a stable order, so
  // reordering a table's factors does not ask the same question again.
  const askedFor = useMemo(() => {
    const wanted = [...new Set(columns.filter((column) => column.trim() !== ""))].sort()
    return wanted.length > 0 ? JSON.stringify(wanted) : null
  }, [columns])
  const { cache, answer, loading, basis, error } = useWholeDataAnswer<RatingLevelsResponse>({
    node,
    allNodes,
    edges,
    submodels,
    preamble,
    askedFor,
    ask: ({ asked, graph, nodeId, source, signal }) =>
      getRatingLevels({
        graph,
        node_id: nodeId,
        source,
        columns: JSON.parse(asked) as string[],
        signal,
      }),
    failureMessage: "the levels could not be read",
  })
  const levels = useMemo(() => {
    if (!answer) return NO_LEVELS
    const byColumn: Record<string, string[]> = {}
    for (const column of answer.columns) {
      if (column.values.length > 0) {
        byColumn[column.column] = column.values.map((value) => value.value)
      }
    }
    return byColumn
  }, [answer])

  return {
    cache,
    levels,
    totalRows: answer ? answer.total_rows : 0,
    loading,
    error,
    basis,
  }
}
