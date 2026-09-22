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

interface Tagged<T> {
  identity: string
  value: T
}

interface RatingLevelsRequest {
  controller: AbortController | null
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
  const [answer, setAnswer] = useState<Tagged<RatingLevelsResponse | null> | null>(null)
  const [loading, setLoading] = useState<Tagged<boolean> | null>(null)
  const [error, setError] = useState<Tagged<string | null> | null>(null)
  const inFlight = useRef<RatingLevelsRequest | null>(null)
  const nodeId = node?.id ?? null

  // What the request is about: the columns, once each, in a stable order, so
  // reordering a table's factors does not ask the same question again.
  const askedFor = useMemo(() => {
    const wanted = [...new Set(columns.filter((column) => column.trim() !== ""))].sort()
    return wanted.length > 0 ? JSON.stringify(wanted) : null
  }, [columns])
  const available = cache.availability
  const dataVersion = cache.dataVersion
  const requestIdentity = JSON.stringify({ nodeId, askedFor, activeSource, available, dataVersion })

  useEffect(() => {
    if (!nodeId || !askedFor || available !== "current") {
      return
    }
    const asked = JSON.parse(askedFor) as string[]
    const fence = captureDocumentExecutionFence()
    const request: RatingLevelsRequest = { controller: null }
    const timer = setTimeout(() => {
      const controller = new AbortController()
      request.controller = controller
      inFlight.current = request
      setLoading({ identity: requestIdentity, value: true })
      getRatingLevels({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: nodeId,
        source: activeSource,
        columns: asked,
        signal: controller.signal,
      })
        .then((response) => {
          if (
            controller.signal.aborted ||
            inFlight.current !== request ||
            !isDocumentExecutionFenceCurrent(fence)
          ) return
          setAnswer({ identity: requestIdentity, value: response })
          setError({ identity: requestIdentity, value: null })
        })
        .catch((err: unknown) => {
          if (
            controller.signal.aborted ||
            inFlight.current !== request ||
            !isDocumentExecutionFenceCurrent(fence)
          ) return
          // The editor keeps working from preview levels, and says why it had
          // to: the server's own message rather than the bare "HTTP 422" the
          // client builds as the error's message.
          setAnswer({ identity: requestIdentity, value: null })
          setError({ identity: requestIdentity, value: apiErrorMessage(err, "the levels could not be read") })
        })
        .finally(() => {
          if (inFlight.current !== request) return
          inFlight.current = null
          setLoading({ identity: requestIdentity, value: false })
        })
    }, RATING_LEVELS_DEBOUNCE_MS)
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

  // Whole-dataset levels are offered only while the point they came from is
  // the one this node reads *now*: a stale point falls back to the preview,
  // however recently its levels arrived.
  const matchingAnswer = answer?.identity === requestIdentity ? answer.value : null
  const current =
    available === "current" &&
    matchingAnswer?.status === "ok" &&
    matchingAnswer.data_version === dataVersion
  const levels = useMemo(() => {
    if (!current || !matchingAnswer) return NO_LEVELS
    const byColumn: Record<string, string[]> = {}
    for (const column of matchingAnswer.columns) {
      if (column.values.length > 0) {
        byColumn[column.column] = column.values.map((value) => value.value)
      }
    }
    return byColumn
  }, [current, matchingAnswer])

  return {
    cache,
    levels,
    totalRows: current && matchingAnswer ? matchingAnswer.total_rows : 0,
    loading: loading?.identity === requestIdentity ? loading.value : false,
    error: error?.identity === requestIdentity ? error.value : null,
    basis: current ? "all" : available === "stale" ? "stale" : "sample",
  }
}
