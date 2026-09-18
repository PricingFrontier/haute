import { useEffect, useMemo, useRef, useState } from "react"

import { getBandingStats } from "../../../api/client"
import { apiErrorMessage } from "../../../api/errors"
import type { BandingStatsResponse } from "../../../api/types"
import useNodeDataCache, { type NodeDataCache } from "../../../hooks/useNodeDataCache"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../../../stores/useDocumentStatusStore"
import useSettingsStore from "../../../stores/useSettingsStore"
import { buildGraph } from "../../../utils/buildGraph"
import type { SimpleEdge, SimpleNode } from "../_shared"
import type { BandingFactor } from "../../../types/banding"

/** How long the editor waits after the last edit before asking about it. */
export const BANDING_STATS_DEBOUNCE_MS = 250

/** Where the numbers on screen came from. */
export type BandingStatsBasis = "all" | "sample" | "stale"

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

/**
 * Whole-dataset statistics for the factor being edited.
 *
 * The editor asks only when the point it reads is current, so the numbers it
 * shows are either the whole dataset's or plainly labelled as a sample — never
 * full-data counts drawn from data that has moved on. An edit supersedes the
 * request before it: the previous one is aborted, and its answer could not be
 * published anyway, because every answer is checked against the identity and
 * the document it was asked under.
 */
export default function useBandingStats({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  factor,
  histogramBins,
}: UseBandingStatsInput): BandingStatsState {
  const activeSource = useSettingsStore((s) => s.activeSource)
  const cache = useNodeDataCache({ node, allNodes, edges, submodels, preamble })
  const [stats, setStats] = useState<BandingStatsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef<AbortController | null>(null)
  const nodeId = node?.id ?? null

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
          })
        : null,
    [factor, histogramBins],
  )
  const available = cache.availability
  const dataVersion = cache.dataVersion

  useEffect(() => {
    if (!nodeId || !askedFor || available !== "current") {
      // Nothing to ask about, or nothing current to ask. Any answer in flight
      // is abandoned; what is on screen is not shown as this data, because the
      // reading below requires a current point.
      inFlight.current?.abort()
      inFlight.current = null
      return
    }
    const asked = JSON.parse(askedFor) as {
      column: string
      banding: string
      rules: unknown[]
      rightClosed: boolean
      bins: number | null
    }
    const fence = captureDocumentExecutionFence()
    const timer = setTimeout(() => {
      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller
      setLoading(true)
      getBandingStats({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: nodeId,
        source: activeSource,
        factor: {
          banding: asked.banding,
          column: asked.column,
          outputColumn: factor?.outputColumn ?? "",
          rules: asked.rules,
          rightClosed: asked.rightClosed,
        },
        ...(asked.bins === null ? {} : { histogramBins: asked.bins }),
        signal: controller.signal,
      })
        .then((response) => {
          if (controller.signal.aborted || !isDocumentExecutionFenceCurrent(fence)) return
          setStats(response)
          setError(null)
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted || !isDocumentExecutionFenceCurrent(fence)) return
          // The editor keeps working from preview rows, and says why it had to:
          // the server's own message — which for bad rules is execution's —
          // rather than the bare "HTTP 422" the client builds as the message.
          setStats(null)
          setError(apiErrorMessage(err, "the data could not be counted"))
        })
        .finally(() => {
          if (inFlight.current === controller) inFlight.current = null
          // A superseded request leaves the flag to its successor, which is
          // still running; the last one standing clears it.
          if (inFlight.current === null) setLoading(false)
        })
    }, BANDING_STATS_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, askedFor, available, dataVersion, activeSource])

  useEffect(() => () => inFlight.current?.abort(), [])

  // Full-data numbers are shown only while the point they describe is the one
  // this node reads *now*: a stale point shows the sample instead, however
  // recently its statistics arrived.
  const current =
    available === "current" && stats?.status === "ok" && stats.data_version === dataVersion
  return {
    cache,
    stats: current ? stats : null,
    loading,
    error,
    basis: current ? "all" : available === "stale" ? "stale" : "sample",
  }
}
