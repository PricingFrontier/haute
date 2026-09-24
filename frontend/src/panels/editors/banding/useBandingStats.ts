import { useMemo } from "react"

import { getBandingStats } from "../../../api/client"
import type { BandingStatsResponse } from "../../../api/types"
import type { NodeDataCache } from "../../../hooks/useNodeDataCache"
import type { SimpleEdge, SimpleNode } from "../_shared"
import type { BandingFactor } from "../../../types/banding"
import useWholeDataAnswer, { type WholeDataBasis } from "../shared/useWholeDataAnswer"

/** Where the numbers on screen came from. */
export type BandingStatsBasis = WholeDataBasis

export interface BandingStatsState {
  /** The shared cache of the data this node reads, for the header control. */
  cache: NodeDataCache
  /** The last statistics received, kept while a newer request is in flight. */
  stats: BandingStatsResponse | null
  loading: boolean
  basis: BandingStatsBasis
  error: string | null
}

export interface UseBandingStatsInput {
  node: SimpleNode | null
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  /** The factor in the editor, so the counts follow what is being edited. */
  factor: BandingFactor | null
  histogramBins?: number
}

interface AskedFactor {
  column: string
  banding: string
  rules: unknown[]
  rightClosed: boolean
  bins: number | null
}

/** Whole-dataset statistics for the factor being edited. */
export default function useBandingStats({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  factor,
  histogramBins,
}: UseBandingStatsInput): BandingStatsState {
  // What the request is about. A factor edit that cannot change the numbers —
  // renaming the output column, say — does not ask again.
  const askedFor = useMemo(
    () =>
      factor && factor.column
        ? JSON.stringify({
            column: factor.column,
            banding: factor.banding,
            rules: factor.rules ?? [],
            rightClosed: factor.rightClosed ?? true,
            bins: histogramBins ?? null,
          } satisfies AskedFactor)
        : null,
    [factor, histogramBins],
  )
  const outputColumn = factor?.outputColumn ?? ""
  const { cache, answer, loading, basis, error } = useWholeDataAnswer<BandingStatsResponse>({
    node,
    allNodes,
    edges,
    submodels,
    preamble,
    askedFor,
    ask: ({ asked, graph, nodeId, source, signal }) => {
      const question = JSON.parse(asked) as AskedFactor
      return getBandingStats({
        graph,
        node_id: nodeId,
        source,
        factor: {
          banding: question.banding,
          column: question.column,
          outputColumn,
          rules: question.rules,
          rightClosed: question.rightClosed,
        },
        ...(question.bins === null ? {} : { histogramBins: question.bins }),
        signal,
      })
    },
    failureMessage: "the data could not be counted",
  })
  return { cache, stats: answer, loading, basis, error }
}
