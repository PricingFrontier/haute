import { useEffect, useMemo, useRef, useState } from "react"

import { getRatingLevels } from "../../../api/client"
import { apiErrorMessage } from "../../../api/errors"
import type { RatingLevelsResponse } from "../../../api/types"
import useNodeDataCache, { type NodeDataCache } from "../../../hooks/useNodeDataCache"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../../../stores/useDocumentStatusStore"
import useSettingsStore from "../../../stores/useSettingsStore"
import { buildGraph } from "../../../utils/buildGraph"
import type { SimpleEdge, SimpleNode } from "../_shared"

/** How long the editor waits after the last change before asking again. */
export const RATING_LEVELS_DEBOUNCE_MS = 250

/** Where the levels on screen came from. */
export type RatingLevelsBasis = "all" | "sample" | "stale"

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

/**
 * Whole-dataset levels for the raw factor columns a Rating Step rates on.
 *
 * The editor asks only when the point it reads is current, so the levels it
 * offers are either the whole dataset's or plainly the preview's — never a
 * list drawn from data the node has moved on from. A change of factors
 * supersedes the request before it, and every answer is checked against the
 * identity and the document it was asked under before it is published.
 */
export default function useRatingLevels({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  columns,
}: UseRatingLevelsInput): RatingLevelsState {
  const activeSource = useSettingsStore((s) => s.activeSource)
  const cache = useNodeDataCache({ node, allNodes, edges, submodels, preamble })
  const [answer, setAnswer] = useState<RatingLevelsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef<AbortController | null>(null)
  const nodeId = node?.id ?? null

  // What the request is about: the columns, once each, in a stable order, so
  // reordering a table's factors does not ask the same question again.
  const askedFor = useMemo(() => {
    const wanted = [...new Set(columns.filter((column) => column.trim() !== ""))].sort()
    return wanted.length > 0 ? JSON.stringify(wanted) : null
  }, [columns])
  const available = cache.availability
  const dataVersion = cache.dataVersion

  useEffect(() => {
    if (!nodeId || !askedFor || available !== "current") {
      // Nothing to ask about, or nothing current to ask. Any answer in flight
      // is abandoned; what is on screen is not offered as this data, because
      // the reading below requires a current point.
      inFlight.current?.abort()
      inFlight.current = null
      return
    }
    const asked = JSON.parse(askedFor) as string[]
    const fence = captureDocumentExecutionFence()
    const timer = setTimeout(() => {
      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller
      setLoading(true)
      getRatingLevels({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: nodeId,
        source: activeSource,
        columns: asked,
        signal: controller.signal,
      })
        .then((response) => {
          if (controller.signal.aborted || !isDocumentExecutionFenceCurrent(fence)) return
          setAnswer(response)
          setError(null)
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted || !isDocumentExecutionFenceCurrent(fence)) return
          // The editor keeps working from preview levels, and says why it had
          // to: the server's own message rather than the bare "HTTP 422" the
          // client builds as the error's message.
          setAnswer(null)
          setError(apiErrorMessage(err, "the levels could not be read"))
        })
        .finally(() => {
          if (inFlight.current === controller) inFlight.current = null
          // A superseded request leaves the flag to its successor, which is
          // still running; the last one standing clears it.
          if (inFlight.current === null) setLoading(false)
        })
    }, RATING_LEVELS_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, askedFor, available, dataVersion, activeSource])

  useEffect(() => () => inFlight.current?.abort(), [])

  // Whole-dataset levels are offered only while the point they came from is
  // the one this node reads *now*: a stale point falls back to the preview,
  // however recently its levels arrived.
  const current =
    available === "current" && answer?.status === "ok" && answer.data_version === dataVersion
  const levels = useMemo(() => {
    if (!current || !answer) return NO_LEVELS
    const byColumn: Record<string, string[]> = {}
    for (const column of answer.columns) {
      if (column.values.length > 0) {
        byColumn[column.column] = column.values.map((value) => value.value)
      }
    }
    return byColumn
  }, [current, answer])

  return {
    cache,
    levels,
    totalRows: current && answer ? answer.total_rows : 0,
    loading,
    error,
    basis: current ? "all" : available === "stale" ? "stale" : "sample",
  }
}
