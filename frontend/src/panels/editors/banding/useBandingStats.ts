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

interface Tagged<T> {
  identity: string
  value: T
}

interface BandingStatsRequest {
  controller: AbortController | null
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
  const [stats, setStats] = useState<Tagged<BandingStatsResponse | null> | null>(null)
  const [loading, setLoading] = useState<Tagged<boolean> | null>(null)
  const [error, setError] = useState<Tagged<string | null> | null>(null)
  const inFlight = useRef<BandingStatsRequest | null>(null)
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
  const requestIdentity = JSON.stringify({ nodeId, askedFor, activeSource, available, dataVersion })

  useEffect(() => {
    if (!nodeId || !askedFor || available !== "current") {
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
    const request: BandingStatsRequest = { controller: null }
    const timer = setTimeout(() => {
      const controller = new AbortController()
      request.controller = controller
      inFlight.current = request
      setLoading({ identity: requestIdentity, value: true })
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
          if (
            controller.signal.aborted ||
            inFlight.current !== request ||
            !isDocumentExecutionFenceCurrent(fence)
          ) return
          setStats({ identity: requestIdentity, value: response })
          setError({ identity: requestIdentity, value: null })
        })
        .catch((err: unknown) => {
          if (
            controller.signal.aborted ||
            inFlight.current !== request ||
            !isDocumentExecutionFenceCurrent(fence)
          ) return
          // The editor keeps working from preview rows, and says why it had to:
          // the server's own message — which for bad rules is execution's —
          // rather than the bare "HTTP 422" the client builds as the message.
          setStats({ identity: requestIdentity, value: null })
          setError({ identity: requestIdentity, value: apiErrorMessage(err, "the data could not be counted") })
        })
        .finally(() => {
          if (inFlight.current !== request) return
          inFlight.current = null
          setLoading({ identity: requestIdentity, value: false })
        })
    }, BANDING_STATS_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      request.controller?.abort()
      if (inFlight.current === request) {
        inFlight.current = null
        setLoading({ identity: requestIdentity, value: false })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, askedFor, available, dataVersion, activeSource])

  // Full-data numbers are shown only while the point they describe is the one
  // this node reads *now*: a stale point shows the sample instead, however
  // recently its statistics arrived.
  const matchingStats = stats?.identity === requestIdentity ? stats.value : null
  const current =
    available === "current" &&
    matchingStats?.status === "ok" &&
    matchingStats.data_version === dataVersion
  return {
    cache,
    stats: current ? matchingStats : null,
    loading: loading?.identity === requestIdentity ? loading.value : false,
    error: error?.identity === requestIdentity ? error.value : null,
    basis: current ? "all" : available === "stale" ? "stale" : "sample",
  }
}
